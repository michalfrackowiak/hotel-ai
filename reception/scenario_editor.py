import re
from collections import Counter
from string import Formatter

from django import forms
from django.core.exceptions import ValidationError

from .models import ScenarioPrompt


MAX_VOICE_PARTS = 6
MAX_VOICE_TEXT = 1000
MAX_PAUSE_SECONDS = 30
MAX_TOTAL_PAUSE_SECONDS = 60
ARRIVAL_KEYS = ("check_in", "web_check_in")
VOICE_FIELDS = ("text", "seconds", "listen")
INVALID_XML_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _fields(texts):
    names = Counter()
    for text in texts:
        if INVALID_XML_CHARACTERS.search(text):
            raise ValueError("unsupported control character")
        for _, field, spec, conversion in Formatter().parse(text):
            if field is not None:
                if not field.isidentifier() or spec or conversion is not None:
                    raise ValueError("unsupported format expression")
                names[field] += 1
    return names


def _template_error(texts, allowed):
    try:
        fields = _fields(texts)
    except ValueError:
        return "Use plain text and simple placeholders such as {check_in}. Use doubled braces for literal braces."
    if unknown := set(fields) - set(allowed):
        return "Unknown placeholders: " + ", ".join(f"{{{name}}}" for name in sorted(unknown))
    if missing := set(allowed) - set(fields):
        return "Keep these placeholders in the prompt: " + ", ".join(f"{{{name}}}" for name in sorted(missing))
    if duplicates := [name for name, count in fields.items() if count > 1]:
        return "Use these placeholders only once: " + ", ".join(f"{{{name}}}" for name in sorted(duplicates))
    return None


def _voice_error(parts, key, allowed):
    if not isinstance(parts, list) or len(parts) > MAX_VOICE_PARTS:
        return f"Enter at most {MAX_VOICE_PARTS} voice segments."
    if not parts:
        return None
    total_pause = 0
    for number, part in enumerate(parts, 1):
        if not isinstance(part, dict) or set(part) != set(VOICE_FIELDS):
            return f"Voice segment {number} is invalid."
        text, seconds, listen = (part[field] for field in VOICE_FIELDS)
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_VOICE_TEXT:
            return f"Enter spoken text of up to {MAX_VOICE_TEXT} characters in segment {number}."
        if type(seconds) is not int or not 0 <= seconds <= MAX_PAUSE_SECONDS:
            return f"Pause {number} must be a whole number from 0 to {MAX_PAUSE_SECONDS} seconds."
        if type(listen) is not bool or (listen and (key not in ARRIVAL_KEYS or seconds == 0)):
            return "Listening is available for arrival prompts with a positive pause."
        total_pause += seconds
    if total_pause > MAX_TOTAL_PAUSE_SECONDS:
        return f"Voice pauses may total at most {MAX_TOTAL_PAUSE_SECONDS} seconds."
    if key in ARRIVAL_KEYS and not parts[-1]["listen"]:
        return "The last arrival segment must give the guest time to answer. Select Listen and enter a positive pause."
    if key == "option" and (len(parts) != 1 or total_pause):
        return "Room option text is spoken inside the room list. Use one segment with no pause."
    return _template_error((part["text"] for part in parts), allowed)


def validate_prompt(language, key, chat_text, voice_parts):
    from .scenario import default_chat_text, default_voice_parts, scenario_keys

    if language not in ("en", "pl") or key not in scenario_keys(language):
        return {"key": "This prompt is unavailable in the selected language."}
    errors = {}
    if chat_text and (error := _template_error([chat_text], _fields([default_chat_text(language, key)]))):
        errors["chat_text"] = error
    voice_fields = _fields(part["text"] for part in default_voice_parts(language, key))
    if error := _voice_error(voice_parts, key, voice_fields):
        errors["voice_parts"] = error
    return errors


class VoicePartsWidget(forms.Widget):
    template_name = "admin/reception/scenarioprompt/voice_parts.html"

    def __init__(self):
        super().__init__()
        self.defaults = []
        self.allow_listening = False
        self.max_parts = MAX_VOICE_PARTS

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        rows = value or self.defaults
        rows = list(rows[:self.max_parts]) + [{}] * (self.max_parts - len(rows))
        context["widget"].update(
            rows=rows, allow_listening=self.allow_listening, max_pause=MAX_PAUSE_SECONDS, max_text=MAX_VOICE_TEXT,
        )
        return context

    def value_from_datadict(self, data, files, name):
        return [
            {
                "text": data.get(f"{name}_{number}_text", ""),
                "seconds": data.get(f"{name}_{number}_seconds", ""),
                "listen": data.get(f"{name}_{number}_listen", "") == "on",
            }
            for number in range(1, self.max_parts + 1)
        ]

    def value_omitted_from_data(self, data, files, name):
        # This field uses numbered inputs. All blank inputs explicitly clear the script.
        return False


class VoicePartsField(forms.Field):
    widget = VoicePartsWidget

    def to_python(self, value):
        parts = []
        for number, row in enumerate(value or [], 1):
            text = row["text"].strip()
            seconds = str(row["seconds"]).strip()
            listen = row["listen"]
            if not text and seconds in ("", "0") and not listen:
                continue
            if not text:
                raise ValidationError(f"Enter text in voice segment {number}, or clear the segment.")
            if seconds and (len(seconds) > 2 or not seconds.isdecimal()):
                raise ValidationError(f"Pause {number} must be a whole number from 0 to {MAX_PAUSE_SECONDS} seconds.")
            parts.append({"text": text, "seconds": int(seconds or "0"), "listen": listen})
        return parts


class ScenarioPromptForm(forms.ModelForm):
    voice_parts = VoicePartsField(
        required=False, label="Voice segments",
        help_text="Played from top to bottom. Fill the next empty row to add a segment. Clear its text and pause to remove it.",
    )
    reset_voice = forms.BooleanField(required=False, label="Restore built-in voice script")

    class Meta:
        model = ScenarioPrompt
        fields = ("chat_text", "voice_parts")
        widgets = {"chat_text": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        from .scenario import default_chat_text, default_voice_parts

        super().__init__(*args, **kwargs)
        language, key = self.instance.language, self.instance.key
        self.default_chat = default_chat_text(language, key)
        self.default_voice = [
            {field: part[field] for field in VOICE_FIELDS} for part in default_voice_parts(language, key)
        ]
        if chat := self.fields.get("chat_text"):
            self.initial["chat_text"] = self.instance.chat_text or self.default_chat
            chat_fields = _fields([self.default_chat])
            chat.help_text = "Clear the text to restore the built-in wording." + _placeholder_help(chat_fields)
        if voice := self.fields.get("voice_parts"):
            voice_fields = _fields(part["text"] for part in self.default_voice)
            voice.help_text += _placeholder_help(voice_fields)
            voice.widget.defaults = self.default_voice
            voice.widget.allow_listening = key in ARRIVAL_KEYS
            if key == "option":
                voice.widget.max_parts = 1
                voice.help_text = "Spoken within the room list. Set the pause to zero." + _placeholder_help(voice_fields)

    def clean_chat_text(self):
        text = self.cleaned_data["chat_text"]
        return "" if text == self.default_chat else text

    def clean(self):
        data = super().clean()
        if data.get("reset_voice"):
            self.errors.pop("voice_parts", None)
            data["voice_parts"] = []
        elif data.get("voice_parts") == self.default_voice:
            data["voice_parts"] = []
        return data


def _placeholder_help(names):
    if not names:
        return ""
    return " Keep these placeholders: " + ", ".join(f"{{{name}}}" for name in sorted(names)) + "."
