# Session handoff

## Latest milestone

My booking. The reception has English and Polish booking lookup. Guests submit the confirmation number from their receipt and see the room, dates, guest count, and booked price. The lookup uses a CSRF-protected form, keeps the number out of the URL, and returns uncached responses. Nginx publishes the lookup on the guest listener with the reception rate limit.

## Previous milestones

Checkout question. After check-in, the reception asks how long the guest will stay or when they plan to check out. The prompt and retry give a supported example in English and Polish. Guests can supply a checkout date or an existing relative expression such as “in three days” or “za trzy dni.” Converting lengths measured from check-in remains pending.

Relative checkout. The shared date parser accepts relative day and week offsets in English and Polish, measured from the current date in Europe/Warsaw. After accepting checkout, the reception reads back both dates before asking how many guests are staying. Regression tests cover “tomorrow” followed by “in three days” and its Polish equivalent across a year boundary. The yearless and one-sentence stay parser work remains open.

Public microphone transcription. The original browser speech service could fail after Piper finished its greeting. The public reception now captures audio during each listening turn and sends bounded PCM to Django. Django matches the session revision, uses the conversation language, and relays the samples in memory to an internal Vosk service. Other deployments may continue to use their browser speech service. See [browser voice booking](browser-voice.md) and the [server deployment guide](server-deployment.md).

All 56 Django tests passed. The native ARM64 stack loaded both Vosk models and kept the Piper and Django services healthy. Independent Chrome tests fed prerecorded microphone replies through the public HTTPS page, the audio worklet, and Vosk. “Tomorrow” and “jutro” both advanced the conversation to checkout. Permission denial kept chat available. Stopping a session released the microphone and ignored a late transcript. Physical microphone recognition on the target browser remains a manual check.

Public browser access. Nginx on the Ubuntu host forwards the reception and its static files to Django on loopback. [HTTP](http://92.5.19.10/) redirects to the [HTTPS reception](https://92.5.19.10/). The host firewall allows inbound TCP 80 and 443 before the Oracle image's reject rule. Certbot renews the short-lived IP certificate with HTTP webroot validation and reloads Nginx through its deploy hook. The admin, booking API, and phone callback paths remain behind their private or separately configured listeners. See the [server deployment guide](server-deployment.md).

External requests verified the redirect, trusted TLS, valid browser-style CSRF requests and rejection of cross-origin requests, English and Polish Piper WAVs, secure cookies, and public 404s for private endpoints. Independent Chrome checks played the real Piper replies using simulated microphone recognition. All 52 Django tests passed.

Split deployment and lightweight speech, PR [#10](https://github.com/michalfrackowiak/hotel-ai/pull/10). The Ubuntu 26.04 ARM64 host runs the `hotel-ai` Compose project from `~/hotel-ai`. Docker Engine 29.9.2 and Compose 5.6.1 were already installed. Django runs with debug disabled on the server's loopback port 8080. Piper shares a private Docker network with Django and uses the server's ARM CPU to synthesize English and Polish prompts. The MacBook connects over SSH and handles microphone capture and playback in Chrome. The server seeded demo inventory into a fresh SQLite volume. See [server setup](server-deployment.md).

All 52 Django tests passed, and an independent review checked Compose isolation and the session-bound audio route. The native ARM64 smoke returned 5.48 seconds of English audio in 1.07 seconds and 7.47 seconds of Polish in 1.42 seconds through Django. Piper used about 341 MiB after the smoke. The release image explicitly grants the non-root service read access to the downloaded model files. Chromium played two live prompts in each language through the SSH tunnel with simulated microphone recognition, and it handled offline Piper and stale revisions. The server also passed admin login and authenticated rate checks.

The Piper deployment supersedes the ZONOS3-Mini experiment in [PR #9](https://github.com/michalfrackowiak/hotel-ai/pull/9). Keep using the built-in conversation parser for the private demo. Hotel-ai uses a dedicated project and does not require Coolify.

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

Open the [public reception](https://92.5.19.10/) in Chrome on the MacBook and make a voice booking in each language with its microphone and speakers. Use the [SSH tunnel](server-deployment.md#connect-from-the-macbook) for Django admin.

Route a test number through Twilio and verify live calls in both languages when a number or SIP domain, a public HTTPS origin, and the carrier route are available. Add the Twilio Auth Token through the ignored `.env` file. The manual checks in [alpha verification](alpha-verification.md) remain pending the target microphone, hosted provider credentials, or shared calendar. Booking-platform integration depends on which platform the hotel uses.
