"""Explicit native Linux controller startup; existing durable stores are required."""
import argparse
from contextlib import closing, contextmanager
import os
from pathlib import Path
import platform
import sqlite3

from .admission import ControllerAdmission
from .backend import DockerLifecycleBackend
from .dispatch import ControlServer
from .docker_control import DockerControls
from .endpoint_binding import BoundReadiness
from .file_lock import exclusive_lock
from .routed_executor import RoutedLifecycleExecutor
from .routing import RoutedTrafficGate
from .runtime_config import RuntimeConfiguration, private_file


class RuntimeServer(ControlServer):
    # Keep route ownership until dispatched operations have finished on shutdown.
    daemon_threads = False


@contextmanager
def open_runtime(config):
    if not isinstance(config, RuntimeConfiguration) or platform.system() != "Linux":
        raise ValueError("Native Linux controller configuration is required.")
    observed = tuple(private_file(path)[1] for path in (config.traffic_path, config.journal_path))
    if observed != config.identities or len(set(observed)) != 2:
        raise ValueError("Controller state files changed.")
    lock_path = config.traffic_path + ".controller.lock"
    if Path(lock_path).exists() or Path(lock_path).is_symlink():
        private_file(lock_path)
    with exclusive_lock(lock_path) as acquired:
        if not acquired:
            raise ValueError("A controller already owns this route store.")
        controls = DockerControls(config.docker_socket, config.daemon_id, config.approvals)
        admission = ControllerAdmission(controls, config.policy)
        traffic = RoutedTrafficGate(config.traffic_path, config.targets)
        readiness = BoundReadiness(controls, config.targets, config.bindings)
        backend = DockerLifecycleBackend(controls, traffic.deployments, admission=admission,
            traffic=traffic, readiness=readiness, reconciliation=admission.verify_reconciliation)
        # The legacy constructor can initialize/migrate journals. Prove this is
        # already a complete existing journal before allowing it to open one.
        uri = Path(config.journal_path).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as db:
            if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
                raise ValueError("An unchanged DELETE-mode journal is required.")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"metadata", "operations", "reconciliations"}:
                raise ValueError("An initialized controller journal is required.")
            limits = db.execute("SELECT journal_limit FROM metadata").fetchall()
            if len(limits) != 1 or type(limits[0][0]) is not int or not 1 <= limits[0][0] <= 128:
                raise ValueError("Invalid controller journal.")
        state = traffic.state
        executor = RoutedLifecycleExecutor(backend, traffic.deployments, dict(state.routes),
            config.journal_path, revision=state.revision, journal_limit=limits[0][0])
        for approval in config.approvals:
            controls.inspect(approval.name)
        yield executor


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start the private native Linux speech controller.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--check", action="store_true", help="Validate without listening or changing workers/routes.")
    args = parser.parse_args(argv)
    try:
        config = RuntimeConfiguration.load(args.config, os.environ.get("HOTEL_SPEECH_CONTROL_TOKEN", ""))
        with open_runtime(config) as executor:
            if args.check:
                print("Controller configuration and existing state validated; no listener started.")
            else:
                with RuntimeServer(config.control, executor) as server:
                    server.serve_forever()
    except KeyboardInterrupt:
        return
    except Exception:
        raise SystemExit("Controller startup unavailable; inspect private configuration and existing state.") from None


if __name__ == "__main__":
    main()
