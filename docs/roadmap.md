# Hotel-ai roadmap

Each milestone has a development branch, a pull request to `main`, and independent review. Update this checklist and the [handoff](handoff.md) with the implementation PR and verification evidence.

## Alpha

- [x] Development environment. Create the private repository and branches. Track the roadmap, handoff, pinned project skills, and MCP configuration. Verify GitHub, Docker, and local development tools.
- [x] Booking engine. Run Django in Docker with persistent SQLite and Django admin. Seed one room for two guests at PLN 120 per night, one room for up to six guests at PLN 200 per night, and the requested local test administrator. Expose authenticated availability and booking endpoints. Confirm that retries cannot create duplicate bookings and overlapping dates cannot double-book a room.
- [x] Bilingual booking conversation. Offer a browser chat in English and Polish that gathers stay details and contact information, quotes an available room, and requires explicit confirmation before writing a booking. Verify successful and unavailable stays in both languages.
- [x] Model configuration. Switch the conversation model from Django admin. Support a hosted OpenAI-compatible provider with an API key and a small self-hosted model through Ollama. Run the local demo without a paid provider.
- [x] Browser voice. Allow guests to start a microphone session on the Django page, use English or Polish, and hear responses through the same conversation flow. Test both languages, microphone errors, and the booking confirmation path.
- [ ] Room calendars. Link each room to its own Google Calendar in Django admin and publish bookings to the configured room calendar. Verify date boundaries and safe retries, then exercise a real test calendar when credentials are available.
- [ ] Alpha verification. Start the Docker stack from a fresh checkout. Verify the complete guest and admin paths and document local setup and provider configuration.

## Later releases

- [ ] Connect the reception agent to a dedicated mobile number through a SIM/GSM or telephony gateway. Verify inbound calls and booking conversations in both languages.
- [ ] Add integrations with hotel booking platforms as needed.
