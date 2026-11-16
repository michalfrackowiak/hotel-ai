# Off-host database and Django recovery drill

This drill checks whether an online SQLite backup from the ARM64 server can be transferred intact and opened by the Django application on the separate Windows/x86-64 workstation. It is a database/application-layer recovery check, not a full replacement-host or speech-container recovery.

## Data handling

Create the snapshot with SQLite's online backup API using a read-only connection to the production source. Do not copy a live database file directly: committed data can also reside in its WAL. Create the snapshot exclusively with mode 600 and check SQLite integrity and foreign keys. Transfer it over SSH into a private folder outside the repository. On Windows, remove inherited access and grant only the current user and SYSTEM before copying any data. Compare SHA-256 before and after transfer.

Run verification against a newly restored disposable copy, never the production database or the original backup. Use clean local Django configuration, a temporary secret, disabled external integrations and blocked outbound network calls. The verifier must not log contacts, conversations, credentials, references or database contents. Restrict any report to checks and aggregate counts.

After verification, remove the snapshot and restored copy from the workstation and temporary server locations. This removal is ordinary filesystem deletion, not a claim of forensic secure erasure. Preserve existing operational backups. A recovery drill does not substitute for scheduled encrypted off-host backups and tested retention.

## Run the application-layer verifier

With this checkout's dependencies installed, create an empty restore directory inside the same restricted private folder as the transferred backup, then run:

```powershell
.venv/Scripts/python.exe scripts/verify_recovery.py --backup C:/private/snapshot.sqlite3 --restore-dir C:/private/restore
```

The example paths are placeholders; set the private folder's access restrictions before copying data. The verifier creates `restored.sqlite3` exclusively and refuses a nonempty destination. It checks migration compatibility without applying migrations. Its JSON report contains booleans, elapsed verifier time and the number of tables checked; it omits row contents and individual table counts. Keep private input files outside the repository and remove them once the drill is complete. A successful report must also be accompanied by transfer checksum, production-health and cleanup verification.

## Verified result, 16 November 2026

The production online snapshot passed source integrity/foreign-key checks and its SHA-256 matched after SSH transfer. Windows access was restricted before transfer to the current account and SYSTEM. [Sanitized results](offhost-recovery-results.json) record successful integrity and foreign-key checks across 24 tables, matching source/restore row counts, current migrations, Django system checks, English/Polish page and chat starts, and fictional booking creation with idempotent replay. All probe writes rolled back; the original backup checksum remained unchanged.

The local verifier completed in 2.69 seconds. This excludes snapshot creation, transfer, preparation and manual review, so it is not a disaster recovery-time estimate. Two fictional regression tests passed, including execution with optimized Python. Independent review approved the verifier before use. Temporary snapshots and restored copies were deleted from the workstation, server host and application data directory; operational backups remain. Production container IDs matched before/after and all three services remained healthy. No private database is included in the repository.

## Scope and remaining gates

The connected workstation has 8 GiB RAM and had about 645 MiB free at the start of this drill. Its existing Python environment avoids starting the full Docker speech stack under that pressure. Verification covers SQLite consistency, migration compatibility, ORM access, in-process Django request handling and a fictional booking/idempotency transaction that rolls back.

It does not verify Gunicorn/Nginx startup, TLS renewal, actual SMTP/SMS/Calendar credentials, model downloads, speech images on a replacement architecture, DNS cutover, backup scheduling or achievable disaster RTO/RPO. Those remain explicit recovery tasks. Physical microphone testing also remains separate under the [staff protocol](microphone-evaluation.md).
