# Hotel-ai roadmap

Each milestone has a development branch, a pull request to `main`, and independent review. Update this checklist and the [handoff](handoff.md) with the implementation PR and verification evidence.

## Alpha

- [x] Development environment. Create the private repository and branches. Track the roadmap, handoff, pinned project skills, and MCP configuration. Verify GitHub, Docker, and local development tools.
- [x] Booking engine. Run Django in Docker with persistent SQLite and Django admin. Seed one room for two guests at PLN 120 per night, one room for up to six guests at PLN 200 per night, and the requested local test administrator. Expose authenticated availability and booking endpoints. Confirm that retries cannot create duplicate bookings and overlapping dates cannot double-book a room.
- [x] Bilingual booking conversation. Offer a browser chat in English and Polish that gathers stay details and contact information, quotes an available room, and requires explicit confirmation before writing a booking. Verify successful and unavailable stays in both languages.
- [x] Model configuration. Switch the conversation model from Django admin. Support a hosted OpenAI-compatible provider with an API key and a small self-hosted model through Ollama. Run the local demo without a paid provider.
- [x] Browser voice. Allow guests to start a microphone session on the Django page, use English or Polish, and hear responses through the same conversation flow. Test both languages, microphone errors, and the booking confirmation path.
- [x] Room calendars. Link each room to its own Google Calendar in Django admin and publish bookings to the configured room calendar. Automated checks cover date boundaries and safe retries. Live publication remains pending a shared test calendar and service-account credentials.
- [x] Alpha verification. Start the Docker stack from a fresh checkout. Verify guest and admin paths and document local setup and provider configuration. Physical audio and credentialed hosted provider and Calendar checks are documented separately.
- [x] Split deployment and lightweight speech. Deploy Django, SQLite, and Piper on the ARM64 server. Keep browser microphone input and playback on the MacBook. Verify private SSH access, English and Polish WAV generation, and browser voice fallback. See the [server deployment guide](server-deployment.md).
- [x] Public browser access. Serve the reception through Nginx with HTTP-to-HTTPS redirects and automatic certificate renewal. Verify bilingual Piper replies and private administration routes. See the [server deployment guide](server-deployment.md).
- [x] Public microphone transcription. Capture microphone turns in the browser and transcribe with private English and Polish Vosk models on the server. Verify that a spoken date advances the public booking conversation without the browser speech service.
- [x] Relative checkout. Accept “in three days” and “za trzy dni” after check-in and read back both dates before collecting the guest count.
- [x] Checkout question. Ask how long guests will stay or when they plan to check out, and give an example of a supported reply in both languages.
- [x] My booking. Look up a confirmed stay by its confirmation number in English or Polish.
- [x] Room availability. Search by dates and number of guests, then confirm a selected room through a prefilled booking form.
- [x] Neptun browser booking scenario. Start in Polish, offer English, present room choices, and obtain consent before collecting contact details.
- [x] Neptun booking flow for chat, browser voice, and phone. Parse bilingual yearless dates and durations, offer room choices with total prices, and require consent before collecting the email address or SMS number.
- [x] Staff handover and confirmation delivery. Route browser handovers to the reception inbox and enable configured phone transfers. Queue confirmation requests atomically with the booking and dispatch them through SMTP or Twilio.
- [x] Match the Resort Neptun scenario prompts and timing. Open with a bilingual greeting and a two-second language window, wait ten seconds for an arrival date, quote room prices before consent, and use the selected delivery closing after provider acceptance.
- [x] Start English web chat and browser voice with a Neptun Resort greeting followed immediately by Step 2.b.
- [ ] Configure live SMTP, Twilio SMS, and the staff phone transfer destination. Verify each live destination with a controlled test.
- [x] Flexible stay dates. Extract arrival and departure from one English or Polish message, including a spoken arrival date and stay duration amid extra words.
- [x] Browser speech training. Let staff transcribe opted-in failed recognition clips, reuse reviewed English corrections, and export labeled audio for offline model evaluation and training.
- [x] Voice arrival prompts. Give English callers the address and a two-second pause, then offer two five-second listening windows in English and Polish.

## Later releases

- [x] Implement the inbound phone connector, including English and Polish booking flows, signed callbacks, and retry-safe confirmation.
- [ ] Route the dedicated mobile number to the connector and place live calls in both languages. This needs a Twilio number or SIP domain, a public HTTPS endpoint, and a carrier forwarding or cellular gateway route.
- [ ] Add integrations with hotel booking platforms as needed.
