from datetime import timedelta
from decimal import Decimal
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, BookingConfirmation, Client, Room
from reception.models import Conversation, StaffHandover


class ReceptionStaffGroupTests(TestCase):
    def setup_group(self):
        call_command("setup_reception_staff_group", stdout=StringIO())
        return Group.objects.get(name="Reception staff")

    def test_setup_is_idempotent_and_leaves_membership_to_the_operator(self):
        user = get_user_model().objects.create_user(username="staff-fixture")
        group = self.setup_group()
        self.assertFalse(group.user_set.exists())
        group.user_set.add(user)
        self.assertEqual(group.pk, self.setup_group().pk)
        self.assertEqual(list(group.user_set.all()), [user])
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.user_permissions.exists())
        self.assertEqual(group.permissions.count(), 5)

    def test_setup_refuses_unexpected_existing_permissions(self):
        group = Group.objects.create(name="Reception staff")
        unexpected = Permission.objects.get(content_type__app_label="auth", codename="change_user")
        group.permissions.add(unexpected)
        with self.assertRaisesMessage(CommandError, "has unexpected permissions"):
            self.setup_group()
        self.assertEqual(list(group.permissions.all()), [unexpected])

    def test_setup_refuses_to_expand_a_populated_group(self):
        group = Group.objects.create(name="Reception staff")
        group.user_set.create(username="existing-fixture")
        with self.assertRaisesMessage(CommandError, "has members and is missing permissions"):
            self.setup_group()
        self.assertFalse(group.permissions.exists())

    def test_role_allows_handover_work_and_read_only_booking_access(self):
        user = get_user_model().objects.create_user(username="reception-fixture", is_staff=True)
        user.groups.add(self.setup_group())
        self.client.force_login(user)
        handover = StaffHandover.objects.create(
            conversation=Conversation.objects.create(conversation_id="role-fixture", state={}),
            language="en",
        )
        message = handover.messages.create(text="Fictional guest question")
        handover_detail = reverse("admin:reception_staffhandover_change", args=[handover.pk])
        handover_page = self.client.get(handover_detail)
        self.assertContains(handover_page, message.text)
        self.assertEqual(self.client.post(
            reverse("admin:reception_staffhandover_mark_read", args=[handover.pk]),
            {"snapshot": handover_page.context["read_snapshot"]},
        ).status_code, 302)
        handover.refresh_from_db()
        self.assertEqual(handover.last_read_guest_message_id, message.pk)
        reply = "Fictional staff reply"
        self.assertRedirects(self.client.post(handover_detail, {
            "messages-TOTAL_FORMS": "1", "messages-INITIAL_FORMS": "1", "messages-0-id": message.pk,
            "reply": reply, "assignee": user.pk,
        }), reverse("admin:reception_staffhandover_changelist"))
        self.assertTrue(handover.messages.filter(staff=True, text=reply).exists())
        handover.refresh_from_db()
        self.assertEqual(handover.assignee_id, user.pk)

        arrival = timezone.localdate() + timedelta(days=7)
        booking = Booking.objects.create(
            room=Room.objects.create(code="ROLE-FIXTURE", capacity=2),
            client=Client.objects.create(name="Fictional Guest", email="role@example.test"),
            check_in=arrival, check_out=arrival + timedelta(days=1),
            guests=2, nightly_rate=Decimal("120.00"), idempotency_key=uuid4(),
        )
        confirmation = BookingConfirmation.objects.create(
            booking=booking, method="email", language="en", destination="role@example.test",
        )
        for model, record in (("booking", booking), ("bookingconfirmation", confirmation)):
            with self.subTest(model=model):
                self.assertEqual(self.client.get(reverse(f"admin:bookings_{model}_changelist")).status_code, 200)
                detail = reverse(f"admin:bookings_{model}_change", args=[record.pk])
                self.assertEqual(self.client.get(detail).status_code, 200)
                self.assertEqual(self.client.post(detail, {}).status_code, 403)

        for url_name in (
            "admin:auth_user_changelist", "admin:bookings_client_changelist", "admin:bookings_room_changelist",
            "admin:reception_parsersample_changelist", "admin:reception_scenarioversion_changelist",
            "admin:model_providers_speechselection_changelist",
        ):
            with self.subTest(url_name=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 403)
