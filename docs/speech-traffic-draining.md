# Shared speech traffic draining

The optional SQLite traffic gate coordinates outbound speech calls across web processes and the local controller. Four fixed slots cover English/Polish STT/TTS. A short transaction admits each call only when its slot is open and fewer than 32 total claims exist. Existing web/runtime concurrency limits remain in force. Claims contain opaque IDs and language/task only, never guest text or audio.

`drain(slot)` first closes admission durably, then waits up to five seconds for claims to finish. Timeout leaves admission closed. `resume(slot)` reopens only when no claims remain. Other slots continue independently. Control operations must be serialized by the durable lifecycle executor; arbitrary concurrent drain/resume callers are not supported.

## Explicit local setup

The feature is disabled by default. Before enabling it, stop participating web/controller processes, choose a private local filesystem shared by all participants, and initialize a **new** store:

```text
python -m speech_controller.traffic init /absolute/private/speech-traffic.sqlite3
```

Set `HOTEL_SPEECH_TRAFFIC_DB` to that absolute file path in the web environment. Compose passes this setting through; arrange the shared private mount explicitly when a controller is added. The controller uses `TrafficGate(path)` against the same physical store. Initialization refuses existing files. Normal operations open existing stores only; missing, corrupt, locked or incompatible stores fail closed. Never use the guest database, network filesystem, copied stores per worker, or replace the file while participants are active. The file is created with owner-only permissions on POSIX; provision equivalent directory permissions on other platforms.

Closed slots produce the existing localized busy response (HTTP 429), preserving the booking draft and typed chat. Store failures produce HTTP 503. Cached welcome audio performs no upstream call and needs no claim. Unconfigured installations continue using the existing fixed endpoints and limits.

## Uncertain work and recovery

A successful complete HTTP response releases its claim. An upstream 429 also releases it because the trusted runtime refused admission. Other HTTP errors, transport/read failures, oversized responses and incomplete declared bodies retain a claim and close the affected slot. A disconnected client does not prove server inference stopped. Process crashes leave claims behind; there is deliberately no TTL or automatic expiry. A retained claim prevents successful draining and resumption across restarts.

If persisting closure itself fails, the claim remains and still prevents a successful drain. No automatic recovery or force-clear action is exposed. Before any manual offline repair, stop all participating callers/controller, verify the speech workers are quiescent, and reconcile the store and runtime state together. Do not delete a claim simply because it is old. Durable routing/recovery integration remains future work.

This gate tracks calls to the current fixed endpoints; it does not publish provider routes, authenticate controller commands, or by itself authorize stopping containers. Production activation still requires verified shared storage, endpoint/container binding, atomic routing and reconciliation. SQLite transaction deadlines assume a responsive local filesystem and OS; they are not hard real-time guarantees.

Tests exercise real subprocess claims, crash retention, per-slot isolation, bounded draining and web busy/error handling. CI explicitly initializes a disposable store and enables the gate while checking real bilingual Piper/Vosk traffic. This is not a production deployment.
