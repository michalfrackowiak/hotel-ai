# Prepare the routed speech stores

The offline initializer prepares the revision-zero route store and its controller journal while the new workers are stopped. Run it as the dedicated native Linux controller account with the reviewed private runtime manifest and access to the pinned local Docker daemon. The initializer checks the full approved worker and bridge identities and each configured static address before creating state. Guest activation and the live bilingual probes happen during cutover. [Runtime manifest](speech-controller-startup.md) · [Production rollout](speech-production-rollout.md)

1. Reserve UID/GID `10001:10001` on the host and provision the dedicated account with login disabled. Grant Docker socket access to the controller process and offline operator command. Keep the host socket outside the Django container.
2. Create two owner-private directories with mode `0700`. Set `traffic_path` in the bind-mounted directory and put `journal_path` in the host-only directory. Keep the manifest and credential in private host configuration outside the bind mount. Use canonical absolute paths for both new database files.
3. Create the four routed workers by explicit service name, leaving all four stopped. Inspect them and record the approved full container IDs, image IDs, bridge ID, addresses and limits in the manifest. Supply the same endpoint catalog to Django. [Routed Compose configuration](speech-cutover-config.md)
4. Load the matching token into the initializer process from private environment configuration and run the command below. Keep the token out of arguments and shell history.

```sh
python3 -m speech_controller.initialize \
  --config /absolute/private/controller.json \
  --primary-stt hotel-ai-routed-vosk-primary \
  --primary-tts hotel-ai-routed-piper-primary
python3 -m speech_controller.runtime --config /absolute/private/controller.json --check
```

Both language slots for each task use the selected bilingual primary. The revision-zero traffic slots start open in SQLite. Point the live Django service at this store only after both primaries pass the cutover readiness checks. The SQLite files have mode `0600` and use DELETE journal mode. The journal is bound to the original traffic store inode and the approved route catalog. The startup check inspects state and approvals without opening the controller listener.

Initialization requires two new paths. If it fails after creating a file, keep the files for inspection and verify that no process has used them before preparing another pair of paths. Preserve the original file identity after successful initialization. At cutover, start the primary pair and confirm their live endpoint identity and readiness before starting guest routing.

## ARM64 rehearsal

The 18 November 2026 run initialized revision zero against four stopped disposable workers using the cached ARM64 Piper and Vosk images. The same controller startup command reopened the stores. The fixture recovered the seeded pending request, switched all four language and task slots through revision 4, and served English and Polish speech with both fictional booking drafts intact.

The source archive passed SHA-256 verification (`de702e55db6978feefd101c3769f9bfa154e4cacaaaac4160691bab3cf9939b8`). The run finished in 85 seconds and removed its containers, bridge and temporary source. All 116 production loopback health samples passed, with p95 4.03 ms and maximum 5.49 ms. Production container identities, start times and health matched before and after the run. [ARM64 staging method](speech-runtime-arm64-staging.md)
