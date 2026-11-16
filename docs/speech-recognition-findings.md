# Speech recognition findings and next safety increment

Reviewed 16 November 2026 against the fictional [ARM64 baseline](speech-baseline-2026-11-15.json), deployed application revision `ca543c9`. This historical diagnosis describes that revision and the proposed safety boundary. The subsequent [contact confirmation feature](contact-confirmation.md) implements the readback, explicit approval and typed fallback described below. No guest recordings, production conversations, bookings, or delivery requests were used for this diagnosis.

## What the baseline establishes

The two repetitions regenerate Piper speech before sending it to Vosk. They are separate synthetic clips, not repeated decoding of one retained recording. The corpus contains five spoken phrases and one silence control per language; it is too small to estimate guest accuracy, accents, noise tolerance, or false-acceptance rates. It does identify reproducible contact failures and one Polish date failure. See the [benchmark implementation](../scripts/benchmark_speech.py) and [measurement conditions](hardware-speech-baseline.md).

| Intended input | Observed transcript | Direct parser result | Consequence in the current shared flow |
| --- | --- | --- | --- |
| EN `guest at example dot com`, both repetitions | `guess that example dot com` | `None` | Remain at email entry and request a valid address. |
| PL `gosc małpa example kropka com`, repetition 1 | `gość małpa jak sample kropka kom` | `None` | Remain at email entry and request a valid address. |
| Same Polish input, repetition 2 | `gość małpa g sample kropka kon` | `None` | Remain at email entry and request a valid address. |
| PL `jutro`, repetition 1 | `i oto` | `None` | An unresolved arrival reply reaches staff handover. |
| PL `jutro`, repetition 2 | `jutro` | Arrival `2026-11-17` at the baseline reference date | Continue to departure entry. |

These are direct parser results, not live booking attempts. The English transcript has no recognized `at`/`@` separator. The Polish transcripts contain recognized `małpa`, but the resulting local part `gość` fails the installed Django email validator. Both intended email phrases parse successfully when supplied as text. `jutro` is an existing Polish relative date; adding it to the date vocabulary would not fix the changed transcript `i oto`. Sources: [contact parsing](../reception/contacts.py), [date parsing](../reception/parsing.py), [shared transitions](../reception/conversation.py), and [existing contact tests](../reception/test_scenario.py).

The baseline checks deterministic extraction only. Browser message handling can consult a separately configured stay-extraction provider after deterministic failure; the baseline did not exercise that provider or learned parser corrections. Phone calls use the shared booking transitions but receive Twilio `SpeechResult`, rather than this Vosk adapter. Do not project the measured Vosk failures onto Twilio accuracy. Sources: [browser parsing](../reception/views.py), [provider fallback](../model_providers/extraction.py), [phone gathering](../telephony/views.py), and [phone transitions](../telephony/calls.py).

## Rejection and incorrect acceptance need different handling

All four observed email failures were rejected. The baseline contains no observed incorrect email acceptance. Syntax validation nevertheless cannot establish the guest's intended destination. Controlled fictional probes illustrate the distinction:

| Transcript supplied directly to parser | Parsed result | Interpretation |
| --- | --- | --- |
| `guest at example dot com` | `guest@example.com` | Intended baseline address. |
| `guess at example dot com` | `guess@example.com` | Valid syntax; wrong local part if the guest intended `guest`. |
| `gosk małpa example kropka com` | `gosk@example.com` | Valid syntax; wrong local part if the guest intended `gosc`. |
| `plus four eight five zero one two three four five six eight` | `+48501234568` | Valid syntax; one digit differs from the intended baseline number. |

The changed-contact examples are constructed negatives, not additional ASR observations, and nothing was sent to these destinations. The contact parser maps spoken separators/digits, concatenates email tokens, then validates syntax. It does not verify ownership or correctness. After the earlier room/stay consent, the current `email` and `phone` branches call `_book` immediately when a contact parses; that transaction creates the booking and confirmation destination. There is no intervening contact readback or contact-specific approval. Provider acceptance later establishes dispatch status, not that the guest supplied the intended address. Sources: [contact parsing](../reception/contacts.py), [`advance` and `_book`](../reception/conversation.py), [booking transaction](../bookings/services.py), [confirmation dispatch](../bookings/confirmations.py), and [receipt status](../reception/receipt.py).

The current Vosk adapter returns only a text string. It creates an unrestricted recognizer for the selected language and exposes no word-confidence or alternate-candidate contract. No acoustic cause can be isolated from this JSON report because the synthetic audio was not retained. Do not label these failures as microphone clipping, a specific accent problem, or insufficient training data without separate evidence. Sources: [ASR runtime](../asr_runtime/service.py) and [browser speech adapter](../reception/speech.py).

## Proposed smallest safe feature

Add a shared contact-confirmation step before `_book`, plus an explicit browser option to type the contact. This protects both rejection recovery and plausible incorrect recognition without changing ASR models or guessing identity characters.

1. Keep failed email/phone parses at contact entry. In the browser, offer **Type instead** / **Wpisz zamiast dyktować**, pause microphone capture, and focus the existing text field. Typing must preserve the quoted stay and earlier consent. Phone callers can repeat or follow an explicit reception route; a browser-only typing instruction must not be spoken on the phone.
2. On a valid email or phone parse, store a pending candidate in the existing private conversation/call state. Show the exact normalized address or full international number and read it back with unambiguous spoken separators or individual digits. Ask **Is this correct?** / **Czy to się zgadza?** with confirm/change actions. Include typed contacts so a typo follows the same check.
3. Only an explicit affirmative for the current candidate and revision calls `_book`. A negative clears the candidate and returns to contact entry. Silence, ambiguous speech, an unrelated answer, or an old revision must never approve it. A replacement contact needs a fresh readback. Changing delivery method clears the earlier candidate.
4. Retain the current final availability/rate validation and booking idempotency key. Do not create a `Client`, `Booking`, or `BookingConfirmation` merely to display the candidate. A provider error after final confirmation follows the existing truthful receipt path.

This is a proposed state-machine change for browser chat/voice and phone together, with bilingual scenario-editor prompts. Readback reduces risk; it does not prove ownership, delivery, or that every spoken affirmation was recognized correctly. Typed fallback alone cannot protect a plausible wrong contact that was already accepted. Displaying the transcript alone also leaves no approval boundary: [voice submission](../reception/static/reception/voice.js) currently forwards recognized text immediately. Existing [conversation consent and contact tests](../reception/test_scenario.py), [revision locking](../reception/views.py), and [phone callbacks](../telephony/calls.py) provide the implementation seams.

For Polish arrival, retain `jutro` and the existing **Jutro** quick reply. Do not add a global `i oto` → `jutro` rule: the text is also an ordinary Polish phrase and contains no recoverable date. Investigate longer prompted staff utterances and capture quality separately, with consent. A date-specific retry before handover would change the supplied scenario's unrelated-matter routing and should be a separate scenario decision. Sources: [date rules](../reception/parsing.py), [arrival handover](../reception/conversation.py), and [quick replies](../reception/views.py).

## Acceptance checks for that feature

- English and Polish, email and SMS, typed and spoken, browser and phone: a valid contact reaches candidate confirmation with zero bookings or dispatch rows; only explicit current-candidate confirmation creates one booking and one destination.
- The four exact failed email transcripts remain rejected. The constructed wrong-but-valid email/phone examples reach readback, never an automatic write. Negative, silence, uncertain answers, and replacement contact inputs cannot reuse approval of an earlier candidate.
- Browser **Type instead** stops capture and ignores late ASR results; typing, stopping/restarting voice, or quick replies cannot erase the pending stay or duplicate the guest transcript. Captions and actual spoken readback agree in each language.
- Phone prompts contain no unusable browser instructions. Signed callbacks and retries enforce revisions, preserve idempotency, and apply the same candidate confirmation. Existing in-progress contact drafts upgrade safely without being treated as already approved.
- Recheck room availability/rate at final approval. Replayed final requests produce the same reference. Pending, accepted, and failed dispatch keep the current truthful receipt behavior.
- At fixed Europe/Warsaw date `2026-11-16`, `jutro` parses as `2026-11-17` and `i oto` remains unresolved; include a year boundary. Preserve intentional staff routing and separately test any future retry policy.
- Report correct extraction, invalid rejection, incorrect valid acceptance, successful user correction, and abandonment separately. Do not use syntactic **Succeeded** alone as an accuracy label. Reuse the small synthetic corpus as a smoke check and add separately consented staff speech before claiming recognition improvement.

## Privacy and verification

Keep pending contact values in the existing private state and render them only in the same session/call. Do not place contacts in URLs, telemetry, public reports, or logs. Keep the retired guest-audio queue retired. Existing parser-review contact text remains subject to its 30-day retention and staff permissions; contact confirmation must not expand collection or retention. Training corrections must continue to preserve address characters and phone digits; neither separator aliases nor manual rules should silently invent `guest` from `guess that`, transliterate `gość` into an identity, or change a phone digit. Sources: [parser review](../reception/parser_review.py), [correction validation](../reception/parser_training.py), [retention model](../reception/models.py), and [parser-training operations](parser-training.md).

Verification for this note used the repository's `.venv` with Django 5.2.19, an in-memory database setting, direct contact-parser calls, and a frozen date with default Polish date configuration patched in memory. Exact baseline transcripts returned the results above; the two correct email fixtures and constructed negatives were also checked. No network, database migrations, guest data, booking writes, or delivery calls were required. These probes validate the diagnosis; feature verification is recorded separately in the [handoff](handoff.md).
