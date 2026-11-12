# Session handoff

## Latest milestone

Booking engine, PR [#2](https://github.com/michalfrackowiak/hotel-ai/pull/2). Django 5.2 runs in Docker with a persistent SQLite volume, the demo inventory, a local administrator seed, and bearer-authenticated availability and confirmation endpoints. See the [booking API](booking-api.md) for the HTTP contract. In-process callers can use `bookings.services.available_rooms` and `confirm_booking`.

Verification passed for Django checks, migration drift, and nine tests. A fresh Compose stack passed the seed, admin login, rate, authentication, and booking checks. A saved reservation survived container recreation. Independent review tested twelve concurrent callers against disk-backed SQLite and observed one booking for duplicate requests and one booking for overlapping requests.

Development tooling shipped in PR [#1](https://github.com/michalfrackowiak/hotel-ai/pull/1). Both repository MCP servers initialized in Codex on Windows. The project owner authorized opening and merging reviewed milestone PRs. See [tooling research](tooling-research.md) for the Claude-Mem evaluation and external sources.

## Next action

Branch from `main` for the bilingual browser chat. Use the booking service to validate availability and rates, then persist a reservation after explicit guest confirmation. Keep the confirmation key stable across retries. Use Europe/Warsaw for hotel dates and checkout as the exclusive end date. The booking database owns alpha availability.
