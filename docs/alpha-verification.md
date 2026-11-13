# Alpha verification

## Run the checks from a fresh checkout

```sh
docker compose up --build --wait
docker compose exec web python manage.py seed_demo
docker compose exec web pip check
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py test
```

Use the local `.env` settings in the [README](../README.md) to enable demo admin login and bearer-authenticated booking API requests. Start the verification with an empty database and use fictional guest details. From the reception homepage, run an English and a Polish booking and check the references in Django admin. Run another request for the same room and dates to verify that the room no longer appears among available offers.

## Verification coverage

- The Docker stack was checked from a fresh checkout with a new SQLite volume. Django tests cover the booking API, bilingual chat, model provider contracts, and Google Calendar publication. CI also checks that the two reception routes and their JavaScript load after startup.
- Chromium completed English and Polish reservations with simulated browser speech, including permission denial and typed fallback. Use [browser voice setup](browser-voice.md) to verify a real microphone and playback on the intended device.
- Django extracted free-form English and Polish guest counts using a warmed `qwen3:0.6b` model on a local Ollama server. Hosted API behavior was checked against fixtures. A live hosted endpoint check requires its key in `.env`. The built-in parser works without a model endpoint. See [provider setup](model-providers.md).
- Calendar publication was tested with simulated Google responses. Follow the [live calendar check](room-calendars.md#verify-a-live-calendar) after configuring a shared test calendar and service-account credentials.
