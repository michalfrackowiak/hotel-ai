# Parser accuracy rehearsal

Use a disposable local instance or isolated staging target with fictional rooms and a fresh database. Enable parser training and keep the reception model on **Built-in demo**. Use the same app revision, language settings, room availability and booking scenario versions throughout. The guest-count step must allow parser corrections. Run `python manage.py seed_demo` against the fresh disposable database, or add a priced fictional room that accepts reservations, can accommodate two guests and is available for each test stay. Keep delivery and receptionist integrations disabled. Stop at the room offers.

| Language | Arrival and departure replies | Reply at the guest-count prompt | Intended count | Clear control reply |
| --- | --- | --- | --- | --- |
| English | `tomorrow`, then `for a week` | `two guess` | 2 | `two guests` |
| Polish | `jutro`, then `na tydzień` | `załoga bez liczby` | 2 | `dwie osoby` |

Run the guest-count comparison once in chat (`/chat/?lang=en` or `/chat/?lang=pl`) and once in browser voice (`/voice/?lang=en` or `/voice/?lang=pl`) for each language. Record the intended count before the trial. Assign someone other than the rule editor to compare the displayed count with that intent. The noisy replies represent fictional speech recognition errors.

1. With the correction absent or paused, start a new draft for the selected language and channel. Advance to the guest-count prompt. Use the specified reply and note whether the assistant asks again or shows an extracted count in the booking summary. Check the parser training row for its language, source, step and original result. Record the baseline before editing the rule.
2. Set **Corrected result** to `2` on the matching sample and check **Enable correction**. Confirm that the rule is eligible and that no enabled rule conflicts with it. For a chat rule the sample should show `chat`. For a voice rule it should show `voice`.
3. Start a fresh draft in the same language and channel with the same stay and reach the same prompt. Submit the exact same reply. Compare the displayed count to the intent and confirm the room offer appears. In the isolated web service log, check for one `parser_correction_applied` event for that turn with the expected language, `stage=guests`, `source=chat` or `source=voice`, and `original_result=missing`. Run one case at a time so the log can be checked by time window.
4. In a separate draft, submit the clear control reply. It should lead to the same count and room offers without emitting a correction event. Keep the tested rule enabled and try its noisy reply in a fresh draft in the other channel with no matching rule enabled for that channel. The assistant should ask for the count again and emit no correction event. Pause the tested rule after this scope check.

For browser voice, use the microphone and accept the transcript as shown on the voice review screen. The experiment needs the same transcript across both drafts. A typed reply or an edited transcript is classified as chat. If the speech recognizer does not return the fixture text in both drafts, mark the voice comparison indeterminate. For a one-case English voice smoke test of the server-side correction path with a deterministic transcript, run `python manage.py test reception.test_parser_review.ParserReviewTests.test_reviewed_result_resolves_later_voice_error_but_not_chat`.

The rehearsal passes when all four language-and-channel comparisons transition from a retry with no count and no correction event to room offers with the intended count and exactly one correction event. The clear controls must still yield the intended count and the other-channel scope checks must stay at the guest-count step. Report pass, fail or indeterminate and the reason for each case. Keep only aggregate counts and fictional case labels in shared evidence. Clean up the disposable database and its logs after the run.

The event shows that the correction changed a committed turn. The tester's comparison with predefined intent supplies accuracy evidence for this fixture. Real guest intent needs separate evidence.

## English wording observed in Vosk

The hosted harness repeats the English noisy synthetic clip in two fresh voice drafts. It checks a separate, predefined correction for `to guess`, the wording Vosk returned during the local preflight. A matching baseline must leave the guest count empty. The corrected reply must show two guests and produce one committed correction event. While the rule is active, submitting `to guess` in chat must leave the guest count empty with no correction event. If the transcript differs or the baseline already produces a result, the harness reports the comparison as indeterminate. The original `two guess` comparison retains its own result.

## Independently recorded English fixture

On an isolated Linux workstation, record a fictional speaker saying “two guess”
with the intended count of two guests. Export an uncompressed mono 16-bit PCM WAV
at 16–48 kHz, between 0.25 and 5 seconds and at most 512 KiB. Keep the clip
outside the checkout and use it only in the disposable rehearsal.

From a fresh checkout with no deployment `.env`, use a fresh local Docker
Compose project and the same service files as the hosted fixture. Install
Playwright and Chromium in a local Python environment before starting the
fixture.

```sh
python -m pip install 'playwright==1.58.0'
python -m playwright install --with-deps --only-shell chromium

export COMPOSE_FILE=compose.yaml:compose.server.yaml:compose.piper.yaml:compose.asr.yaml
export COMPOSE_PROJECT_NAME=hotel-parser-ci
export DJANGO_SECRET_KEY=django-insecure-ci-browser-parser-fixture
export HOTEL_PORT=8082
export HOTEL_PARSER_TRAINING_ENABLED=1

docker compose up --build --wait --wait-timeout 90 web piper asr
docker compose exec -T web python manage.py seed_demo
python -u scripts/rehearse_browser_parser.py --english-noisy-wav /private/english-noisy.wav
docker compose down --volumes --remove-orphans
```

Use the teardown command even if the rehearsal fails. Both English noisy voice
comparisons replay the same recording through Chromium's simulated microphone.
The clear English control and Polish voice cases still use Piper. Keep the
recorded results separate from the hosted synthetic results. A transcript
mismatch still yields an indeterminate case. A live physical microphone
rehearsal remains a separate acceptance check.
