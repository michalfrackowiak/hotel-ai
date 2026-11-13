from datetime import timedelta
from urllib.parse import quote

import google.auth
import requests
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from google.auth.exceptions import DefaultCredentialsError, GoogleAuthError
from google.auth.transport.requests import AuthorizedSession

from .models import Booking, CalendarPublication


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars"
SYNC_MARKER = {"private": {"source": "hotel-ai"}}


class CalendarError(RuntimeError):
    def __init__(self, message, *, insert_rejected=False):
        super().__init__(message)
        self.insert_rejected = insert_rejected


class GoogleCalendarClient:
    def __init__(self, session):
        self.session = session

    @classmethod
    def connect(cls):
        try:
            credentials, _ = google.auth.default(scopes=[CALENDAR_SCOPE])
        except (DefaultCredentialsError, OSError, ValueError) as error:
            raise CalendarError("Google Calendar credentials could not be loaded.") from error
        return cls(AuthorizedSession(credentials, refresh_timeout=10, max_refresh_attempts=1))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.session.close()

    def _request(self, method, url, **kwargs):
        try:
            return self.session.request(method, url, timeout=(5, 15), allow_redirects=False, **kwargs)
        except (GoogleAuthError, requests.RequestException) as error:
            raise CalendarError("Google Calendar could not be reached or authenticated.") from error

    def publish(self, calendar_id, event):
        events_url = f"{CALENDAR_API}/{quote(calendar_id, safe='')}/events"
        response = self._request("POST", events_url, json=event)
        if response.status_code in (200, 201):
            return
        if response.status_code != 409:
            raise CalendarError(
                f"Google Calendar event insert returned HTTP {response.status_code}.",
                insert_rejected=response.status_code in (400, 401, 403, 404, 429),
            )

        # A timeout may have hidden a successful insert. Check the existing event before marking success.
        response = self._request("GET", f"{events_url}/{event['id']}")
        if response.status_code != 200:
            raise CalendarError(f"Google Calendar event verification returned HTTP {response.status_code}.")
        try:
            existing = response.json()
        except ValueError as error:
            raise CalendarError("Google Calendar returned an unreadable event.") from error
        if not _matches_event(existing, event):
            raise CalendarError("The Google Calendar event ID is already in use by a different event.")


def _matches_event(existing, expected):
    if not isinstance(existing, dict) or existing.get("status") == "cancelled":
        return False
    for edge in ("start", "end"):
        boundary = existing.get(edge)
        if not isinstance(boundary, dict) or boundary.get("date") != expected[edge]["date"]:
            return False
    properties = existing.get("extendedProperties")
    return (
        existing.get("id") == expected["id"]
        and existing.get("transparency", "opaque") == "opaque"
        and isinstance(properties, dict)
        and properties.get("private") == SYNC_MARKER["private"]
    )


def _booking_event(booking):
    return {
        "id": booking.reference.hex,
        "summary": "Reserved",
        "start": {"date": booking.check_in.isoformat()},
        "end": {"date": booking.check_out.isoformat()},
        "transparency": "opaque",
        "extendedProperties": SYNC_MARKER,
    }


def due_booking_ids():
    return list(
        Booking.objects.filter(
            check_out__gt=timezone.localdate(),
            calendar_publication__published_at__isnull=True,
            room__google_calendar_id__gt="",
        )
        .filter(Q(calendar_publication__isnull=True) | Q(calendar_publication__next_attempt_at__lte=timezone.now()))
        .order_by("check_in", "pk")
        .values_list("pk", flat=True)
    )


@transaction.atomic
def _claim_publication(booking_id):
    now = timezone.now()
    booking = (
        Booking.objects.select_related("room").filter(pk=booking_id, check_out__gt=timezone.localdate()).first()
    )
    if booking is None or not booking.room.google_calendar_id:
        return None
    publication = CalendarPublication.objects.filter(booking=booking).first()
    if publication is None:
        publication = CalendarPublication(booking=booking)
    elif publication.published_at or publication.next_attempt_at > now:
        return None

    event_may_exist = publication.event_may_exist
    if not event_may_exist:
        publication.calendar_id = booking.room.google_calendar_id

    # Persist the destination before sending so a crash or lost response cannot create an event on a second calendar.
    publication.event_may_exist = True
    publication.attempts += 1
    delay = min(120 * 2 ** min(publication.attempts - 1, 5), 3600)
    publication.next_attempt_at = now + timedelta(seconds=delay)
    publication.save()
    publication.booking = booking
    return publication, event_may_exist


def publish_due_bookings(client, booking_ids):
    published, failed = 0, 0
    for booking_id in booking_ids:
        claim = _claim_publication(booking_id)
        if claim is None:
            continue
        publication, event_may_exist = claim

        pending = CalendarPublication.objects.filter(pk=publication.pk, published_at__isnull=True)
        try:
            client.publish(publication.calendar_id, _booking_event(publication.booking))
        except CalendarError as error:
            pending.filter(attempts=publication.attempts).update(
                last_error=str(error),
                event_may_exist=event_may_exist or not error.insert_rejected,
            )
            failed += 1
        else:
            published += pending.update(published_at=timezone.now(), last_error="")

    return published, failed
