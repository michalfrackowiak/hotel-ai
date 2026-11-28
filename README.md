# Hotel-ai

Hotel-ai is a bilingual hotel reception application for making room reservations in English and Polish. It combines a Django booking engine and staff admin with browser-based chat and voice. Optional model integrations can help interpret free-form dates and guest counts; the built-in parser works without a model provider.

## Capabilities and architecture

- Guests can book through chat or a dedicated voice experience, review their stay and price, and explicitly confirm before a reservation is created. The reception can hand a conversation to staff.
- Django manages room inventory, nightly prices, reservations, guest conversations, and staff workflows. Staff can edit and publish booking scenarios, review parser corrections, and manage handovers and confirmation delivery.
- Optional integrations include hosted OpenAI-compatible models or Ollama, Piper speech generation and Vosk recognition, Google Calendar synchronization, email or SMS confirmations, inbound phone booking, and Google guest sign-in. Each integration requires its own configuration; see the linked setup guides below.
- The core application is a Django service with SQLite persistence. Docker Compose provides the local web service and separate overlays for optional services. The `reception`, `bookings`, `guest_accounts`, `telephony`, and `model_providers` Django apps cover guest and staff workflows; `speech_controller` contains the separate speech-routing and operations controller.

## Run the reception locally

Copy `.env.example` to `.env`. Set `BOOKING_API_KEY` to a private random value to enable the booking API. To create a demo administrator, set `HOTEL_DEMO_ADMIN_USERNAME` and choose a local-only `HOTEL_DEMO_ADMIN_PASSWORD`. Keep `.env` private and do not commit credentials.

```sh
docker compose up --build --wait
docker compose exec web python manage.py seed_demo
```

Open [Hotel-ai reception](http://127.0.0.1:8080/) and choose **English** or **Polski**. The chat asks for dates, guest count, and contact details, then shows the room and total before asking you to confirm. A confirmed stay appears in Django admin with its booking reference.

Click **Start voice booking** to talk to the same reception and hear its replies. See [browser voice setup](docs/browser-voice.md) for microphone access and spoken confirmation.

For a separate Linux server, see the [server deployment guide](docs/server-deployment.md) for Django, Piper, and the public HTTPS reception. Use the SSH tunnel for private administration.

The chat works with the built-in parser by default. To interpret more free-form dates and guest counts, [configure a hosted model or Ollama](docs/model-providers.md) in Django admin.

Optional integrations are configured separately: [room calendars](docs/room-calendars.md), [phone booking](docs/phone-booking.md), [email and SMS confirmations](docs/booking-confirmations.md), and [Google guest sign-in](docs/google-guest-signin-readiness.md).

Open [Django admin](http://127.0.0.1:8080/admin/) to manage the room inventory and nightly prices and inspect confirmed bookings. The seed creates `ROOM-2` for up to two guests at PLN 120.00 per night and `ROOM-6` for up to six guests at PLN 200.00 per night. Re-running the seed preserves existing prices and administrator credentials. A Docker volume persists the SQLite database.

The admin homepage links to workflows available to your staff account. Use **Room inventory** to pause new reservations for a room, **Reservations** to filter arrivals and departures by the hotel’s local date, and **Date parser settings → Preview dates** to try a fictional Polish or English reply using saved settings. The private admin also contains tools for staff handover, scenario authoring and publication, parser review, and optional speech operations; access depends on the staff account's permissions.

See the [booking API reference](docs/booking-api.md) for availability and confirmation requests. Compose runs the Django development server on `127.0.0.1`, with `DJANGO_DEBUG=1` by default to serve the admin assets. Set `HOTEL_PORT` in `.env` if port 8080 is in use.

```sh
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py test
```

The GitHub Actions checks also exercise isolated speech-controller and recovery tools, Compose configuration, and a hosted browser parser rehearsal. See [the workflow](.github/workflows/checks.yml) for the full check list.

## Development environment

Use Git, authenticated GitHub CLI, Docker with Compose, and Node.js 20 or newer for browser automation. Python 3.13 is useful for host-side checks. Clone the private repository with `gh repo clone michalfrackowiak/hotel-ai`.

- [`AGENTS.md`](AGENTS.md) describes the branch, review, and merge workflow.
- [`docs/roadmap.md`](docs/roadmap.md) tracks milestone acceptance and completion. [`docs/handoff.md`](docs/handoff.md) records the latest verified state and next action across sessions.
- [`.agents/skills/`](.agents/skills/) contains the installed project skills. Start a new Codex session if they do not appear in the skill selector.
- [`.codex/config.toml`](.codex/config.toml) configures Playwright MCP for isolated browser checks and OpenAI Docs MCP for provider and Codex references. In a trusted Codex project, use `codex mcp list` to inspect the configured servers. Node downloads the pinned Playwright MCP package when needed. Its first browser launch may also need a browser download.
- [`docs/tooling-research.md`](docs/tooling-research.md) records the evaluated tooling and the rationale for using a repository handoff as shared memory.

`main` holds merged milestones. Use `develop/<milestone>` branches and pull requests for development.

### Skill sources

- `code-review-and-quality`, `context-engineering`, and `incremental-implementation` come from [Agent Skills](https://github.com/addyosmani/agent-skills/tree/301b02b12bff98e4688e6984af6556802322bbde/skills). The MIT license and the referenced checklists are included. The handoff and completion guidance follows this project's authorized review and merge workflow.
- `gh-fix-ci` comes from [OpenAI's curated skills](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/gh-fix-ci) and includes its upstream license. Its workflow uses the project's existing authorization to fix and recheck failing milestone checks.
