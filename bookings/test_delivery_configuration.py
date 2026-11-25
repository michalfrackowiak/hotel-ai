from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings


@override_settings(
    EMAIL_HOST="",
    DEFAULT_FROM_EMAIL="",
    HOTEL_TWILIO_ACCOUNT_SID="",
    HOTEL_TWILIO_AUTH_TOKEN="",
    HOTEL_TWILIO_SMS_FROM="",
    HOTEL_PHONE_RECEPTION_NUMBER="",
)
class DeliveryConfigurationCommandTests(SimpleTestCase):
    def report(self, *args):
        stdout = StringIO()
        call_command("check_delivery_configuration", *args, stdout=stdout)
        return stdout.getvalue()

    def test_unconfigured_process_reports_all_settings_missing(self):
        output = self.report()
        for label in ("SMTP sender", "SMS sender", "Staff transfer destination"):
            self.assertIn(f"{label}: Not configured", output)

    @override_settings(
        EMAIL_HOST="smtp.example.test",
        DEFAULT_FROM_EMAIL="sender@example.test",
        HOTEL_TWILIO_ACCOUNT_SID="fictional-account",
        HOTEL_TWILIO_AUTH_TOKEN="fictional-token",
        HOTEL_TWILIO_SMS_FROM="+12025550120",
        HOTEL_PHONE_RECEPTION_NUMBER="+12025550121",
    )
    def test_configured_process_reports_status_without_settings_values(self):
        output = self.report()
        for label in ("SMTP sender", "SMS sender", "Staff transfer destination"):
            self.assertIn(f"{label}: Configured", output)
        for value in (
            "smtp.example.test", "sender@example.test", "fictional-account", "fictional-token",
            "+12025550120", "+12025550121",
        ):
            self.assertNotIn(value, output)

    @override_settings(
        EMAIL_HOST="smtp.example.test",
        DEFAULT_FROM_EMAIL="invalid sender",
        HOTEL_TWILIO_AUTH_TOKEN="fictional-webhook-token",
        HOTEL_PHONE_RECEPTION_NUMBER="12025550121",
    )
    def test_invalid_values_and_webhook_token_alone_do_not_configure_delivery(self):
        output = self.report()
        for label in ("SMTP sender", "SMS sender", "Staff transfer destination"):
            self.assertIn(f"{label}: Not configured", output)

    @override_settings(
        EMAIL_HOST="smtp.example.test",
        DEFAULT_FROM_EMAIL="sender@example.test",
        HOTEL_TWILIO_ACCOUNT_SID="fictional-account",
        HOTEL_TWILIO_AUTH_TOKEN="fictional-token",
        HOTEL_TWILIO_SMS_FROM="+12025550120",
    )
    def test_confirmation_worker_can_require_both_senders_without_transfer(self):
        output = self.report("--require", "email", "--require", "sms")
        self.assertIn("SMTP sender: Configured", output)
        self.assertIn("SMS sender: Configured", output)
        self.assertIn("Staff transfer destination: Not configured", output)

    @override_settings(HOTEL_PHONE_RECEPTION_NUMBER="+12025550121")
    def test_phone_can_require_transfer_without_senders(self):
        output = self.report("--require", "transfer")
        self.assertIn("SMTP sender: Not configured", output)
        self.assertIn("SMS sender: Not configured", output)
        self.assertIn("Staff transfer destination: Configured", output)
        self.assertNotIn("+12025550121", output)

    def test_missing_requirements_fail_after_report_without_duplicate_labels(self):
        stdout = StringIO()
        with self.assertRaisesMessage(CommandError, "Required delivery configuration missing: SMTP sender, SMS sender."):
            call_command(
                "check_delivery_configuration", "--require", "email", "--require", "sms", "--require", "sms",
                stdout=stdout,
            )
        for label in ("SMTP sender", "SMS sender", "Staff transfer destination"):
            self.assertIn(f"{label}: Not configured", stdout.getvalue())

    def test_invalid_required_channel_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "invalid choice"):
            self.report("--require", "fax")
