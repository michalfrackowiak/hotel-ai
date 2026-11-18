# Routed speech restart rehearsal

The disposable runtime drill simulates the process state after a host reboot. It closes the fixture Django and controller processes, stops the selected speech workers, and verifies that all approved workers are stopped. The original route and journal files stay in place. The drill reads them to reject changed routes, closed admission slots, retained claims or pending operations before resuming service.

The fixture had already moved all four language and task routes to the standby pair at revision 4. It restarts only the workers selected by the stored routes. Both models must pass bound inference in English and Polish before the controller and Django reopen. Django verifies the original bilingual session drafts, checks controller and audit state, and exercises Piper WAV and Vosk PCM through those restored sessions. Hosted CI also runs the fixed-endpoint rollback after the restart.

The ARM64 mode for the live routed topology pins the running web and primary worker caps and compares all seven production containers before and after the drill. It uses disposable resources with caps of 0.25 CPU per fixture speech worker and 0.2 CPU for fixture Django. With one overlapping replacement, the total configured cap for the production and fixture web and speech containers is 2 CPUs. The production loopback health monitor aborts the fixture on failure.

```sh
python3 scripts/smoke_speech_runtime.py --arm64-server --routed-live --source "$PWD"
```

Use the reviewed source in a private temporary archive directory. This rehearsal simulates stopped processes and existing stores. An actual server reboot, abrupt power loss and automatic startup require separate checks. [ARM64 staging method](speech-runtime-arm64-staging.md)

## ARM64 evidence

The 18 November 2026 run completed in 151 seconds. The server verified source archive SHA-256 `e3458e42c58ae638a4cb41c0d8e317b0d84da8656faa7e0c81288d9ffffc9bac`. All four fixture workers stopped. Only the workers selected at revision 4 restarted, and Django reopened the original bilingual drafts and audit history. The fixture stores retained their identities and the journal checksum matched. Cleanup removed the disposable resources.

All 201 production loopback health samples passed, with p95 49.8 ms and maximum 73.93 ms. The production controller process and seven container identities, states and start times matched. The final code tightens the test for an interrupted reconciliation checkpoint. Independent tests verified the refusal for that case, four other uncertain-state cases, and production CPU cap drift.
