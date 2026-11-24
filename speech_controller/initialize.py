"""Provision fresh private controller stores offline from an approved manifest."""
import argparse
import os

from .admission import ControllerAdmission
from .backend import DockerLifecycleBackend
from .docker_control import DockerControls
from .endpoint_binding import BoundReadiness
from .lifecycle import SLOTS
from .routed_executor import RoutedLifecycleExecutor
from .routing import RoutedTrafficGate, initialize_routing
from .runtime_config import RuntimeConfiguration, private_new_store


def initialize(config_path, token, *, primary_stt, primary_tts):
    config = RuntimeConfiguration.load_for_initialization(config_path, token)
    primary = {"stt": primary_stt, "tts": primary_tts}
    targets = {target.name: target for target in config.targets}
    for task, name in primary.items():
        target = targets.get(name)
        if target is None or target.task != task or set(target.languages) != {"en", "pl"}:
            raise ValueError("Choose an approved bilingual primary for each task.")
    routes = {slot: primary[slot[1]] for slot in SLOTS}
    controls = verify_stopped_workers(config)
    create_private_stores(config, controls, routes, revision=0)


def verify_stopped_workers(config):
    controls = DockerControls(config.docker_socket, config.daemon_id, config.approvals)
    if len({binding.address for binding in config.bindings}) != len(config.bindings):
        raise ValueError("Approved workers require distinct static addresses.")
    for binding in config.bindings:
        if controls.verify_stopped_endpoint(binding.name, binding.network_id, binding.address) is not True:
            raise ValueError("A stopped worker endpoint could not be verified.")

    return controls


def create_private_stores(config, controls, routes, *, revision):
    admission = ControllerAdmission(controls, config.policy)
    readiness = BoundReadiness(controls, config.targets, config.bindings)
    # Recheck both destinations after Docker calls, then exclusively reserve
    # the journal. Any later failure leaves evidence for offline inspection.
    private_new_store(config.traffic_path)
    journal = private_new_store(config.journal_path)
    descriptor = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    initialize_routing(config.traffic_path, config.targets, routes, revision=revision)
    traffic = RoutedTrafficGate(config.traffic_path, config.targets)
    backend = DockerLifecycleBackend(
        controls, traffic.deployments, admission=admission, traffic=traffic,
        readiness=readiness, reconciliation=admission.verify_reconciliation,
    )
    return RoutedLifecycleExecutor(backend, traffic.deployments, routes, journal, revision=revision)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize the private speech stores offline.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--primary-stt", required=True)
    parser.add_argument("--primary-tts", required=True)
    args = parser.parse_args(argv)
    try:
        initialize(
            args.config, os.environ.get("HOTEL_SPEECH_CONTROL_TOKEN", ""),
            primary_stt=args.primary_stt, primary_tts=args.primary_tts,
        )
    except Exception:
        raise SystemExit("Controller initialization failed. Inspect private configuration and store paths offline.") from None
    print("Private controller stores initialized at revision 0.")


if __name__ == "__main__":
    main()
