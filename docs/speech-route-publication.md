# Atomic speech route publication

An optional version-2 traffic store holds the four English/Polish STT/TTS routes alongside admission slots and claims. Each outbound request selects a target and inserts its claim in one SQLite transaction. Its selected endpoint and revision remain fixed for that call. A route cannot change while that slot still has an active or uncertain claim.

`RoutedTrafficGate.publish(current, destination)` compares the exact current revision and all four routes, requires the next revision to increase by one, and permits exactly one compatible slot change. That slot must already be closed and have no claims. The route and revision update atomically; admission stays closed until explicit resumption. Stale publication, incompatible targets, multi-slot changes, missing/corrupt stores and configuration drift fail closed.

## Local configuration

Set `HOTEL_SPEECH_ROUTE_TARGETS` to a private JSON list of one to eight approved endpoint definitions. Each entry has exactly `name`, `task` (`stt` or `tts`), `base_url` and `languages` (`en` and/or `pl`). Names must be unique. URLs must be HTTP/HTTPS origins without credentials, query, fragment or non-root path. Endpoints are trusted local operator configuration, never guest input or editable scenario text.

For example, a private two-container configuration is:

```json
[
  {"name":"piper","task":"tts","base_url":"http://piper:5000","languages":["en","pl"]},
  {"name":"asr","task":"stt","base_url":"http://asr:5001","languages":["en","pl"]}
]
```

Routing also requires `HOTEL_SPEECH_TRAFFIC_DB` pointing to the same private physical store in every participating process. Initialize a new file offline through `initialize_routing(path, load_targets(raw_json), routes)`, where `routes` maps all four `(language, task)` tuples to approved names. There is no automatic migration, replacement or reset of an existing file. Follow the [shared traffic setup and recovery rules](speech-traffic-draining.md).

The store records target names and a fingerprint of the complete local catalog, including endpoints and supported languages. All participants must agree on that catalog. Changes require planned offline migration and reconciliation, not silently editing the environment. A plain version-1 gate rejects a routed store, and the routed gate rejects a version-1 store. Removing routing configuration therefore cannot silently fall back through an existing routed store to fixed endpoints.

With routing enabled, web speech uses the endpoint selected by the claim even when fixed Piper/Vosk URLs are empty. Invalid routing configuration never falls back to those fixed URLs. Welcome audio bypasses its process cache so a provider change cannot replay old-provider audio. Without routing configuration, existing fixed-endpoint behavior remains available with the version-1 gate or no gate.

## Lifecycle boundary

This change supplies transactional route storage and dispatch, not a live admin switch button. Publication is an internal primitive requiring serialized controller ownership, approved container/endpoint binding, capacity checks, readiness and draining. It does not start or stop a container, authenticate an operator, or prove model readiness.

The [durable routed coordinator](durable-routed-switches.md) publishes routes and checkpoints the selected state before cleanup or resumption. The legacy lifecycle sequence is unsuitable for routed stores. Coordinated operator recovery and authenticated dispatch remain required before enabling production switching. A successful publication alone must not trigger resumption or old-worker cleanup from an ad hoc script.

CI initializes a new routed store with private Piper/Vosk targets and exercises real bilingual web speech. Tests cover publication guards, cross-process route visibility, pinned claims, uncertain work, catalog drift, and web no-fallback behavior. No production deployment is included.
