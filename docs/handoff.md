# Session handoff

## Latest milestone

Split deployment and lightweight speech. The Ubuntu 26.04 ARM64 host runs the `hotel-ai` Compose project from `~/hotel-ai`. Docker Engine 29.9.2 and Compose 5.6.1 were already installed. Django runs with debug disabled on the server's loopback port 8080. Piper shares a private Docker network with Django and uses the server's ARM CPU to synthesize English and Polish prompts. The MacBook connects over SSH and handles microphone capture and playback in Chrome. The server seeded demo inventory into a fresh SQLite volume. See [server setup](server-deployment.md).

All 52 Django tests passed, and an independent review checked Compose isolation and the session-bound audio route. The native ARM64 smoke returned 5.48 seconds of English audio in 1.07 seconds and 7.47 seconds of Polish in 1.42 seconds through Django. Piper used about 341 MiB after the smoke. The release image explicitly grants the non-root service read access to the downloaded model files. A Chromium test checked browser speech fallback and stale-session handling.

The Piper deployment supersedes the ZONOS3-Mini experiment in [PR #9](https://github.com/michalfrackowiak/hotel-ai/pull/9). Keep using the built-in conversation parser for the private demo. Hotel-ai uses a dedicated project and does not require Coolify.

## Previous milestones

Inbound phone connector, PR [#8](https://github.com/michalfrackowiak/hotel-ai/pull/8). An optional Gunicorn service accepts signed Twilio voice webhooks and shares the browser reception's SQLite booking database. The caller chooses English or Polish by keypad, hears the quote, and confirms the booking by voice. Phone calls use the built-in parser so each callback has time to finish within the Twilio deadline. See [phone booking setup](phone-booking.md) for exact configuration, call routing, and the live smoke test.

All 44 Django tests passed. Independent review verified the separate listener's route restrictions and sent eight simultaneous signed confirmations against file-backed SQLite. The callbacks returned one shared reference and created one booking. A live Twilio call and carrier route are still pending.

Alpha verification, PR [#7](https://github.com/michalfrackowiak/hotel-ai/pull/7). A fresh Compose checkout verified local startup, dependency and migration checks, all 31 tests, and the reception, booking API, and admin paths. CI checks both language routes and chat and voice assets. A warmed local Ollama model extracted guest counts in both languages. See [alpha verification](alpha-verification.md) for the commands and the manual integration checks.

Room calendars, PR [#6](https://github.com/michalfrackowiak/hotel-ai/pull/6). A room can publish confirmed stays as all-day busy events without guest details. Django remains the source of availability. The optional worker uses a stable event ID and a durable retry record. An independent eight-worker SQLite probe produced one Google API attempt with simulated credentials and transport. A live Google Calendar test is pending. See [calendar setup](room-calendars.md).

Browser voice, PR [#5](https://github.com/michalfrackowiak/hotel-ai/pull/5). The guest can start a voice session in English or Polish. Voice reads the current prompt and submits microphone transcripts through the same chat flow. Chromium with simulated speech completed real reservations in both languages and exercised microphone denial, reset, and text fallback. A physical microphone and speaker still need a manual check. See [browser voice setup](browser-voice.md).

Model configuration, PR [#4](https://github.com/michalfrackowiak/hotel-ai/pull/4). Django admin switches among the built-in parser, a hosted OpenAI-compatible model, and Ollama. The selected provider interprets date and guest-count messages the local parser cannot read. The hosted URL and API key live together in deployment configuration. See [provider setup](model-providers.md).

Bilingual booking chat, PR [#3](https://github.com/michalfrackowiak/hotel-ai/pull/3). The homepage offers English and Polish reception chat. The server saves one conversation per Django session. Each turn uses a revision so a stale request cannot confirm a newer offer. A confirmed reservation returns the existing booking reference on retry.

All 19 Django tests passed. Chromium completed bookings in both languages, recovered from an interrupted request and stale tabs, and checked narrow mobile layouts. Eight simultaneous confirmations against a disk-backed database returned one booking reference and wrote one reservation.

Booking engine, PR [#2](https://github.com/michalfrackowiak/hotel-ai/pull/2). Django 5.2 runs in Docker with a persistent SQLite volume, the demo inventory, a local administrator seed, and bearer-authenticated availability and confirmation endpoints. See the [booking API](booking-api.md) for the HTTP contract. In-process callers can use `bookings.services.available_rooms` and `confirm_booking`.

Verification passed for Django checks, migration drift, and nine tests. A fresh Compose stack passed the seed, admin login, rate, authentication, and booking checks. A saved reservation survived container recreation. Independent review tested twelve concurrent callers against disk-backed SQLite and observed one booking for duplicate requests and one booking for overlapping requests.

Development tooling shipped in PR [#1](https://github.com/michalfrackowiak/hotel-ai/pull/1). Both repository MCP servers initialized in Codex on Windows. The project owner authorized opening and merging reviewed milestone PRs. See [tooling research](tooling-research.md) for the Claude-Mem evaluation and external sources.

## Next action

Open the private reception on the MacBook through the [SSH tunnel](server-deployment.md#connect-from-the-macbook) and make a voice booking in each language with the MacBook microphone and speakers to check speech quality and recognition.

Route a test number through Twilio and verify live calls in both languages when a number or SIP domain, a public HTTPS origin, and the carrier route are available. Add the Twilio Auth Token through the ignored `.env` file. The manual checks in [alpha verification](alpha-verification.md) remain pending the target microphone, hosted provider credentials, or shared calendar. Booking-platform integration depends on which platform the hotel uses.
