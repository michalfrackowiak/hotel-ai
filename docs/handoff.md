# Session handoff

## Latest milestone

Alpha verification, PR [#7](https://github.com/michalfrackowiak/hotel-ai/pull/7). A fresh Compose checkout verified local startup, dependency and migration checks, all 31 tests, and the reception, booking API, and admin paths. CI checks both language routes and chat and voice assets. A warmed local Ollama model extracted guest counts in both languages. See [alpha verification](alpha-verification.md) for the commands and the manual integration checks.

## Previous milestones

Room calendars, PR [#6](https://github.com/michalfrackowiak/hotel-ai/pull/6). A room can publish confirmed stays as all-day busy events without guest details. Django remains the source of availability. The optional worker uses a stable event ID and a durable retry record. An independent eight-worker SQLite probe produced one Google API attempt with simulated credentials and transport. A live Google Calendar test is pending. See [calendar setup](room-calendars.md).

Browser voice, PR [#5](https://github.com/michalfrackowiak/hotel-ai/pull/5). The guest can start a voice session in English or Polish. Voice reads the current prompt and submits microphone transcripts through the same chat flow. Chromium with simulated speech completed real reservations in both languages and exercised microphone denial, reset, and text fallback. A physical microphone and speaker still need a manual check. See [browser voice setup](browser-voice.md).

Model configuration, PR [#4](https://github.com/michalfrackowiak/hotel-ai/pull/4). Django admin switches among the built-in parser, a hosted OpenAI-compatible model, and Ollama. The selected provider interprets date and guest-count messages the local parser cannot read. The hosted URL and API key live together in deployment configuration. See [provider setup](model-providers.md).

Bilingual booking chat, PR [#3](https://github.com/michalfrackowiak/hotel-ai/pull/3). The homepage offers English and Polish reception chat. The server saves one conversation per Django session. Each turn uses a revision so a stale request cannot confirm a newer offer. A confirmed reservation returns the existing booking reference on retry.

All 19 Django tests passed. Chromium completed bookings in both languages, recovered from an interrupted request and stale tabs, and checked narrow mobile layouts. Eight simultaneous confirmations against a disk-backed database returned one booking reference and wrote one reservation.

Booking engine, PR [#2](https://github.com/michalfrackowiak/hotel-ai/pull/2). Django 5.2 runs in Docker with a persistent SQLite volume, the demo inventory, a local administrator seed, and bearer-authenticated availability and confirmation endpoints. See the [booking API](booking-api.md) for the HTTP contract. In-process callers can use `bookings.services.available_rooms` and `confirm_booking`.

Verification passed for Django checks, migration drift, and nine tests. A fresh Compose stack passed the seed, admin login, rate, authentication, and booking checks. A saved reservation survived container recreation. Independent review tested twelve concurrent callers against disk-backed SQLite and observed one booking for duplicate requests and one booking for overlapping requests.

Development tooling shipped in PR [#1](https://github.com/michalfrackowiak/hotel-ai/pull/1). Both repository MCP servers initialized in Codex on Windows. The project owner authorized opening and merging reviewed milestone PRs. See [tooling research](tooling-research.md) for the Claude-Mem evaluation and external sources.

## Next action

Run the manual integration checks in [alpha verification](alpha-verification.md) when the target microphone, provider credentials, or shared calendar are available. The dedicated mobile number and external booking-platform integrations are later milestones in the roadmap.
