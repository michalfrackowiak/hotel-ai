# Language-specific speech readiness

The controller readiness callback performs one real inference request against an explicitly configured deployment endpoint for the requested English/Polish STT/TTS slot. Target definitions are immutable local configuration, not URLs supplied by guests or editable admin text.

Piper receives a short fixed fictional sentence in the requested language. The probe requires a successful bounded WAV response with complete mono 16-bit, 22050 Hz audio and a positive duration within its limit. Vosk receives one second of silent 16 kHz mono PCM and must return exactly an empty transcription. Requests contain no guest audio or personal data, and response content is not logged.

Unknown targets, incompatible slots, unsupported languages, busy services, network errors and malformed responses return not-ready. There is one attempt, no automatic retry. Redirects and environment proxies are disabled, response sizes are bounded, and socket inactivity has a timeout. This is not an absolute deadline against a peer that continually sends data.

## Meaning of a pass

The probe verifies that the language-specific inference path responds in the expected format. Silence handling does not measure transcription accuracy. Valid audio does not prove pronunciation quality, correct voice identity or artifact checksums. These checks do not establish production capacity or replace physical microphone evaluation.

The lifecycle backend combines this callback with approved-container identity/running-state checks. Operators must still bind each private endpoint to the approved container; an HTTP response alone does not attest container identity. Keep these endpoints on verified private networking and do not derive them from user input.

CI runs both languages against the real Piper/Vosk services through the internal speech networks. The production controller HTTP API remains read-only; no admin switch action or guest-routing change is introduced. Guest draining, authoritative admission and atomic route publication remain required before enabling live switching.
