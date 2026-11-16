# Hotel-ai roadmap

Each milestone has a development branch, a pull request to `main`, and independent review. Update this checklist and the [handoff](handoff.md) with the implementation PR and verification evidence.

## Next versions

See the [phased roadmap for versions 0.4–1.0](next-versions-roadmap.md) for processing steps, speech-provider switching, full scenario configuration, separate voice/chat views, Google accounts, the blue visual redesign, hardware budgets and acceptance gates. These are planned releases, not implemented features. Start with the hardware and performance baseline in 0.4.

- [x] 0.4 first baseline: inventory accessible server/MacBook hardware, add bounded bilingual synthetic speech tooling, measure warm latency and semantic extraction, and verify isolated SQLite backup/restore. See [results and remaining checks](hardware-speech-baseline.md).
- [ ] 0.4 completion: staging cold-start/concurrent-load/peak-resource checks, physical microphone evaluation, full off-host recovery and any separately used PC/macOS inventory.
- [x] 0.4 staging increment: cached-image process readiness, two-request admission, cgroup memory peaks and web-health responsiveness. See [staging results](staging-speech-capacity.md). Sustained mixed workload and microphone evaluation remain open.
- [x] Contact safety increment: bilingual email/SMS readback and explicit confirmation before booking, correction and delivery changes, browser typed fallback, shared phone behavior, and revision/idempotency checks. All 149 Django tests and independent bilingual Chromium checks passed. [PR #33](https://github.com/michalfrackowiak/hotel-ai/pull/33) is merged and deployed; live bilingual contact and speech checks passed. See [contact confirmation](contact-confirmation.md).
- [x] 0.4 bounded mixed-load increment: 60-second isolated bilingual speech and booking workload, 20 completed fictional bookings, resource counters, health-abort checks and verified cleanup. See [results](staging-mixed-load.md). Long-term stability, physical microphone, uncached startup and off-host recovery remain open.

## Alpha

- [x] 0.5 speech readiness callback: real bilingual Piper synthesis and Vosk silence probes through approved local endpoints, with bounded responses and strict format checks. See [probe scope](speech-readiness-probes.md). Guest gating/routing and production activation remain pending.

- [x] 0.5 Docker lifecycle composition: approved controls connected to durable execution through required admission/traffic/readiness/recovery interfaces, with isolated switching and rollback coverage. See [scope](speech-docker-lifecycle.md). Production gate/model implementations and guest dispatch remain pending.

- [x] 0.5 Docker control adapter: bounded start/stop of approved full container IDs with pinned daemon and pre/post identity, image, limit and ownership checks. See [control boundary](speech-docker-control.md). Lifecycle/HTTP/guest integration remains disconnected.

- [x] 0.5 explicit reconciliation: exclusive execution/recovery lock, verifier-gated revision changes and atomic idempotent audit records. See [recovery contract](speech-journal-reconciliation.md). Production verification and Docker/guest mutation remain unwired.

- [x] 0.5 durable lifecycle tracking: separate SQLite pending/completed journal, restart-safe idempotency and blocking of uncertain work. See [journal limits](speech-durable-journal.md). Automatic reconciliation, Docker mutation and guest routing remain unwired.

- [x] 0.5 lifecycle core: serialized, revision-checked switch sequencing with shared-worker retention, readiness, idempotency and failure recovery through an injected test backend. See [executor scope](speech-lifecycle-executor.md). Docker execution, durable recovery and guest routing are not wired.

- [x] 0.5 resource preview: explicit local reserves/artifact estimates, pinned daemon and operator-confirmed topology checked against the same transition snapshot. See [resource policy](speech-resource-admission.md). Default blocked; no execution authorization or automatic topology attestation.

- [x] 0.5 daemon identity: observed daemon identity and optional explicit Unix-socket/daemon-ID pinning, with legacy connections labeled unverified. See [identity limits](speech-daemon-identity.md). Pinning alone does not establish resource co-location or admission.

- [x] 0.5 host observations: Linux controller-host CPU/memory and explicitly configured filesystem space in inventory/admin, with unknown unsupported metrics and legacy response compatibility. See [scope](speech-host-resources.md). Observations are not admission or verified Docker-daemon capacity.

- [x] 0.5 transition budget preview: retain observed active workers while counting planned allocations once, with blockers for unknown limits, name conflicts and uncovered hosts. See [overlap accounting](speech-transition-budget.md). No stop credit, measured headroom or activation authorization.

- [x] 0.5 observed admin inventory: restricted read-only controller snapshot, freshness/schema checks and exact host/container comparisons with planned image and resource settings. See [admin inventory](speech-admin-inventory.md). Disabled by default; no saved-state updates or activation.

- [x] 0.5 controller inventory: separate authenticated loopback service observes only allowlisted container state, image identity and configured limits through bounded read-only Docker calls. See [controller](speech-controller.md). Django integration, observed transition admission and mutation remain unimplemented.

- [x] 0.5 static preflight: private admin checks complete assignments against local host/image approvals, architecture, metadata completeness and reserved CPU/RAM budgets, counting shared deployments once. See [preflight](speech-plan-preflight.md). This does not authorize activation or replace observed transition admission in the future controller.

- [x] 0.5 planning slice: deployment metadata and four fixed EN/PL STT/TTS planned assignments, shared-deployment reference counts and compatibility validation. See [deployment plans](speech-deployment-plans.md). Assignments do not activate providers; lifecycle control and live routing remain next.

- [x] 0.5 first slice: private speech-provider catalog with STT/TTS metadata, EN/PL model/voice entries, pinned artifact validation and resource budgets. See [catalog](speech-provider-catalog.md). Live selection and Docker switching remain unimplemented; remaining 0.4 gates stay open.

- [x] Off-host SQLite/Django recovery: private online snapshot transferred intact, restored on Windows/x86-64, integrity/migration/bilingual entry/booking replay checks passed and temporary copies deleted. See [drill](offhost-recovery.md). Full replacement-host and speech-service recovery remains open.

- [x] Two-minute deployed-queue staging validation: 39 fictional bookings and 126 successful speech requests; unchanged production health and verified cleanup. See [report](queue-load-validation.md). [Physical microphone protocol](microphone-evaluation.md) prepared; execution remains pending.

- [x] Bounded speech queue: one active inference and one waiting request per engine, three-second waiting deadline, explicit busy feedback and shared web admission cap. Candidate staging and automated recovery tests passed; see [speech queue](speech-queue.md).

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
- [x] Retire browser speech training. Remove opt-in audio capture, sample storage, and the admin transcription/export view while keeping live voice transcription.
- [x] Voice arrival prompts. Give English callers the address and a two-second pause, then offer two five-second listening windows in English and Polish.
- [x] Booking scenario editor. Edit bilingual chat text, voice scripts, and pauses in the private admin.
- [x] Parser training. Review bilingual chat and voice inputs and their parsed values, then reuse validated staff corrections.
- [x] Synchronize voice and chat. Display spoken segments as playback begins, show guest transcripts, and offer contextual reply buttons.
- [x] Expand parser configuration and bilingual stay dates. Configure accepted numeric formats in admin, parse check-in and check-out dates from one sentence, and retain text-based parser review and corrections.

## Later releases

- [x] Implement the inbound phone connector, including English and Polish booking flows, signed callbacks, and retry-safe confirmation.
- [ ] Route the dedicated mobile number to the connector and place live calls in both languages. This needs a Twilio number or SIP domain, a public HTTPS endpoint, and a carrier forwarding or cellular gateway route.
- [ ] Add integrations with hotel booking platforms as needed.
