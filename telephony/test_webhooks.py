import base64
import hashlib
import hmac
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookings.confirmations import dispatch_confirmations
from bookings.models import Booking, BookingConfirmation
from config.phone_wsgi import application as phone_application


ORIGIN = "https://reception.example.test"
AUTH_TOKEN = "fixture-token"
CALL_SID = "CA" + "a" * 32
RECEPTION_NUMBER = "+12025550142"
FORM_TYPE = "application/x-www-form-urlencoded"


def _signature(url, pairs):
    # Build the fixture independently of Twilio's Python validator.
    signed = url + "".join(key + value for key, value in sorted(set(pairs)))
    digest = hmac.new(AUTH_TOKEN.encode(), signed.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


@override_settings(
    HOTEL_TELEPHONY_PUBLIC_BASE_URL=ORIGIN, HOTEL_TWILIO_AUTH_TOKEN=AUTH_TOKEN,
    HOTEL_PHONE_RECEPTION_NUMBER="",
)
class PhoneWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def signed_post(self, path, fields, *, signed_url=None, signature=None):
        pairs = list(fields.items()) if isinstance(fields, dict) else fields
        if signature is None:
            signature = _signature(signed_url or ORIGIN + path, pairs)
        return self.client.post(
            path, urlencode(pairs), content_type=FORM_TYPE, headers={"X-Twilio-Signature": signature},
        )

    def start(self, call_sid=CALL_SID):
        response = self.signed_post(reverse("telephony:incoming"), {"CallSid": call_sid})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")
        self.assertIn("no-store", response["Cache-Control"])
        return ElementTree.fromstring(response.content)

    def turn(self, previous, call_sid=CALL_SID, **fields):
        gathers = previous.findall("Gather")
        gather = (gathers[0] if any(fields.values()) else gathers[-1]) if gathers else None
        action = urlsplit(previous.findtext("Redirect") if gather is None else gather.attrib["action"])
        self.assertEqual((action.scheme, action.netloc, action.query), ("https", "reception.example.test", ""))
        response = self.signed_post(action.path, {"CallSid": call_sid, **fields})
        self.assertEqual(response.status_code, 200)
        return ElementTree.fromstring(response.content)

    def test_webhooks_are_disabled_without_credentials(self):
        with self.settings(HOTEL_TWILIO_AUTH_TOKEN=""):
            response = self.signed_post(reverse("telephony:incoming"), {"CallSid": CALL_SID})
            self.assertEqual(response.status_code, 404)

    def test_webhooks_are_disabled_for_unsafe_public_origins(self):
        path = reverse("telephony:incoming")
        for invalid_origin in (
            "", "http://reception.example.test", ORIGIN + "/subpath", ORIGIN + "?query=value", ORIGIN + "#",
            "https://user@reception.example.test", "https://invalid_host.example.test", ORIGIN + ":noport",
            ORIGIN + ":65536", ORIGIN + ":0", ORIGIN + ":", ORIGIN + "\n",
        ):
            with self.subTest(origin=invalid_origin), self.settings(HOTEL_TELEPHONY_PUBLIC_BASE_URL=invalid_origin):
                self.assertEqual(self.signed_post(path, {"CallSid": CALL_SID}).status_code, 404)

    def test_signatures_bind_to_the_configured_url_and_form_parameters(self):
        path = reverse("telephony:incoming")
        fields = {"CallSid": CALL_SID}
        self.assertEqual(self.signed_post(path, fields, signature="").status_code, 403)
        self.assertEqual(self.signed_post(path, fields, signed_url="http://testserver" + path).status_code, 403)
        self.assertEqual(self.signed_post(path + "?unexpected=yes", fields).status_code, 403)
        self.assertEqual(self.client.get(path).status_code, 405)
        tampered = self.signed_post(path, {"CallSid": "CA" + "b" * 32}, signature=_signature(
            ORIGIN + path, list(fields.items()),
        ))
        self.assertEqual(tampered.status_code, 403)
        signed = self.client.post(
            path, urlencode(fields), content_type=FORM_TYPE,
            headers={"X-Twilio-Signature": _signature(ORIGIN + path, list(fields.items())),
                     "X-Forwarded-Host": "wrong.example.test", "X-Forwarded-Proto": "http"},
        )
        self.assertEqual(signed.status_code, 200)
        self.assertContains(signed, ORIGIN + "/telephony/turn/")

    def test_form_is_bounded_and_call_fields_are_unambiguous(self):
        path = reverse("telephony:incoming")
        self.assertEqual(self.signed_post(path, {"CallSid": "bad"}).status_code, 400)
        self.assertEqual(self.signed_post(path, [("CallSid", CALL_SID)] * 2).status_code, 400)
        self.assertEqual(self.signed_post(path, {"CallSid": CALL_SID, "extra": "x" * 17000}).status_code, 413)
        unsupported = self.client.post(path, "{}", content_type="application/json", headers={"X-Twilio-Signature": "x"})
        self.assertEqual(unsupported.status_code, 415)

    def test_duplicate_and_out_of_order_turns_do_not_advance_the_conversation(self):
        menu = self.start()
        welcome = self.turn(menu, Digits="2")
        replay = self.turn(menu, Digits="1")
        self.assertEqual(ElementTree.tostring(replay), ElementTree.tostring(welcome))
        fake_path = reverse("telephony:turn", kwargs={"revision": "1" * 32})
        signed_for_other_path = self.signed_post(fake_path, {"CallSid": CALL_SID}, signed_url=ORIGIN + reverse("telephony:incoming"))
        self.assertEqual(signed_for_other_path.status_code, 403)
        self.assertEqual(Booking.objects.count(), 0)

    def test_arrival_prompts_separate_greeting_and_two_listening_windows(self):
        for digit, locale, voice, prompts in (
            ("2", "en-US", "Polly.Joanna", (
                "For availability, prices, booking. Specify the dates of your stay",
                "If you are calling regarding another matter, please stay on the line and I will connect "
                "you with a receptionist. For availability, prices, booking - Specify the dates of your stay",
            )),
            ("", "pl-PL", "Polly.Ewa", (
                "Dostępność, ceny, rezerwacje. Proszę podaj planowany termin przyjazdu.",
                "Jeżeli dzwonisz w innej sprawie, pozostań na linii. Dostępność, ceny, rezerwacje. "
                "Proszę podaj planowany termin przyjazdu.",
            )),
        ):
            with self.subTest(locale=locale):
                call_sid = "CA" + (digit or "3") * 32
                arrival = self.turn(self.start(call_sid), call_sid, Digits=digit)
                self.assertEqual(
                    [element.tag for element in arrival],
                    ["Say", "Pause", "Say", "Gather", "Say", "Gather"] if digit else ["Say", "Gather", "Say", "Gather"],
                )
                if digit:
                    self.assertEqual(arrival.findtext("Say"), "Hi, this is Neptun Resort, Pucka 2 street")
                    self.assertEqual(arrival.find("Pause").attrib["length"], "2")

                first, last = arrival.findall("Gather")
                self.assertEqual(first.attrib["action"], last.attrib["action"])
                self.assertEqual([say.text for say in arrival.findall("Say")[-2:]], list(prompts))
                for say in arrival.findall("Say"):
                    self.assertEqual(say.attrib, {"language": locale, "voice": voice})
                for gather, on_empty in zip((first, last), ("false", "true"), strict=True):
                    self.assertEqual(gather.attrib["timeout"], "5")
                    self.assertEqual(gather.attrib["actionOnEmptyResult"], on_empty)
                    self.assertEqual(gather.attrib["input"], "speech")
                    self.assertEqual(gather.attrib["language"], locale)

                departure = self.turn(arrival, call_sid, SpeechResult="tomorrow" if digit else "jutro")
                self.assertEqual(len(departure.findall("Gather")), 1)
                self.assertIn("departure date" if digit else "datę wyjazdu", departure.findtext("Gather/Say"))
                self.assertEqual(ElementTree.tostring(self.turn(arrival, call_sid)), ElementTree.tostring(departure))

    def test_arrival_retry_preserves_the_unavailable_stay_message(self):
        arrival = self.turn(self.start(), Digits="2")
        departure = self.turn(arrival, SpeechResult="Tomorrow")
        guests = self.turn(departure, SpeechResult="For two nights")
        retry = self.turn(guests, SpeechResult="9")
        self.assertEqual([element.tag for element in retry], ["Gather"])
        self.assertIn("No room is available", retry.findtext("Gather/Say"))
        self.assertFalse(Booking.objects.exists())

    def test_signed_english_and_polish_calls_complete_only_after_explicit_confirmation(self):
        today = timezone.localdate()
        for digit, locale, voice, method, contact, confirmation in (
            ("2", "en-US", "Polly.Joanna", "Email", "en at example dot test", "Confirm."),
            ("", "pl-PL", "Polly.Ewa", "SMS", "+48 500 000 123", "Potwierdzam."),
        ):
            with self.subTest(locale=locale):
                call_sid = "CA" + (digit or "3") * 32
                menu = self.start(call_sid)
                gather = menu.find("Gather")
                self.assertEqual(gather.attrib["input"], "dtmf speech")
                self.assertEqual(gather.attrib["timeout"], "2")
                self.assertEqual(gather.attrib["language"], "en-US")
                self.assertEqual(gather.attrib["numDigits"], "1")
                self.assertEqual(gather.attrib["actionOnEmptyResult"], "true")
                self.assertEqual(
                    [say.attrib["language"] for say in gather.findall("Say")], ["pl-PL", "en-US", "pl-PL"],
                )
                self.assertEqual(" ".join(say.text for say in gather.findall("Say")), (
                    "Dzień dobry, Resort Neptun Jastrzębia Góra. For English press 2 or say 'English'. "
                    "Język polski – pozostań na linii lub napisz wiadomość."
                ))

                welcome = self.turn(menu, call_sid, Digits=digit)
                gather = welcome.find("Gather")
                self.assertEqual(gather.attrib["input"], "speech")
                self.assertEqual(gather.attrib["timeout"], "5")
                self.assertEqual(gather.attrib["language"], locale)
                self.assertEqual(gather.attrib["speechModel"], "experimental_utterances")
                self.assertEqual(gather.attrib["speechTimeout"], "2")
                self.assertEqual(welcome.find("Say").attrib["voice"], voice)

                start_date = today + timedelta(days=int(digit or "3") * 5)
                flow = welcome
                for speech in (str(start_date), str(start_date + timedelta(days=2)), "2"):
                    flow = self.turn(flow, call_sid, SpeechResult=speech)
                self.assertEqual(flow.find("Gather").attrib["input"], "dtmf speech")
                self.assertIn("240", flow.findtext("Gather/Say"))
                self.assertIn("400", flow.findtext("Gather/Say"))
                quote = self.turn(flow, call_sid, Digits="2")
                self.assertIn("ROOM-6", quote.findtext("Gather/Say"))
                self.assertFalse(Booking.objects.filter(check_in=start_date).exists())

                uncertain = self.turn(quote, call_sid, SpeechResult="Maybe" if digit else "Może")
                self.assertIsNotNone(uncertain.find("Gather"))
                self.assertFalse(Booking.objects.filter(check_in=start_date).exists())
                delivery = self.turn(uncertain, call_sid, SpeechResult=confirmation)
                collect_contact = self.turn(delivery, call_sid, SpeechResult=method)
                self.assertFalse(Booking.objects.filter(check_in=start_date).exists())
                completed = self.turn(collect_contact, call_sid, SpeechResult=contact)
                booking = Booking.objects.get(check_in=start_date)
                self.assertEqual(booking.room.code, "ROOM-6")
                if method == "Email":
                    self.assertEqual(booking.client.email, "en@example.test")
                else:
                    self.assertEqual(booking.client.phone, "+48500000123")
                self.assertIn(str(booking.reference), completed.findtext("Say"))
                self.assertEqual(completed.find("Say").attrib["language"], locale)
                self.assertIsNotNone(completed.find("Hangup"))
                self.assertIsNone(completed.find("Gather"))
                self.turn(collect_contact, call_sid, SpeechResult=contact)
                self.assertEqual(Booking.objects.filter(check_in=start_date).count(), 1)

    @override_settings(HOTEL_CONFIRMATION_WAIT_METHODS=("sms",))
    @patch("bookings.confirmations._submit", return_value=("fixture-message", True))
    def test_signed_receipt_waits_for_dispatch_and_uses_the_sms_closing(self, submit):
        flow = self.turn(self.start())
        for speech in ("jutro", "na dwie noce", "dwie osoby", "1", "tak", "sms"):
            flow = self.turn(flow, SpeechResult=speech)
        pending = self.turn(flow, SpeechResult="500 000 123")
        self.assertNotIn("Wysłaliśmy SMS", pending.findtext("Say"))
        self.assertEqual(pending.find("Pause").attrib["length"], "2")
        self.assertEqual(pending.find("Redirect").attrib["method"], "POST")
        path = urlsplit(pending.findtext("Redirect")).path
        self.assertEqual(self.signed_post(path, {"CallSid": CALL_SID}, signature="").status_code, 403)
        waiting = self.turn(pending)
        self.assertIsNone(waiting.find("Say"))
        self.assertEqual(waiting.findtext("Redirect"), pending.findtext("Redirect"))
        submit.assert_not_called()

        self.assertEqual(dispatch_confirmations(["sms"]), (1, 0))
        self.assertEqual(dispatch_confirmations(["sms"]), (0, 0))
        completed = self.turn(waiting)
        booking = Booking.objects.get()
        self.assertEqual(booking.confirmation.status, BookingConfirmation.Status.ACCEPTED)
        self.assertIn("Wysłaliśmy SMS z potwierdzeniem.", completed.findtext("Say"))
        self.assertIn(str(booking.reference), completed.findtext("Say"))
        self.assertIsNotNone(completed.find("Hangup"))
        self.assertEqual(
            ElementTree.tostring(completed),
            ElementTree.tostring(self.turn(flow, SpeechResult="500 000 123")),
        )
        self.assertEqual(Booking.objects.count(), 1)
        submit.assert_called_once()

    @override_settings(HOTEL_PHONE_RECEPTION_NUMBER=RECEPTION_NUMBER)
    def test_handover_dials_configured_reception_and_does_not_redial_after_callback(self):
        welcome = self.turn(self.start(), SpeechResult="English.")
        transfer = self.turn(welcome)
        self.assertEqual(transfer.find("Say").attrib["language"], "en-US")
        dial = transfer.find("Dial")
        self.assertEqual(dial.text, RECEPTION_NUMBER)
        action = urlsplit(dial.attrib["action"])
        self.assertEqual((action.scheme, action.netloc, action.query), ("https", "reception.example.test", ""))
        self.assertEqual(dial.attrib["method"], "POST")
        fields = {"CallSid": CALL_SID, "DialCallStatus": "completed"}
        self.assertEqual(self.signed_post(action.path, fields, signature="").status_code, 403)
        self.assertEqual(self.signed_post(action.path, {**fields, "DialCallStatus": "bad"}).status_code, 400)
        self.assertEqual(ElementTree.tostring(self.turn(welcome)), ElementTree.tostring(transfer))

        pairs = list(fields.items())
        request = RequestFactory().post(
            action.path, urlencode(pairs), content_type=FORM_TYPE,
            headers={"X-Twilio-Signature": _signature(ORIGIN + action.path, pairs)},
        )
        statuses = []
        response = phone_application(request.environ, lambda status, headers: statuses.append(status))
        try:
            self.assertEqual(statuses, ["200 OK"])
            self.assertIsNotNone(ElementTree.fromstring(b"".join(response)).find("Hangup"))
        finally:
            if close := getattr(response, "close", None):
                close()
        repeated_callback = self.signed_post(action.path, fields)
        self.assertEqual(repeated_callback.status_code, 200)
        self.assertIsNotNone(ElementTree.fromstring(repeated_callback.content).find("Hangup"))
        replay = self.turn(welcome)
        self.assertIsNotNone(replay.find("Hangup"))
        self.assertIsNone(replay.find("Dial"))
        self.assertFalse(Booking.objects.exists())

    @override_settings(HOTEL_PHONE_RECEPTION_NUMBER=RECEPTION_NUMBER)
    def test_failed_polish_handover_announces_unavailability_and_hangs_up(self):
        welcome = self.turn(self.start())
        transfer = self.turn(welcome, SpeechResult="Chcę porozmawiać z recepcją")
        action = urlsplit(transfer.find("Dial").attrib["action"])
        response = self.signed_post(action.path, {"CallSid": CALL_SID, "DialCallStatus": "no-answer"})
        result = ElementTree.fromstring(response.content)
        self.assertEqual(result.find("Say").attrib["language"], "pl-PL")
        self.assertIn("niedostępna", result.findtext("Say"))
        self.assertIsNotNone(result.find("Hangup"))
        self.assertIsNone(result.find("Dial"))
        self.assertFalse(Booking.objects.exists())

    def test_handover_without_a_routable_number_announces_unavailability(self):
        for phone, sid_char in (("", "a"), ("https://wrong.example.test", "b")):
            with self.subTest(phone=phone), self.settings(HOTEL_PHONE_RECEPTION_NUMBER=phone):
                call_sid = "CA" + sid_char * 32
                welcome = self.turn(self.start(call_sid), call_sid)
                result = self.turn(welcome, call_sid)
                self.assertIn("niedostępna", result.findtext("Say"))
                self.assertIsNotNone(result.find("Hangup"))
                self.assertIsNone(result.find("Dial"))
