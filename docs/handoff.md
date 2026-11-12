# Session handoff

## Latest milestone

Bilingual booking chat, PR [#3](https://github.com/michalfrackowiak/hotel-ai/pull/3). The homepage offers English and Polish reception chat. The server saves one conversation per Django session. Each turn uses a revision so a stale request cannot confirm a newer offer. A confirmed reservation returns the existing booking reference on retry.

All 19 Django tests passed. Chromium completed bookings in both languages, recovered from an interrupted request and stale tabs, and checked narrow mobile layouts. Eight simultaneous confirmations against a disk-backed database returned one booking reference and wrote one reservation.

## Previous milestones

Booking engine, PR [#2](https://github.com/michalfrackowiak/hotel-ai/pull/2). Django 5.2 runs in Docker with a persistent SQLite volume, the demo inventory, a local administrator seed, and bearer-authenticated availability and confirmation endpoints. See the [booking API](booking-api.md) for the HTTP contract. In-process callers can use `bookings.services.available_rooms` and `confirm_booking`.

Verification passed for Django checks, migration drift, and nine tests. A fresh Compose stack passed the seed, admin login, rate, authentication, and booking checks. A saved reservation survived container recreation. Independent review tested twelve concurrent callers against disk-backed SQLite and observed one booking for duplicate requests and one booking for overlapping requests.

Development tooling shipped in PR [#1](https://github.com/michalfrackowiak/hotel-ai/pull/1). Both repository MCP servers initialized in Codex on Windows. The project owner authorized opening and merging reviewed milestone PRs. See [tooling research](tooling-research.md) for the Claude-Mem evaluation and external sources.

## Next action

Branch from `main` for model configuration. Extend the stay parser to handle free-form date and guest-count input via the selected provider. The booking flow owns quotes and confirmation. Browser voice will use the same chat endpoint and displayed quote.
