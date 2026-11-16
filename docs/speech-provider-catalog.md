# Speech provider catalog

The private Django admin contains a catalog for recording speech-provider candidates. It is the first part of version 0.5. Saving catalog records does not select a live provider, start or stop containers, download models, change speech endpoints or alter booking behavior. The deployed Piper/Vosk configuration remains controlled by Compose.

Record each provider's task (speech-to-text or text-to-speech), engine, supported English/Polish languages, architecture, adapter version, image reference and CPU/memory budget. Add model or voice entries for the supported languages, with artifact checksums and licensing notes. A bilingual deployment can describe both languages under one provider; STT and TTS remain separate task records.

Incomplete candidates can be documented before an image or model checksum is known. When supplied, the image must be pinned using a repository reference with `@sha256:` and a 64-character hexadecimal digest; a mutable tag alone is insufficient. Artifact checksums use a 64-character SHA-256 digest. Resource estimates are planning metadata, not enforced Docker limits or measurements. License text records staff research and does not constitute automatic approval.

The catalog starts empty so it cannot imply that an unverified candidate is installed or approved. Enter image digests and model metadata from the artifacts actually evaluated. Existing benchmark reports remain the evidence for current runtime behavior. Do not enter credentials or guest data into names, references or notes.

Open **Model providers → Speech providers** in admin. Model/voice entries are edited inline. Each entry must use a language selected for its provider. Delete incompatible model/voice entries and save before removing a supported provider language; this also protects records hidden from staff with limited permissions.

## Next slices

Separate deployment records and a unique language/task selection matrix will distinguish catalog candidates from running instances. A later private controller must validate image allowlists and resource budgets, serialize switches, preserve shared-provider ownership, check readiness and roll back failures. The public web process must not receive a Docker socket. Until that workflow is implemented and verified, this catalog offers no activation action.

This additive catalog migration does not complete the outstanding 0.4 physical-microphone or replacement-host recovery gates, or the full 0.5 switching acceptance criteria.
