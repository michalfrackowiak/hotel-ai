import base64
import hashlib
import hmac
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking
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
        action = urlsplit(previous.find("Gather").attrib["action"])
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
                self.assertEqual(gather.attrib["language"], "pl-PL")
                self.assertEqual(gather.attrib["numDigits"], "1")
                self.assertEqual(gather.attrib["actionOnEmptyResult"], "true")
                self.assertEqual(
                    [say.attrib["language"] for say in gather.findall("Say")], ["pl-PL", "en-US", "pl-PL"],
                )
                self.assertIn("Resort Neptun Jastrzębia Góra", gather.findtext("Say"))

                welcome = self.turn(menu, call_sid, Digits=digit)
                gather = welcome.find("Gather")
                self.assertEqual(gather.attrib["input"], "speech")
                self.assertEqual(gather.attrib["timeout"], "10")
                self.assertEqual(gather.attrib["language"], locale)
                self.assertEqual(gather.attrib["speechModel"], "experimental_utterances")
                self.assertEqual(gather.attrib["speechTimeout"], "2")
                self.assertEqual(gather.find("Say").attrib["voice"], voice)
                self.assertIn("stay on the line" if digit else "pozostać na linii", gather.findtext("Say"))

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
