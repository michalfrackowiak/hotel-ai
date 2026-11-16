# Controller host resource observations

The inventory snapshot can include Linux resource observations from the machine running the controller. The private admin displays CPU count, physical total/available memory and total/free space on an explicitly configured storage filesystem. These are transient observations, not resource reservations or admission decisions.

Memory comes from Linux `/proc/meminfo` (`MemTotal` and `MemAvailable`). Missing, malformed or unsupported measurements remain unknown. Other operating systems currently report unsupported observations rather than approximating Linux metrics. CPU count is an operating-system count, not measured idle capacity, and physical memory does not describe container cgroup limits.

Set `HOTEL_SPEECH_CONTROLLER_STORAGE_PATH` in the controller's private environment to an absolute path on the filesystem used for speech images/models. An empty setting leaves disk observations unknown. No path is selected automatically: `/` may not be the filesystem storing Docker images or model data. A single filesystem observation cannot cover model files on additional mounts. Errors leave disk values unknown; filesystem paths are not returned to Django or shown in admin.

These observations describe the **controller host**. If the Docker CLI uses a remote daemon or a Docker Desktop VM, they do not establish the daemon's capacity. Operators must run the controller on the intended Linux Docker host and choose the relevant storage filesystem before using its observations as operational evidence. This increment does not verify that deployment topology.

The inventory field is additive. An older controller response without resource data remains readable, with unknown resource observations. If resource data is present, the client validates its schema, nonnegative finite values and available/free amounts against their totals. Stale snapshots are rejected through the existing freshness check.

Live switching still requires explicit resource reserves, required artifact sizes, verified daemon/topology identity, serialized admission, model readiness, draining and rollback. The transition-budget preview continues to use configured allocations; displaying physical resource observations does not turn that preview into an execution permit.
