from uuid import uuid4

from django.contrib.sessions.models import Session
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Now
from django.utils import timezone

from .models import Conversation


CONVERSATION_SESSION_KEY = "reception_conversation_id"
CLEANUP_BATCH_SIZE = 200


def conversation_id(session):
    # Load the server-side session before inspecting its key. An expired,
    # deleted, or invented cookie cannot use the legacy key as a locator.
    if session.get("reception"):
        return session.get(CONVERSATION_SESSION_KEY) or session.session_key
    return None


def bind_conversation_id(session, locator):
    """Keep the server-side locator in session data through key rotation."""
    if session.get(CONVERSATION_SESSION_KEY) != locator:
        session[CONVERSATION_SESSION_KEY] = locator


def active_conversations(session, locator):
    live_session = Session.objects.filter(pk=session.session_key, expire_date__gt=Now())
    return Conversation.objects.filter(pk=locator).filter(
        Exists(live_session),
        Q(expires_at__gt=Now()) | Q(expires_at__isnull=True, pk=session.session_key),
    )


def preserve_conversation_for_rotation(session):
    locator = conversation_id(session)
    if locator is None:
        return False
    conversations = active_conversations(session, locator)
    draft = conversations.first()
    if draft is None:
        session.pop(CONVERSATION_SESSION_KEY, None)
        return False
    if draft.expires_at is None:
        # The previous Django image may have inserted this legacy draft without an expiry.
        expiry = Session.objects.filter(
            pk=session.session_key, expire_date__gt=Now(),
        ).values_list("expire_date", flat=True).first()
        if expiry is None:
            return False
        conversations.filter(expires_at__isnull=True).update(expires_at=expiry)
    bind_conversation_id(session, locator)
    return True


def open_conversation(session, locator, initial_state, *, allow_create):
    if session.session_key is None:
        if not allow_create:
            return None, False
        session["reception"] = True
        session.create()
    now = timezone.now()
    expiry = Session.objects.filter(
        pk=session.session_key, expire_date__gt=now,
    ).values_list("expire_date", flat=True).first()
    if expiry is None:
        return None, False
    locator = locator or session.session_key
    conversation = Conversation.objects.filter(pk=locator).first()
    if conversation is not None and (
        (conversation.expires_at is None and locator != session.session_key)
        or (conversation.expires_at is not None and conversation.expires_at <= now)
    ):
        # A rotated authentication session can outlive the reception draft.
        locator = uuid4().hex
        conversation = None
    if conversation is None and not allow_create:
        return None, False
    session.setdefault("reception", True)
    if conversation is not None and conversation.expires_at is not None:
        bind_conversation_id(session, locator)
        return conversation, False

    # A NULL expiry can come from a rollback image writing the previous
    # schema. Recover its original session expiry before binding it.
    if conversation is None:
        conversation, created = Conversation.objects.get_or_create(
            pk=locator, defaults={"state": initial_state, "expires_at": expiry},
        )
    else:
        conversation.expires_at = expiry
        conversation.save(update_fields=["expires_at"])
        created = False
    bind_conversation_id(session, locator)
    return conversation, created


def delete_expired_conversations():
    now = timezone.now()
    active_legacy_session = Session.objects.filter(pk=OuterRef("pk"), expire_date__gt=now)
    expired = Conversation.objects.alias(legacy_session_active=Exists(active_legacy_session)).filter(
        Q(expires_at__lte=now) | Q(expires_at__isnull=True, legacy_session_active=False),
    )
    batch = list(expired.order_by("pk").values_list("pk", flat=True)[:CLEANUP_BATCH_SIZE])
    deleted, _ = expired.filter(pk__in=batch).delete()
    return deleted
