# Hotel-ai: next versions

Planning baseline: 15 November 2026, after PR #28. Version numbers below are proposed delivery milestones, not release dates. STT means speech-to-text (called SST in the request). This document plans future implementation; it does not enable services or change live bookings.

## Direction and processing steps

```text
Choose voice or chat → choose language → optional Google sign-in
  Voice: microphone → STT → editable transcript
  Chat: typed message / suggested answer
    → extract booking details → validate → scenario step
    → availability and quote → explicit confirmation → booking
    → receipt / staff handover / account history
  Voice output: scenario text → TTS → audio + captions + animated sphere
  Chat output: independent scenario text + answer buttons
```

Both experiences use the same booking engine, inventory and consent rules. The public voice view keeps visible captions and guest transcripts. The chat view has its own entry point and never requests a microphone. Selected English begins in English; selected Polish begins in Polish, with language switching available explicitly.

| Release | Processing area | Deliverable | Dependency |
| --- | --- | --- | --- |
| 0.4 | Measure and operate | Hardware inventory, benchmark baseline, reliability backlog | Current production |
| 0.5 | Speech input/output | Provider catalog, per-language selection and container lifecycle | 0.4 |
| 0.6 | Booking decisions | Complete scenario configuration with drafts and publishing | 0.4; integrate with 0.5 |
| 0.7 | Guest interaction | Separate voice/chat views, blue luxury design, speaking animation | 0.6; voice uses 0.5 |
| 0.8 | Identity and continuity | Google registration/login, guest account and booking ownership | 0.7 |
| 0.9 | Quality and hotel operations | Parser workbench, delivery, calendar and staff improvements | 0.5–0.8 |
| 1.0 | Assisted reception | Selected advanced features and production readiness | Measured results from 0.9 |

Each release should be split into small PRs with an implementation owner, independent reviewer, acceptance evidence and handoff update. Owners are assigned when a milestone begins. Backend scenario design and frontend visual design can proceed in parallel after the baseline. Do not commit to delivery dates until the hardware benchmark and flow inventory are complete.

## 0.4 — Baseline and operating limits

- [ ] Inventory server, PC and MacBook: CPU architecture, cores, RAM, free disk, OS, Docker availability and thermal behavior. Repository deployment notes record an ARM64 server with two Neoverse N1 cores, 11.6 GiB RAM and 96 GiB filesystem. Confirm current free capacity. The 2019 MacBook's exact CPU/RAM are not recorded; do not assume Apple Silicon acceleration. PC specifications are unknown.
- [ ] Measure existing Vosk/Piper end-to-end performance in Polish and English: transcription latency, speech startup, date/contact extraction accuracy, real-time factor, peak memory, CPU and simultaneous sessions. Use fictional scripts and separately consented staff recordings; the removed guest audio training queue stays removed.
- [ ] Publish a benchmark card per model, language, architecture and container image digest. Include cold start, warm inference and performance during concurrent web requests. Prior synthetic results in the handoff favored the existing small English Vosk model over a larger alternative; repeat on the target server before changing defaults.
- [ ] Set initial capacity to one inference request per worker with a bounded queue. Increase only after load measurements. Start with at least 3 GiB of server RAM reserved for OS, web/database, buffers and safety margin; this is a proposed admission budget, not measured spare capacity. Avoid swapping and reserve disk for backups and model downloads.
- [ ] Inventory missing live integrations: SMTP, SMS sender, phone transfer/carrier routing, Google Calendar credentials. Record owners and configuration dependencies.

**Acceptance:** reproducible hardware/benchmark report, approved concurrency and latency budgets, restore drill for SQLite, and a ranked defect list. Target warm speech startup within two seconds and STT response within three seconds after a short answer; revise these provisional targets using measured results, not assumptions.

## 0.5 — Pluggable STT and TTS

### Catalog and private admin

- [ ] Add `SpeechProvider` records for engine, STT/TTS capability, supported languages, model/voice, pinned image and model checksum, architecture, license, estimated resources and approved parameter schema.
- [ ] Add `SpeechDeployment` records for host and container instance, desired/observed state, loaded models, health and measured resources. Add `SpeechSelection` with a unique `(language, task)` key, and `ProviderSwitch` records for progress, requester, failure and rollback.
- [ ] Build dedicated admin views: provider catalog, EN/PL × STT/TTS selection matrix, container health, benchmark results, preview transcription/synthesis, switch history and rollback. Preview inactive providers in a maintenance window or on the PC so it does not silently add production workers.
- [ ] Support Docker plug-ins through a documented, versioned adapter contract: bounded PCM input/text output for STT; text, language and voice input/audio output for TTS; health/readiness, cancellation, timeout and error responses. Keep engine-specific parameters behind validated forms.

### Exactly one selected provider per language and task

| Deployment shape | Active speech containers |
| --- | --- |
| Separate Polish STT/TTS and English STT/TTS | 4 |
| Shared bilingual STT plus separate TTS, or the reverse | 3 |
| Shared bilingual STT and shared bilingual TTS | 2 |

These counts exclude Django, the controller and other hotel services. Sharing requires an adapter that explicitly supports both languages; it does not imply that one model provides both STT and TTS. The current bilingual Vosk and Piper services are candidates for the two-container shape.

- [ ] Before switching, check the measured total CPU/RAM and disk requirement against the approved host budget, including any shared old provider that must remain running alongside the replacement. Reject or defer a switch that does not fit. Lock affected selections, stop admitting new speech turns, finish or cancel in-flight work within a deadline, and pause the affected voice experience with a clear status. Preserve the booking draft.
- [ ] Stop the old deployment when no other selection references it. Start the replacement, load the required language, check readiness, and atomically activate the new selection. If startup fails, stop the failed replacement and restart/restore the previous selection. Record every outcome and make retries idempotent.
- [ ] A shared deployment serving the other language must remain running. If only one language changes, split the mapping and retain the shared instance for its remaining selection. Serialize concurrent switches and reconcile actual container state after controller restart.
- [ ] Run the lifecycle controller separately from public Django. Accept only allowlisted images, commands, networks and bounded resource limits; never expose the Docker socket or arbitrary shell/image entry to the public application. Use an authenticated private control channel and audit staff actions.
- [ ] Support an ARM64 server host and a PC host first. Treat the MacBook as a browser client by default, with an optional local Docker worker after measurement. Remote workers require private authenticated networking and host health checks. If a laptop sleeps or disconnects, offer chat and preserve the draft; do not depend on it for unattended production service.

**Acceptance:** all four language/task selections work independently; 2/3/4-container layouts have correct reference ownership; successful, failed and concurrent switches preserve booking state; inactive unreferenced containers are stopped; server remains responsive under its admitted workload.

### Candidate models to benchmark

| Task | Candidate | Role and constraint |
| --- | --- | --- |
| STT, EN/PL | Vosk small EN and PL | Baseline already deployed. Official catalog lists small models around 50 MB with typical runtime memory around 300 MB; measure the combined service. [Model catalog](https://alphacephei.com/vosk/models) |
| STT, EN/PL | whisper.cpp multilingual tiny/base, quantized where suitable | First alternative for noisy/free-form answers. Use multilingual weights for Polish; test CPU latency and silence hallucinations before activation. Supports CPU inference and quantization; published model sizes are not deployment RAM guarantees. [Project](https://github.com/ggml-org/whisper.cpp) |
| TTS, EN/PL | Piper with selectable voices | Preferred neural baseline. Verify the exact downloaded voice's model card and commercial-use chain independently from the engine license. [Voices](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md), [licensing](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md) |
| TTS, EN/PL | eSpeak NG | Low-resource alternative and operational fallback candidate; compare intelligibility and naturalness with staff. [Project and supported languages](https://github.com/espeak-ng/espeak-ng) |
| STT/TTS research | sherpa-onnx with specific approved models | Alternative runtime for local inference. Select actual EN/PL model artifacts and verify language, license and resource use before claiming support. [Official documentation](https://k2-fsa.github.io/sherpa/onnx/index.html) |

These are candidates, not a claim that every combination meets the hardware budget. Do not run every candidate continuously. Benchmark one replacement at a time; retain inactive images/model files subject to a disk quota. Prefer voice choice and prompt caching before adopting a much larger TTS model.

## 0.6 — Complete scenario editing

The current admin edits prompt text and voice segments; conversation transitions remain in Python. Extend this into configuration over approved booking actions.

- [x] Inventory the booking states and outcomes across English and Polish text chat, browser voice and inbound phone, including retries, timeouts, reception handover and older drafts. See the [booking scenario flow inventory](scenario-flow-inventory.md).
- [x] Define the schema for `Scenario`, `ScenarioVersion`, `ScenarioStep` and `ScenarioTransition`, including separate EN/PL voice and chat scopes and a pinned reference to optional shared defaults. See [version records](scenario-version-records.md).
- [x] Expose draft graph editing, a pinned inheritance indicator, and structural and approval-path validation in Django admin. See [draft editing](scenario-version-records.md#draft-editing-and-validation).
- [x] Validate named outcome coverage for standalone English and Polish web chat and browser voice, including both contact methods, rebooking and dispatch paths.
- [x] Materialize pinned shared defaults and validate the resulting graph. Seal, select, roll back, and audit versions through an internal atomic service.
- [x] Expose permission-gated staff publication and rollback with an effective graph diff, recent selection history, and stale-review protection. See [staff review](scenario-version-records.md#review-publication-or-rollback).
- [x] Edit primary draft prompts with validated chat placeholders and ordered voice segments, pauses and arrival listening.
- [x] Add per-outcome chat wording and voice segments for supported browser prompts.
- [x] Edit chat labels for date and room suggestions, language choice, quote and contact decisions, and delivery actions independently of submitted answers.
- [x] Configure whether an arrival step accepts a single arrival date or requires both stay dates in one reply.
- [x] Configure retry limits for supported browser booking steps and route exhausted retries to reception.
- [x] Configure pinned browser voice silence windows for arrival, departure and guest count.
- [x] Extend pinned browser voice silence windows to room selection, quote decisions, delivery choice, email, phone and contact confirmation.
- [x] Configure the Polish bilingual welcome's browser voice silence window before the usual arrival prompt.
- [x] Configure whether guests can skip spoken prompts at the arrival, departure and guest-count steps.
- [x] Extend opt-in prompt skipping to the Polish welcome, room selection, delivery choice, and email and phone entry. Keep quote and contact confirmation playback outside this setting.
- [x] Configure whether final browser voice silence at arrival continues the booking in chat.
- [x] Configure whether final browser voice silence at the Polish welcome keeps the welcome open in chat, and preview the result in the simulator.
- [x] Configure optional model parser fallback for browser arrival, departure, and guest-count replies.
- [x] Configure whether reviewed parser corrections apply to browser arrival, departure, and guest-count replies, including when the Polish welcome uses the arrival parser.
- [ ] Extend editing to other parser settings, additional validation messages, allowed branches, timeout actions, and delivery/handover behavior.
- [ ] Permit adding/reordering optional steps and routing through approved actions. Keep availability, price calculation, required consent and atomic booking creation as enforced server invariants. Forbid arbitrary Python, SQL or unvalidated external actions in the editor.
- [x] Provide a read-only admin flow overview of the saved or effective inherited graph and its validation findings.
- [x] Preview one bilingual browser arrival reply in the flow overview using built-in parsing, effective transitions and rendered chat/voice wording.
- [x] Simulate a bilingual departure reply using a fictional arrival date, including stay durations and departure retries.
- [x] Simulate bilingual guest-count replies and room-offer outcomes with fictional inventory.
- [x] Simulate bilingual room selection and quote decisions using fictional offers.
- [x] Simulate bilingual delivery choice and email or phone entry up to contact review.
- [x] Simulate email and SMS contact review decisions and the initial receipt with a fictional booking reference.
- [x] Simulate email and SMS receipt polling outcomes with fictional delivery statuses and no provider calls.
- [x] Simulate Polish welcome timeout and bilingual arrival timeout, including the final voice window and **Continue in chat**.
- [x] Simulate a reply with prior consecutive retries and preview retry-limit handover without opening a staff conversation.
- [x] Preview fictional reviewed parser corrections for arrival, departure and guest count without accessing saved samples.
- [x] Preview fictional model results for arrival, departure and guest count using the scenario's fallback settings without calling a model provider.
- [x] Simulate Polish welcome replies, including a single arrival date, a complete stay range and the switch to English.
- [ ] Extend the simulator to additional steps and live parser parity. Validate missing placeholders, unreachable states, loops, required contact/consent paths and terminal states before publishing.
- [x] Record the selected scenario version and entry channel on new web conversations. Preserve the pin across same-language turns, retries, and later publication or rollback. See [web conversation pinning](scenario-version-records.md#web-conversation-pinning).
- [x] Render supported web prompts from the pinned version, with built-in fallbacks and compatibility for unpinned conversations. See [pinned web prompts](scenario-version-records.md#pinned-web-prompts).
- [x] Use the pinned version for approved browser transition dispatch, from welcome through contact confirmation and receipt. Preserve the booking engine’s consent and booking guarantees. See [pinned browser transitions](scenario-version-records.md#pinned-browser-transitions).

**Acceptance:** staff can configure every scenario state in both channels/languages; new sessions use a published version while active bookings finish consistently; simulator and real conversation agree; no edited branch can bypass confirmation or create duplicate/overlapping bookings.

## 0.7 — Separate experiences and premium blue design

- [ ] Add dedicated `/voice/` and `/chat/` views with clear navigation and a shared booking draft. Switching view preserves progress and releases microphone resources. Keep the selected language consistent across views.
- [ ] Voice view: prominent start/stop control, microphone status, visible transcript, accessible captions, retry/edit-last-answer action, compact booking summary and receptionist handover. Chat view: conversation history, contextual answer buttons, date picker and editable booking summary.
- [ ] Introduce a navy/deep-blue primary palette, restrained blue accents, warm neutral backgrounds, generous spacing, refined typography and real licensed hotel photography. Create reusable tokens and layouts for reception, rooms, booking, login and account pages. Aim for a five-star hospitality feel without asserting an unverified hotel star rating.
- [ ] Start with a lightweight CSS/SVG sphere animated from actual audio playback state and amplitude. Give listening, processing, speaking and disconnected states distinct appearances. For browser-native speech fallback, use speech start/end events when amplitude is unavailable. Stop animation on cancellation and support reduced motion. Avoid a generated video avatar on this hardware; an optional prebuilt avatar can follow if measured cost is acceptable.
- [ ] Design mobile layouts, keyboard/focus behavior, screen-reader status updates and accessible contrast. Optimize images and fonts; pause animation in hidden tabs.

**Acceptance:** complete Polish/English bookings in both views, seamless channel switching without duplicate turns, microphone access only after user action, captions aligned with playback, reduced-motion behavior and usable performance on the actual 2019 MacBook and mobile screens.

## 0.8 — Google accounts and guest continuity

- [ ] Add public registration/login using Google Identity Services or a maintained Django OIDC integration. First successful Google sign-in creates a guest account; later sign-ins return to it. Password registration is a separate optional scope decision. Guest booking remains available.
- [ ] Configure a stable public hostname, HTTPS and approved Google origins/redirects before launch. Provide logout, session expiry and a clear account creation/privacy explanation.
- [ ] Validate Google tokens on the server, including issuer, audience, expiry and signature; use Google's subject identifier for identity. Protect login state/CSRF and account linking. Google documents server validation and using `sub` as the unique identifier. [Google verification guide](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)
- [ ] Keep public guest accounts distinct from staff/admin access. Connect bookings through verified ownership; do not expose historical bookings merely because someone supplies an email address or reference. Add an ownership challenge for claiming earlier guest bookings.
- [ ] Add account views for upcoming/past stays, confirmation delivery status, language/contact preferences and sign-out. Cancellation/change requests initially go to staff until hotel policies and inventory rules are formalized. Define retention and account deletion behavior for linked records.

**Acceptance:** new/returning Google login works, failure/cancelled login preserves the draft, duplicate account/linking scenarios are handled, guests cannot read another guest's booking, and login cannot grant staff access. Requires a Google project and approved production configuration.

## 0.9 — Improve existing features

| Priority | Improvement | Concrete outcome / acceptance |
| --- | --- | --- |
| P0 | Parser correctness and configuration | Audit partial dates, month abbreviations, same-month rollover, relative expressions, malformed dates and config boundaries in EN/PL. Record false acceptance separately from parse failure. Dates are read back before confirmation. |
| P0 | Parser training workbench | Store relevant non-secret parsing context, display original/effective/corrected result separately, add rule enable/disable and conflict status, and replay a fictional regression corpus before publishing changes. Track correction effectiveness. Keep scoped exact corrections until broader rules demonstrate safety; no automatic fuzzy rewriting of contacts. |
| P0 | Delivery and handover | Configure live SMTP/SMS and transfer destination, add retries and visible failure states, staff assignment/unread counts and response-time metrics. Verify using controlled recipients. |
| P0 | Operations | Add readiness/resource dashboard, structured timings without raw guest text, alerting for failed providers/delivery, scheduled backup and restore verification. |
| P1 | Availability and pricing | Seasonal rates, minimum stays, occupancy/child rules and staff inventory blocks, introduced behind explicit hotel policies. Quote and final booking must use the same validated rules. |
| P1 | Calendars and booking changes | Complete credentialed Calendar verification, reconciliation and conflict reporting. Add policy-controlled modification/cancellation with idempotent inventory updates and delivery. |
| P1 | Staff permissions | Separate reception, scenario editor, parser reviewer and infrastructure operator roles; audit publication and provider changes. |
| P2 | Database scaling | Keep SQLite until measured write contention or deployment topology justifies PostgreSQL. Test migration/restore before changing production storage. |

**Acceptance:** baseline and post-change reports show fewer incorrect field acceptances and fewer abandoned flows; pending provider integrations have real delivery evidence; operational failures are actionable from admin. Define improvement thresholds from release 0.4 measurements.

## 1.0 — Advanced features within the hardware budget

| Candidate | Value | Cost / decision |
| --- | --- | --- |
| Cached spoken prompts | Faster openings and lower CPU use | High priority. Key cache by text, language, voice/model version and settings; avoid persistent guest-specific audio. |
| Voice activity detection and interruption | More natural turn-taking and lower wasted inference | Pilot small local VAD, echo handling and playback cancellation. Promote only after false-interruption checks on the MacBook microphone. |
| Hotel knowledge assistant | Answer check-in times, parking, amenities and policies | Start with staff-maintained bilingual facts and search. Optional small quantized model only if grounded answers and resource benchmarks pass; booking writes stay in validated tools. |
| Conversation handoff summary | Faster human assistance | Start with a deterministic summary of confirmed fields and unresolved questions; low inference cost. |
| Cross-device continuation | Resume a stay draft by secure link/QR | Low compute. Use short-lived opaque tokens and ownership checks; never place contact details in URLs. |
| Funnel analytics | See failed date steps and booking abandonment | Aggregate by step/language/channel/provider; retention-limited and without recording audio. |
| Personalized offers | Relevant available rooms and extras | Use explicit preferences and deterministic availability/rate rules first; no large recommendation model needed. |

Defer real-time generated video avatars, voice cloning, large continuously loaded LLMs and simultaneous model ensembles. They require a new measured budget or explicit external-provider decision. Treat browser-local STT as research until the target browser and MacBook performance are known.

**Production gate:** verified backup recovery, secure guest identity/ownership, measured speech capacity, accessible bilingual booking, reliable staff escalation and live receipt delivery. Phone and Calendar integrations are enabled only after their credentialed acceptance checks. Begin with release 0.4, then deliver the provider catalog and switch controller as the first implementation milestone.
