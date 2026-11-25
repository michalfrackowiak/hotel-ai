# Hotel-ai roadmap

Each milestone has a development branch, a pull request to `main`, and independent review. Update this checklist and the [handoff](handoff.md) with the implementation PR and verification evidence.

## Next versions

See the [phased roadmap for versions 0.4–1.0](next-versions-roadmap.md) for processing steps, speech-provider switching, full scenario configuration, separate voice/chat views, Google accounts, the blue visual redesign, hardware budgets and acceptance gates. These are planned releases, not implemented features. Start with the hardware and performance baseline in 0.4.

- [x] 0.4 first baseline: inventory accessible server/MacBook hardware, add bounded bilingual synthetic speech tooling, measure warm latency and semantic extraction, and verify isolated SQLite backup/restore. See [results and remaining checks](hardware-speech-baseline.md).
- [ ] 0.4 completion: staging cold-start/concurrent-load/peak-resource checks, physical microphone evaluation, full off-host recovery and any separately used PC/macOS inventory.
- [x] 0.4 replacement-stack rehearsal tooling: verify a fictional SQLite restore, Gunicorn startup and bilingual HTTP speech through a bounded Docker fixture. The Windows rehearsal reached startup before hitting memory pressure. See [results and remaining gates](replacement-stack-rehearsal.md).
- [x] 0.4 routed-state recovery boundary: verify that byte-identical copies of the traffic store and journal are refused before worker activity, even with current file identities in the replacement configuration. See [routed speech state](replacement-stack-rehearsal.md#routed-speech-state-on-a-replacement-filesystem).
- [x] 0.4 routed replacement recovery design: preserve the original audit evidence, validate the captured routes, verify replacement workers, and define the offline preparation and cutover gates. See the [recovery runbook](routed-replacement-recovery.md).
- [x] 0.4 offline source evidence validation: verify the capture hashes and original journal binding, reject unsettled state, and report the saved revision and four routes. See [validator usage](routed-replacement-recovery.md#validate-the-captured-evidence).
- [x] 0.4 offline recovery preparation: verify stopped replacement workers, rebuild a new private store pair at the validated revision, and record recovery provenance. See [offline preparation](routed-replacement-recovery.md#run-offline-preparation).
- [x] 0.4 routed replacement boot rehearsal: verify selected-only boot, bilingual speech, archived audit continuity, and denial of restored switch and recovery requests while dispatch is disabled. See [disposable boot rehearsal](routed-replacement-recovery.md#disposable-boot-rehearsal).
- [x] 0.4 fresh replacement worker rehearsal: prove recovery with newly created and approved worker and bridge identities and retain the original workers for rollback. See [disposable boot rehearsal](routed-replacement-recovery.md#disposable-boot-rehearsal).
- [x] 0.4 restored admin request fencing: bind switch and recovery previews and saved requests to the configured dispatch generation, retaining audit history. See [fencing during recovery](routed-replacement-recovery.md#fence-restored-admin-requests).
- [x] 0.4 disposable dispatch rehearsal: block restored administrative requests with both dispatch flags enabled under a new generation and verify fresh switch dispatch against the restored controller.
- [ ] 0.4 routed recovery operational acceptance: rehearse on a quiet replacement host with freshly approved worker identities, credentialed TLS and DNS checks, and measured recovery. Verify request fencing before re-enabling dispatch.
- [x] 0.4 staging increment: cached-image process readiness, two-request admission, cgroup memory peaks and web-health responsiveness. See [staging results](staging-speech-capacity.md). Sustained mixed workload and microphone evaluation remain open.
- [x] 0.9 numeric date-width audit: align English and Polish direct replies and embedded numeric date fragments with the same ISO and localized component widths.
- [x] 0.9 named-date year audit: preserve explicit years in English and Polish booking replies and reject malformed years before inferring a year.
- [x] 0.9 month abbreviations: recognize common English and Polish month abbreviations in direct and embedded booking dates.
- [x] 0.9 date configuration audit: cover numeric date settings in English and Polish direct replies, embedded dates and stay ranges, and clarify their scope in admin.
- [x] 0.9 checkout year inference: use the recorded arrival to infer the year of a same-month yearless departure in direct and embedded checkout replies.
- [x] 0.9 relative-date boundary audit: keep unsupported fractional offsets from being mistaken for calendar dates in English and Polish booking replies.
- [x] 0.9 date arithmetic boundary: retain the valid arrival when a bilingual stay duration falls outside the supported calendar.
- [x] 0.9 parser training rule controls: allow staff to disable and re-enable exact corrections while retaining the reviewed sample.
- [x] 0.9 parser correction conflicts: show when enabled rules in the same exact match scope disagree and explain how a conflict affects guest replies.
- [x] 0.9 fictional date regression replay: compare a proposed correction with expected English and Polish dates in the private parser preview.
- [x] 0.9 fictional correction effectiveness: summarize baseline and proposed match counts, improvements and regressions in the date replay.
- [x] 0.9 live correction usage: emit a bounded event for a reviewed parser correction that changes a committed booking turn.
- [x] 0.9 parser accuracy rehearsal protocol: compare paused and enabled corrections against independently recorded intent for fictional English and Polish chat and voice replies. See the [rehearsal](parser-accuracy-rehearsal.md).
- [x] 0.9 hosted browser rehearsal: exercise fictional chat replies and synthetic microphone input through Chromium and Vosk on the hosted Checks runner. Report transcript mismatches as indeterminate.
- [x] 0.9 observed English wording rehearsal: check the predefined `to guess` voice correction separately from the original synthetic fixture and verify isolation from chat.
- [ ] 0.9 parser accuracy acceptance: resolve the English voice transcript mismatch and run a physical microphone rehearsal on an isolated staging target. The [hosted results](handoff.md#hosted-browser-parser-rehearsal) cover typed chat and synthetic microphone inputs. Guest-confirmed accuracy remains open.
- [x] Contact safety increment: bilingual email/SMS readback and explicit confirmation before booking, correction and delivery changes, browser typed fallback, shared phone behavior, and revision/idempotency checks. All 149 Django tests and independent bilingual Chromium checks passed. [PR #33](https://github.com/michalfrackowiak/hotel-ai/pull/33) is merged and deployed; live bilingual contact and speech checks passed. See [contact confirmation](contact-confirmation.md).
- [x] 0.4 bounded mixed-load increment: 60-second isolated bilingual speech and booking workload, 20 completed fictional bookings, resource counters, health-abort checks and verified cleanup. See [results](staging-mixed-load.md). Long-term stability, physical microphone, uncached startup and off-host recovery remain open.

- [x] 0.7 reception modes: dedicated bilingual chat and voice pages share the booking draft, preserve the active scenario and language when switching, and release the microphone when the guest leaves. See [handoff and release checks](handoff.md#guest-reception-modes).

- [x] 0.7 speaking animation: voice playback drives a lightweight sphere with distinct listening, processing, speaking and disconnected states. Browser speech uses playback events, and reduced motion keeps status visible without animation. See [handoff and release checks](handoff.md#voice-speaking-animation).

- [x] 0.7 voice answer review: the dedicated voice page lets guests edit, send or repeat a recognized reply before submission, with mobile controls and bilingual status. See [handoff and release checks](handoff.md#voice-answer-review).

- [x] 0.7 booking summary and date picker: dedicated chat and voice pages show the draft stay and quote. Guests can pick dates and return to the date, guest or room prompt to make a change before booking. See [handoff and release checks](handoff.md#booking-summary-and-date-picker).

- [x] 0.7 receptionist handover: a bilingual button on the dedicated voice page stops audio and opens the existing staff text thread. See [handoff and release checks](handoff.md#voice-receptionist-handover).

- [x] 0.8 Google sign-in readiness: assessed the live IP certificate, owned hostname and Google OAuth setup. See [readiness and launch checks](google-guest-signin-readiness.md).

- [x] 0.8 reception session continuity: booking drafts, pending receipts and staff threads can follow browser session rotation. See [migration and verification](handoff.md#reception-session-continuity).

- [x] 0.8 Google guest sign-in backend: opt-in credential verification, guest sessions and booking draft continuity. See [configuration and verification](handoff.md#google-guest-sign-in-backend).

- [x] 0.8 Google guest sign-in controls: optional English and Polish sign-in and sign-out with reception draft continuity. See [browser checks and pilot setup](handoff.md#google-guest-sign-in-controls).

- [x] 0.8 Google guest pilot routing: provide optional, rate-limited guest account routes for the approved HTTPS hostname. See [pilot configuration](google-guest-signin-readiness.md#pilot-proxy-configuration).

- [ ] 0.8 Google guest pilot: verify real bilingual sign-in and anonymous booking on the approved hostname with the hotel Google web client.

## Alpha

- [x] 0.5 production routed speech configuration: validate the opt-in Compose overlay, the preserved booking volume, the private web and worker network, and the ARM64 worker budget. See [cutover configuration](speech-cutover-config.md) and [PR #71](https://github.com/michalfrackowiak/hotel-ai/pull/71).

- [x] 0.5 offline pilot initialization: verify the approved stopped workers and their private endpoints, create the private revision-zero route and journal pair, and validate it through the controller startup path. See [initialization procedure](speech-pilot-initialization.md) and [PR #72](https://github.com/michalfrackowiak/hotel-ai/pull/72).

- [x] 0.5 production pilot preparation: provision the locked controller account, four stopped routed workers, pinned approvals, private revision-zero stores, and rollback inventory on ARM64. See [preparation evidence](speech-pilot-provisioning.md) and [PR #73](https://github.com/michalfrackowiak/hotel-ai/pull/73).

- [x] 0.5 production pilot images: prepare and verify cached routed and rollback Django images and an opt-in Compose image selection. See [image verification](speech-pilot-web-images.md).

- [x] 0.5 production pilot activation: activate during a staffed cutover with a verified booking backup. Check bilingual speech, booking continuity, and SSH-only admin access. See [live cutover evidence](speech-pilot-live.md) and [PR #75](https://github.com/michalfrackowiak/hotel-ai/pull/75).

- [x] 0.5 stopped-process restart rehearsal: preserve controller state and booking drafts, restart only the workers referenced by the stored routes, and check both languages on ARM64. See [restart evidence](speech-pilot-restart.md).

- [x] 0.5 worker boot gate: refuse uncertain state before worker startup and start only the workers selected by the saved routes. Verify the opt-in command with disposable models. See [boot gate and service design](speech-pilot-boot-gate.md).

- [x] 0.5 persistent startup rehearsal: order Django after authenticated controller readiness and verify reboot and failure ordering in a disposable systemd host. See [systemd rehearsal](speech-pilot-systemd.md).

- [x] 0.5 persistent startup installation: install the verified units and transfer ownership of the pinned web container during a staffed production window. Verify live bilingual speech and authenticated admin access. The 19 November 2026 installation retained and verified a fresh SQLite backup, passed authenticated controller readiness, and checked English/Polish Piper and Vosk. A real production reboot then started the controller before web and passed bilingual speech checks. Public HTTPS admin routing was enabled afterward. See [production installation and reboot evidence](speech-pilot-systemd.md#production-reboot-check).

- [x] 0.5 private admin cutover rehearsal: verify loopback Gunicorn, SSH-forwarded login and CSRF, public admin rejection and cleanup on the ARM64 server. Record the tested worker overlap budget. See [rehearsal evidence](speech-private-cutover-check.md).

- [x] 0.5 rollout rehearsal: document private admin connectivity and the cutover sequence, and exercise a verified booking backup and fixed-endpoint rollback on hosted Linux. See [production rollout guide](speech-production-rollout.md). The production activation and persistent startup installation are complete.

- [x] 0.5 ARM64 runtime staging: recovered and switched all four speech slots on the two-core server with cached Piper/Vosk images, preserved both booking drafts and passed 115 production health samples. See [server staging results](speech-runtime-arm64-staging.md). Production activation is now complete; see [installation evidence](speech-pilot-systemd.md#staffed-production-installation).

- [x] 0.5 hosted runtime staging: real Piper/Vosk deployments, native private controller startup, admin recovery and all four switch slots, and bilingual Django speech checks. See [staging scope](speech-runtime-staging.md).

- [x] 0.5 admin provider switches: review and confirm per-slot planned assignments against live routes, with durable requests, same-ID retries and explicit outcomes. See [switching workflow](admin-speech-switch.md).

- [x] 0.5 admin recovery: permission-gated review/save/confirm flow, durable immutable request payloads and explicit same-ID retries after uncertain responses. See [recovery workflow and limits](admin-speech-recovery.md).

- [x] 0.5 live admin status: private controller observations for all four language/task routes, journal disagreements and pending recovery, with bounded authenticated transport and staff permissions. See [status view](admin-speech-status.md).

- [x] 0.5 controller startup: strict private manifest, explicit Linux startup/check command, existing state preservation, lifetime route-store ownership and concrete recovery capacity checks. See [configuration and rollout](speech-controller-startup.md).

- [x] 0.5 endpoint binding: verify approved container identity and private bridge network/address before and after speech readiness; reject mismatched backend catalogs. See [supported topology and limits](speech-endpoint-binding.md). Validated production startup and admin integration remain pending.

- [x] 0.5 private operation dispatch: separate authenticated loopback status/switch/recovery API with strict bounded requests, configured audit actor and durable retries. See [protocol and activation boundary](private-speech-dispatch.md). Production composition, endpoint binding and admin integration remain pending.

- [x] 0.5 routed recovery: explicit verified route adoption, all-slot draining, durable recovery audit before resumption and reverified idempotent retries. See [recovery scope](routed-switch-recovery.md). Authenticated dispatch, admin integration and production activation remain pending.

- [x] 0.5 durable routed coordinator: route publication and durable selected-state checkpoint precede old-worker cleanup and traffic resumption, with pending operations blocking uncertain retries. See [switch ordering](durable-routed-switches.md). Coordinated recovery, authenticated dispatch and production activation remain pending.

- [x] 0.5 atomic route store: approved target/revision selection shares the request-claim transaction, and publication requires a closed, drained slot and exact prior state. See [routing boundary](speech-route-publication.md). Durable lifecycle commit/recovery integration and production activation remain pending.

- [x] 0.5 shared traffic gate: opt-in cross-process speech claims, per-language/task draining, and durable blocking for uncertain requests. See [draining and recovery limits](speech-traffic-draining.md). Fixed endpoints remain; atomic routing and production activation are pending.

- [x] 0.5 controller resource gate: immutable policy and approved container limits checked against fresh host capacity, with all approved running workers included and revalidation after draining. See [admission boundary](speech-controller-admission.md). Guest draining/routing and production activation remain pending.

- [x] 0.5 readiness deadline: isolated one-shot probe workers enforce a total elapsed budget and are terminated/reaped on timeout, including trickling responses. See [deadline boundary](speech-readiness-probes.md#total-probe-deadline). Production admission, draining and routing remain pending.

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
- [x] 0.9 reception inbox first reply: show elapsed time from handover creation to the first staff reply and distinguish open and closed handovers without a reply.
- [x] 0.9 reception inbox filter: find handovers awaiting a first reply, replied handovers, and threads closed without a reply. Combine the filter with language and closure status.
- [x] 0.9 reception inbox assignment: assign or unassign a staff owner in the private admin and filter the inbox by assignee.
- [x] 0.9 reception inbox unread counts: show shared unread guest messages and let authorized staff mark the displayed thread messages as read.
- [x] 0.9 reception inbox unread filter: find handovers with unread guest messages and combine the filter with assignee, language and first response.
- [x] 0.9 reception staff group: provision the permissions to handle handovers and read bookings and confirmations in the private admin.
- [x] 0.9 parser reviewer group: provision private access to inspect parser samples and record corrections.
- [x] 0.9 scenario editor group: provision private access to author scenario drafts while publication and rollback require separate permission.
- [x] Match the Resort Neptun scenario prompts and timing. Open with a bilingual greeting and a two-second language window, wait ten seconds for an arrival date, quote room prices before consent, and use the selected delivery closing after provider acceptance.
- [x] Start English web chat and browser voice with a Neptun Resort greeting followed immediately by Step 2.b.
- [ ] Configure live SMTP, Twilio SMS, and the staff phone transfer destination. Verify each live destination using the [controlled rehearsal](delivery-verification.md).
- [x] 0.9 loopback SMTP verification: submit a fictional confirmation through the real SMTP backend and verify the received message and duplicate suppression.
- [x] 0.9 confirmation submission triage: group failed and uncertain provider submissions in the private admin and explain the recorded statuses.
- [x] 0.9 confirmation dispatch events: record committed submission outcomes by channel and language in the worker log using fixed labels.
- [x] 0.9 delivery configuration check: report per-process SMTP, SMS and staff transfer settings using redacted, read-only output.
- [x] 0.9 confirmation queue summary: count email and SMS submissions by status for the current private admin filters and search.
- [x] 0.9 confirmation claim-age review: find sending attempts claimed at least five minutes ago in the private admin.
- [x] 0.9 confirmation retry design: document which provider response can authorize a bounded retry and which recorded outcomes require investigation. See the [retry policy](delivery-retry-policy.md).
- [x] 0.9 confirmation retry implementation: persist and verify bounded retries for newly observed Twilio 429 responses with error code 20429. The worker flag defaults off.
- [x] 0.9 confirmation retry restart verification: exercise the worker across three separate processes on disposable SQLite with a simulated provider.
- [ ] 0.9 live SMS retry acceptance: verify receipt with a controlled staging recipient before enabling the retry flag in production.
- [x] 0.9 scripted delivery preflight: fail the read-only configuration check when a channel required for staging is unconfigured.
- [x] Flexible stay dates. Extract arrival and departure from one English or Polish message, including a spoken arrival date and stay duration amid extra words.
- [x] Retire browser speech training. Remove opt-in audio capture, sample storage, and the admin transcription/export view while keeping live voice transcription.
- [x] Voice arrival prompts. Give English callers the address and a two-second pause, then offer two five-second listening windows in English and Polish.
- [x] Admin workflows. Add permission-aware shortcuts, reservation timing filters, a room-level switch for accepting new bookings, and a bilingual preview of saved date parser settings.

- [x] Booking scenario editor. Edit bilingual chat text, voice scripts, and pauses in the private admin.
- [x] Parser training. Review bilingual chat and voice inputs and their parsed values, then reuse validated staff corrections.
- [x] Synchronize voice and chat. Display spoken segments as playback begins, show guest transcripts, and offer contextual reply buttons.
- [x] Expand parser configuration and bilingual stay dates. Configure accepted numeric formats in admin, parse check-in and check-out dates from one sentence, and retain text-based parser review and corrections.

## Later releases

- [x] Implement the inbound phone connector, including English and Polish booking flows, signed callbacks, and retry-safe confirmation.
- [ ] Route the dedicated mobile number to the connector and place live calls in both languages. This needs a Twilio number or SIP domain, a public HTTPS endpoint, and a carrier forwarding or cellular gateway route.
- [ ] Add integrations with hotel booking platforms as needed.
