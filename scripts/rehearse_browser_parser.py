"""Run the fictional parser rehearsal against the hosted Checks Compose fixture."""

from contextlib import closing
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import wave

from playwright.sync_api import expect, sync_playwright


ORIGIN = "http://127.0.0.1:8082"
COMPOSE = ("docker", "compose")
FIXTURES = {
    "en": ("tomorrow", "for a week", "two guess", "two guests", "Guests"),
    "pl": ("jutro", "na tydzień", "załoga bez liczby", "dwie osoby", "Goście"),
}
EXPECTED_COUNT = 2


def compose(*args):
    return subprocess.run(
        (*COMPOSE, *args), check=True, capture_output=True, timeout=45,
    ).stdout


def synthesize(language, phrase, path):
    payload = json.dumps({"language": language, "text": phrase}, ensure_ascii=True)
    code = (
        "from urllib.request import Request, urlopen; import sys; "
        "request = Request('http://piper:5000/synthesize', "
        f"data={payload.encode()!r}, headers={{'Content-Type': 'application/json'}}); "
        "sys.stdout.buffer.write(urlopen(request, timeout=15).read())"
    )
    audio = compose("exec", "-T", "web", "python", "-c", code)
    with wave.open(io.BytesIO(audio), "rb") as source, wave.open(str(path), "wb") as output:
        output.setparams(source.getparams())
        frame = b"\0" * source.getnchannels() * source.getsampwidth()
        # The fake device clock can run while the prompt plays.
        output.writeframes(frame * (7 * source.getframerate()))
        output.writeframes(source.readframes(source.getnframes()))
        output.writeframes(frame * (2 * source.getframerate()))


def manage(code):
    return compose("exec", "-T", "web", "python", "manage.py", "shell", "-c", code)


def change_rule(language, source, phrase, active):
    manage(
        "from reception.models import ParserSample; "
        f"samples = ParserSample.objects.filter(language={language!r}, source={source!r}, "
        f"stage='guests', text={ascii(phrase)}, succeeded=False, parsed_result=''); "
        "sample = samples.order_by('pk').first(); "
        "assert sample is not None, 'Expected missing parser sample'; "
        f"sample.corrected_result = '{EXPECTED_COUNT}'; sample.correction_enabled = {active!r}; "
        "sample.save(update_fields=['corrected_result', 'correction_enabled'])"
    )


def event_count(language, source):
    event = f"parser_correction_applied language={language} stage=guests source={source} original_result=missing"
    return compose("logs", "--no-color", "web").decode().count(event)


def post_to(response, path):
    return response.url == f"{ORIGIN}{path}" and response.request.method == "POST"


def submit(page, phrase=None):
    message = page.locator("#chat-message")
    if phrase is not None:
        message.fill(phrase)
    submitted = message.input_value()
    with page.expect_response(lambda response: (
        post_to(response, "/reception/message/")
        and response.request.post_data_json.get("message") == submitted
    )) as turn:
        page.locator("#chat-send").click()
    response = turn.value
    assert response.ok, f"Booking turn failed ({response.status})"
    return response.json(), response.request.post_data_json


def reach_guest_prompt(page, language, channel):
    arrival, departure, *_ = FIXTURES[language]
    page.goto(f"{ORIGIN}/{channel}/?lang={language}")
    assert submit(page, arrival)[0]["stage"] == "check_out"
    assert submit(page, departure)[0]["stage"] == "guests"


def assert_result(page, result, language, corrected):
    stage = "room" if corrected else "guests"
    assert result["stage"] == stage, f"Expected {stage}, got {result['stage']}"
    if corrected:
        assert result["booking_summary"]["guests"] == EXPECTED_COUNT
        label = FIXTURES[language][-1]
        summary = page.locator("#booking-summary-values")
        row = summary.locator("div").filter(has=page.get_by_text(label, exact=True))
        expect(row.locator("dd")).to_have_text(str(EXPECTED_COUNT))
    else:
        assert "guests" not in result["booking_summary"]


def chat_case(browser, language, phrase, corrected):
    with closing(browser.new_context()) as context:
        page = context.new_page()
        reach_guest_prompt(page, language, "chat")
        result, request = submit(page, phrase)
        assert not request.get("transcript")
        assert_result(page, result, language, corrected)


def voice_case(chromium, language, wav_path, expected, corrected):
    browser = chromium.launch(args=[
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        f"--use-file-for-fake-audio-capture={wav_path}",
    ])
    try:
        with closing(browser.new_context(permissions=["microphone"])) as context:
            page = context.new_page()
            reach_guest_prompt(page, language, "voice")
            with page.expect_response(
                lambda response: post_to(response, "/reception/transcribe/"), timeout=25000,
            ) as upload:
                page.locator("#start-voice").click()
            response = upload.value
            assert response.ok, f"Transcription failed ({response.status})"
            transcript = response.json()["text"]
            if not transcript:
                return False
            expect(page.locator("#voice-answer-review")).to_be_visible()
            expect(page.locator("#chat-message")).to_have_value(transcript)
            if transcript != expected:
                return False
            result, request = submit(page)
            assert request.get("transcript") is True
            assert_result(page, result, language, corrected)
            return True
    finally:
        browser.close()


def rehearse(chromium, browser, language, directory):
    _, _, noisy, clear, _ = FIXTURES[language]
    for phrase, label in ((noisy, "noisy"), (clear, "clear")):
        synthesize(language, phrase, directory / f"{language}-{label}.wav")
    noisy_wav = directory / f"{language}-noisy.wav"
    clear_wav = directory / f"{language}-clear.wav"

    chat_before = event_count(language, "chat")
    voice_before = event_count(language, "voice")
    chat_case(browser, language, noisy, corrected=False)
    assert event_count(language, "chat") == chat_before
    change_rule(language, "chat", noisy, active=True)
    chat_case(browser, language, noisy, corrected=True)
    assert event_count(language, "chat") == chat_before + 1
    chat_case(browser, language, clear, corrected=True)
    assert event_count(language, "chat") == chat_before + 1

    # If exact, this is both the opposite-channel scope probe and the voice baseline.
    matched = voice_case(chromium, language, noisy_wav, noisy, corrected=False)
    assert event_count(language, "voice") == voice_before
    change_rule(language, "chat", noisy, active=False)
    print(f"{language} chat correction and clear control passed; "
          f"opposite-channel scope {'passed' if matched else 'indeterminate (transcript mismatch)'}")

    corrected = False
    if matched:
        change_rule(language, "voice", noisy, active=True)
        corrected = voice_case(chromium, language, noisy_wav, noisy, corrected=True)
        assert event_count(language, "voice") == voice_before + int(corrected)
        chat_case(browser, language, noisy, corrected=False)
        assert event_count(language, "chat") == chat_before + 1

    clear_match = voice_case(chromium, language, clear_wav, clear, corrected=True)
    assert event_count(language, "voice") == voice_before + int(corrected)
    if matched:
        change_rule(language, "voice", noisy, active=False)
    print(f"{language} voice exact correction {'passed' if corrected else 'indeterminate (transcript mismatch)'}; "
          f"clear control {'passed' if clear_match else 'indeterminate (transcript mismatch)'}")


def main():
    required_environment = {
        "GITHUB_ACTIONS": "true",
        "COMPOSE_PROJECT_NAME": "hotel-parser-ci",
        "HOTEL_PARSER_TRAINING_ENABLED": "1",
    }
    if any(os.environ.get(name) != value for name, value in required_environment.items()):
        raise SystemExit("Run only against the disposable hosted Checks fixture")
    with tempfile.TemporaryDirectory(prefix="hotel-parser-fixtures-") as temporary, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for language in FIXTURES:
                rehearse(playwright.chromium, browser, language, Path(temporary))
        finally:
            browser.close()
    manage("from bookings.models import Booking; assert not Booking.objects.exists()")


if __name__ == "__main__":
    main()
