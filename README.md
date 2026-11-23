# Hotel-ai

Hotel-ai is a bilingual hotel reception agent for making room reservations in English and Polish. The alpha uses a Django booking engine and a browser interface for voice and chat.

## Run the reception locally

Copy `.env.example` to `.env` and set `BOOKING_API_KEY` to a private random value to enable the booking API. To create the requested demo administrator, use `HOTEL_DEMO_ADMIN_USERNAME=Neptun` and set `HOTEL_DEMO_ADMIN_PASSWORD` to the test password in your local `.env`.

```sh
docker compose up --build --wait
docker compose exec web python manage.py seed_demo
```

Open [Hotel-ai reception](http://127.0.0.1:8080/) and choose **English** or **Polski**. The chat asks for dates, guest count, and contact details, then shows the room and total before asking you to confirm. A confirmed stay appears in Django admin with its booking reference.

Click **Start voice booking** to talk to the same reception and hear its replies. See [browser voice setup](docs/browser-voice.md) for microphone access and spoken confirmation.

For a separate Linux server, see the [server deployment guide](docs/server-deployment.md) for Django, Piper, and the public HTTPS reception. Use the SSH tunnel for private administration.

The chat works with the built-in parser by default. To interpret more free-form dates and guest counts, [configure a hosted model or Ollama](docs/model-providers.md) in Django admin.

To publish reservations to a dedicated Google Calendar for each room, follow the [room calendar setup](docs/room-calendars.md).

To accept reservations from a dedicated mobile number, follow the [phone booking setup](docs/phone-booking.md) for Twilio, carrier forwarding, or a SIM-to-SIP gateway.

Open [Django admin](http://127.0.0.1:8080/admin/) to manage the room inventory and nightly prices and inspect confirmed bookings. The seed creates `ROOM-2` for up to two guests at PLN 120.00 per night and `ROOM-6` for up to six guests at PLN 200.00 per night. Re-running the seed preserves existing prices and administrator credentials. A Docker volume persists the SQLite database.

The admin homepage links to workflows available to your staff account. Use **Room inventory** to pause new reservations for a room, **Reservations** to filter arrivals and departures by the hotel’s local date, and **Date parser settings → Preview dates** to try a fictional Polish or English reply using saved settings.

See the [booking API reference](docs/booking-api.md) for availability and confirmation requests. Compose runs the Django development server on `127.0.0.1`, with `DJANGO_DEBUG=1` by default to serve the admin assets. Set `HOTEL_PORT` in `.env` if port 8080 is in use.

```sh
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py test
```

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
