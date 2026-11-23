from datetime import timedelta
from time import sleep
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Conversation, StaffHandover
from .session_continuity import CONVERSATION_SESSION_KEY, active_conversations, open_conversation


class SessionContinuityTests(TestCase):
    def post(self, client=None, **payload):
        return (client or self.client).post(
            reverse("reception-message"), {"language": "en", **payload}, content_type="application/json",
        )

    def copy_cookie(self):
        old = Client()
        old.cookies[settings.SESSION_COOKIE_NAME] = self.client.session.session_key
        return old

    @override_settings(HOTEL_ASR_URL="http://speech.invalid", HOTEL_PIPER_URL="http://speech.invalid")
    def test_login_keeps_voice_draft_and_only_new_session_can_continue(self):
        first = self.post(start=True, voice=True).json()
        turn = self.post(revision=first["revision"], message="tomorrow").json()
        self.assertEqual(turn["stage"], "check_out")
        draft = Conversation.objects.get()
        cached_session = self.client.session
        self.assertEqual(cached_session[CONVERSATION_SESSION_KEY], draft.pk)
        old = self.copy_cookie()
        self.client.force_login(get_user_model().objects.create_user(username="fictional-guest"))

        self.assertNotEqual(cached_session.session_key, self.client.session.session_key)
        self.assertFalse(Session.objects.filter(pk=cached_session.session_key).exists())
        self.assertFalse(active_conversations(cached_session, draft.pk).exists())
        self.assertEqual(open_conversation(cached_session, draft.pk, dict, allow_create=True), (None, False))

        self.assertEqual(old.get(reverse("reception-chat")).context["language"], "pl")
        # Use the original stale cookie for each comparison.
        old.cookies[settings.SESSION_COOKIE_NAME] = cached_session.session_key
        self.assertEqual(self.post(old, revision=turn["revision"], message="three nights").status_code, 404)
        old.cookies[settings.SESSION_COOKIE_NAME] = cached_session.session_key
        self.assertEqual(old.post(
            reverse("reception-audio"), {"revision": turn["revision"]}, content_type="application/json",
        ).status_code, 404)
        old.cookies[settings.SESSION_COOKIE_NAME] = cached_session.session_key
        self.assertEqual(old.post(
            reverse("reception-transcript"), b"\x00\x00", content_type="application/octet-stream",
            headers={"X-Reception-Revision": turn["revision"]},
        ).status_code, 404)

        self.assertEqual(self.client.get(reverse("reception-chat")).context["language"], "en")
        self.assertEqual(self.post(start=True, resume=True).json(), turn)
        self.assertEqual(self.post(revision=turn["revision"], message="three nights").json()["stage"], "guests")
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Conversation.objects.get().expires_at, draft.expires_at)
        self.client.logout()
        self.assertEqual(self.post(start=True, resume=True).json()["stage"], "check_in")

    @override_settings(HOTEL_PIPER_URL="http://speech.invalid")
    def test_speech_data_is_discarded_if_draft_expires_during_synthesis(self):
        first = self.post(start=True, voice=True).json()

        def expire(*_):
            Conversation.objects.update(expires_at=timezone.now() + timedelta(milliseconds=20))
            sleep(0.04)
            return b"fixture-audio"

        with patch("reception.views.synthesize", side_effect=expire) as synthesize:
            result = self.client.post(
                reverse("reception-audio"), {"revision": first["revision"]}, content_type="application/json",
            )
        synthesize.assert_called_once()
        self.assertEqual(result.status_code, 409)

    def test_rotation_keeps_staff_thread_and_clears_it_on_expiry(self):
        first = self.post(start=True, voice=True).json()
        handover = self.post(revision=first["revision"], handover=True, voice=True).json()
        self.assertEqual(handover["stage"], "handover")
        thread = StaffHandover.objects.get()
        msg = thread.messages.create(staff=True, text="Fixture reply")
        old = self.copy_cookie()
        session = self.client.session
        session.cycle_key()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        call_command("clearsessions", verbosity=0)
        self.assertEqual(self.post(old, revision=handover["revision"], poll=True, cursor=0).status_code, 404)
        response = self.post(revision=handover["revision"], poll=True, cursor=0)
        self.assertEqual(response.json()["handover_messages"][0]["id"], msg.pk)

        Conversation.objects.filter(pk=thread.conversation_id).update(expires_at=timezone.now())
        self.assertEqual(self.post(revision=handover["revision"], poll=True, cursor=0).status_code, 404)
        reset = self.post(start=True, resume=True).json()
        self.assertEqual(reset["stage"], "check_in")
        call_command("clearsessions", verbosity=0)
        self.assertFalse(StaffHandover.objects.exists())
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertTrue(Session.objects.filter(pk=session.session_key).exists())

    def test_bounded_cleanup_preserves_live_legacy_rows_and_removes_orphans(self):
        live = SessionStore()
        live["reception"] = True
        live.create()
        Conversation.objects.create(pk=live.session_key, state={})
        Conversation.objects.create(pk="first-expired", state={}, expires_at=timezone.now())
        Conversation.objects.create(pk="next-orphaned", state={})

        with patch("reception.session_continuity.CLEANUP_BATCH_SIZE", 1):
            call_command("clearsessions", verbosity=0)
            self.assertEqual(Conversation.objects.count(), 2)
            call_command("clearsessions", verbosity=0)
        self.assertEqual(Conversation.objects.get().pk, live.session_key)


class SessionContinuityMigrationTests(TransactionTestCase):
    previous = [("reception", "0009_scenario_publication")]
    candidate = [("reception", "0010_conversation_session_continuity")]

    def setUp(self):
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(self.candidate))
        executor = MigrationExecutor(connection)
        executor.migrate(self.previous)
        self.legacy = executor.loader.project_state(self.previous).apps

    def new_session(self):
        session = SessionStore()
        session["reception"] = True
        session.set_expiry(timezone.now() + timedelta(days=1))
        session.create()
        client = Client()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return session, client

    def test_migrates_staff_children_and_can_read_old_image_writes(self):
        old_conversations = self.legacy.get_model("reception", "Conversation").objects
        old_handovers = self.legacy.get_model("reception", "StaffHandover").objects
        old_messages = self.legacy.get_model("reception", "HandoverMessage").objects
        session, client = self.new_session()
        initial = {"stage": "handover", "language": "en", "flow": "neptun", "revision": "a" * 32,
                   "reply": "Fixture reception"}
        draft = old_conversations.create(session_id=session.session_key, state=initial)
        handover = old_handovers.create(conversation_id=draft.pk, language="en")
        message = old_messages.create(handover_id=handover.pk, text="Fixture guest request")
        draft.state["handover_id"] = handover.pk
        draft.save(update_fields=["state"])

        MigrationExecutor(connection).migrate(self.candidate)
        expiry = Session.objects.get(pk=session.session_key).expire_date
        self.assertEqual(Conversation.objects.get(pk=draft.pk).expires_at, expiry)
        self.assertEqual(old_conversations.get(pk=draft.pk).state, draft.state)
        resumed = client.post(
            reverse("reception-message"), {"language": "en", "start": True, "resume": True},
            content_type="application/json",
        )
        self.assertEqual(resumed.json()["handover_messages"][0]["id"], message.pk)
        self.assertEqual(client.session[CONVERSATION_SESSION_KEY], draft.pk)

        # The previous code inserts without the new expiry field and keeps using session_id.
        rollback_session, rollback_client = self.new_session()
        rollback_state = {"stage": "check_out", "language": "en", "flow": "neptun", "revision": "b" * 32,
                          "reply": "Fixture checkout question", "check_in": "2027-01-02"}
        old_conversations.create(session_id=rollback_session.session_key, state=rollback_state)
        self.assertIsNone(Conversation.objects.get(pk=rollback_session.session_key).expires_at)
        call_command("clearsessions", verbosity=0)
        rollback_resumed = rollback_client.post(
            reverse("reception-message"), {"language": "en", "start": True, "resume": True},
            content_type="application/json",
        )
        self.assertEqual(rollback_resumed.json()["revision"], rollback_state["revision"])
        self.assertEqual(
            Conversation.objects.get(pk=rollback_session.session_key).expires_at,
            Session.objects.get(pk=rollback_session.session_key).expire_date,
        )
