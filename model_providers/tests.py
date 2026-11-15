from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .extraction import extract_stay_value
from .models import AgentConfiguration


class ModelProviderTests(TestCase):
    @patch("reception.views.extract_stay_value")
    def test_chat_uses_provider_only_for_unparsed_stay_inputs(self, extract):
        extract.return_value = timezone.localdate()
        endpoint = reverse("reception-message")
        started = self.client.post(endpoint, {"language": "en", "start": True}, content_type="application/json").json()
        input_requiring_provider = "I want to arrive on the next public holiday."
        next_turn = self.client.post(endpoint, {
            "language": "en", "revision": started["revision"], "message": input_requiring_provider,
        }, content_type="application/json").json()
        self.assertEqual(next_turn["stage"], "check_out")
        extract.assert_called_once_with("check_in", input_requiring_provider, "en")
        self.client.post(endpoint, {
            "language": "en", "revision": next_turn["revision"], "message": "tomorrow",
        }, content_type="application/json")
        extract.assert_called_once()

    @patch("model_providers.extraction._post_json")
    def test_demo_and_contact_fields_stay_inside_django(self, post):
        self.assertIsNone(extract_stay_value("guests", "two", "en"))
        AgentConfiguration.objects.create(provider="ollama", model="test-model")
        self.assertIsNone(extract_stay_value("email", "guest@example.com", "en"))
        self.assertIsNone(extract_stay_value("confirm", "yes", "en"))
        post.assert_not_called()

    @override_settings(
        HOTEL_OLLAMA_URL="http://ollama:11434/", HOTEL_OLLAMA_TIMEOUT=45, HOTEL_MODEL_API_KEY="hosted-test-key"
    )
    @patch("model_providers.extraction._post_json")
    def test_selecting_ollama_uses_the_local_endpoint_without_hosted_credentials(self, post):
        AgentConfiguration.objects.create(provider="ollama", model="test-local-model")
        post.return_value = {"message": {"content": '{"value": 2}'}}
        self.assertEqual(extract_stay_value("guests", "dwie osoby", "pl"), 2)
        url, payload = post.call_args.args
        self.assertEqual(url, "http://ollama:11434/api/chat")
        self.assertEqual(payload["model"], "test-local-model")
        self.assertEqual(payload["messages"][-1]["content"], "dwie osoby")
        self.assertEqual(payload["format"]["properties"]["value"]["type"], ["integer", "null"])
        self.assertEqual(post.call_args.kwargs, {"timeout": 45})

    @override_settings(HOTEL_MODEL_API_KEY="hosted-test-key", HOTEL_MODEL_BASE_URL="https://models.example.com/v1/")
    @patch("model_providers.extraction._post_json")
    def test_selecting_hosted_uses_chat_completions_with_bearer_credentials(self, post):
        AgentConfiguration.objects.create(provider="hosted", model="test-hosted-model")
        post.return_value = {"choices": [{"message": {"content": '{"value": "2027-06-17"}'}}]}
        self.assertEqual(extract_stay_value("check_in", "June seventeenth, 2027", "en"), date(2027, 6, 17))
        self.assertEqual(post.call_args.args[0], "https://models.example.com/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs, {"key": "hosted-test-key"})

    @override_settings(HOTEL_MODEL_API_KEY="")
    @patch("model_providers.extraction._post_json")
    def test_missing_key_and_malformed_model_output_do_not_advance_the_chat(self, post):
        config = AgentConfiguration.objects.create(provider="hosted", model="test-model")
        self.assertIsNone(extract_stay_value("guests", "two", "en"))
        post.assert_not_called()
        config.provider = "ollama"
        config.save(update_fields=["provider"])
        post.return_value = {"message": {"content": '{"value": true}'}}
        self.assertIsNone(extract_stay_value("guests", "two", "en"))
        post.side_effect = TimeoutError
        with self.assertLogs("model_providers.extraction", level="WARNING"):
            self.assertIsNone(extract_stay_value("guests", "two", "en"))

    def test_hosted_configuration_requires_model_and_https_url(self):
        config = AgentConfiguration(provider="hosted")
        with self.assertRaises(ValidationError):
            config.full_clean()
        config.model = "test-model"
        with self.settings(HOTEL_MODEL_BASE_URL="http://models.example.com/v1"):
            with self.assertRaises(ValidationError):
                config.full_clean()
        with self.settings(HOTEL_MODEL_BASE_URL="https://models.example.com/v1"):
            config.full_clean()
