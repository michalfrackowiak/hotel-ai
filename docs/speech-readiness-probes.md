# Language-specific speech readiness

The controller readiness callback performs one real inference request against an explicitly configured deployment endpoint for the requested English/Polish STT/TTS slot. Target definitions are immutable local configuration, not URLs supplied by guests or editable admin text.

Piper receives a short fixed fictional sentence in the requested language. The probe requires a successful bounded WAV response with complete mono 16-bit, 22050 Hz audio and a positive duration within its limit. Vosk receives one second of silent 16 kHz mono PCM and must return exactly an empty transcription. Requests contain no guest audio or personal data, and response content is not logged.

Unknown targets, incompatible slots, unsupported languages, busy services, ambiguous network errors and malformed responses return not-ready. A cold private HTTP listener can refuse connections while models load. The probe retries those connection attempts within its existing elapsed budget, before sending any inference bytes. It sends at most one inference POST. Redirects and environment proxies are disabled and response sizes are bounded.

## Total probe deadline

Each approved probe runs in a short-lived Python worker. The parent applies an eight-second elapsed-time budget, including time spent resolving the endpoint, connecting, negotiating TLS, receiving headers/body and validating the response. A peer cannot extend that budget by trickling bytes. On timeout the parent terminates and reaps the worker before returning not-ready; no background probe thread is left running. Unknown targets and incompatible slots are rejected before launching a worker.

The worker receives only the approved endpoint and synthetic probe configuration through standard input, emits no response content, and communicates success through its exit status. Worker launch errors or unsuccessful completion return not-ready. The existing CI smoke command uses this same deadline path for all four language/task combinations.

This is an application deadline, not a hard real-time guarantee: operating-system process creation, scheduling and termination/reaping can add overhead. The parent accounts for launch time when setting the remaining wait budget. Cancelling the local HTTP worker also does not guarantee cancellation of inference already accepted by the speech server; normal server admission limits still apply.

## Meaning of a pass

The probe verifies that the language-specific inference path responds in the expected format. Silence handling does not measure transcription accuracy. Valid audio does not prove pronunciation quality, correct voice identity or artifact checksums. These checks do not establish production capacity or replace physical microphone evaluation.

The lifecycle backend combines this callback with approved-container identity/running-state checks. Operators must still bind each private endpoint to the approved container; an HTTP response alone does not attest container identity. Keep these endpoints on verified private networking and do not derive them from user input.

CI runs both languages against the real Piper/Vosk services through the internal speech networks. The production controller HTTP API remains read-only; no admin switch action or guest-routing change is introduced. Guest draining, authoritative admission and atomic route publication remain required before enabling live switching.
