"""Prepare stopped routed speech resources on the native hotel server."""

import argparse
from contextlib import closing
import grp
from ipaddress import IPv4Network, ip_network
import json
import os
from pathlib import Path
import platform
import pwd
import re
import secrets
import socket
import stat
import subprocess
import sys
from urllib.parse import urlsplit

from arm64_stage import FixtureError, ProductionHealth, production_snapshot, require

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from speech_controller.resources import host_resources

ACCOUNT = "hotel-ai-speech"
OWNER_ID = 10001
PROJECT = "hotel-ai"
BASE = Path("/var/lib/hotel-ai-speech")
RELEASES = Path("/opt/hotel-ai-speech")
SOCKET = Path("/run/docker.sock")
PORT = 8093
DOCKER = ("/usr/bin/docker", "--host", f"unix://{SOCKET}")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C", "PYTHONNOUSERSITE": "1"}
FILES = ("compose.yaml", "compose.server.yaml", "compose.speech-routed.yaml")
BRIDGE = f"{PROJECT}_routed-speech"
VOLUME = f"{PROJECT}_hotel-data"
GIB = 1024 ** 3
RESERVE = 3 * GIB
SPECS = (
    ("piper", "primary", "tts", 5000, GIB),
    ("vosk", "primary", "stt", 5001, 3 * GIB // 2),
    ("piper", "standby", "tts", 5000, GIB),
    ("vosk", "standby", "stt", 5001, 3 * GIB // 2),
)
SERVICES = tuple(f"routed-{model}-{role}" for model, role, *_ in SPECS)
CONTAINERS = tuple(f"{PROJECT}-{service}" for service in SERVICES)
PRIVATE_RANGES = tuple(map(IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
CONTAINER_ID = re.compile(r"[a-f0-9]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")


def command(label, *arguments, env=None, timeout=20):
    try:
        result = subprocess.run(
            arguments, cwd=ROOT, env=ENV if env is None else env,
            capture_output=True, text=True, check=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise FixtureError(f"{label} failed.") from None
    return result.stdout.strip()


class LocalDocker:
    def run(self, *arguments):
        return command("Local Docker inspection", *DOCKER, *arguments)

    def project(self, *arguments):
        return json.loads(self.run(*arguments))


def private_subnet(value):
    try:
        subnet = ip_network(value, strict=True)
        if (isinstance(subnet, IPv4Network) and subnet.prefixlen == 28
                and any(subnet.subnet_of(private) for private in PRIVATE_RANGES)):
            return subnet
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Supply a canonical RFC1918 IPv4 /28 subnet.")


def verify_release():
    require(platform.system() == "Linux" and platform.machine() == "aarch64" and os.geteuid() == 0,
            "Provisioning requires root on the native ARM64 server.")
    require(ROOT.parent == RELEASES and re.fullmatch(r"[a-f0-9]{40}", ROOT.name),
            "Run from a reviewed commit directory under the pilot release root.")
    directories = (*ROOT.parents, ROOT, ROOT / "scripts", ROOT / "speech_controller")
    files = [Path(__file__), ROOT / "scripts" / "arm64_stage.py", *(ROOT / name for name in FILES),
             *(ROOT / "speech_controller").glob("*.py")]
    for path in (*directories, *files):
        info = path.lstat()
        required = 0o005 if path in directories else 0o004
        kind = stat.S_ISDIR if path in directories else stat.S_ISREG
        require(kind(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and info.st_mode & required == required,
                "Release paths require root ownership and read access for the controller account.")


def absent_account(lookup, value):
    try:
        lookup(value)
    except KeyError:
        return
    raise FixtureError("The proposed controller account or numeric identity already exists.")


def verify_no_conflicts(docker, subnet, *, resume_identity):
    for path in BASE.parents:
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                "Pilot state requires a trusted root-owned parent path.")
    if resume_identity:
        verify_resume_identity()
    else:
        require(not os.path.lexists(BASE), "Pilot state already exists.")
        for lookup, value in ((pwd.getpwnam, ACCOUNT), (pwd.getpwuid, OWNER_ID),
                              (grp.getgrnam, ACCOUNT), (grp.getgrgid, OWNER_ID)):
            absent_account(lookup, value)
    docker_group = grp.getgrnam("docker")
    sock = SOCKET.lstat()
    require(stat.S_ISSOCK(sock.st_mode) and sock.st_uid == 0 and sock.st_gid == docker_group.gr_gid
            and stat.S_IMODE(sock.st_mode) == 0o660 and SOCKET.resolve() == SOCKET,
            "Expected a canonical private local Docker socket.")
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as listener:
        try:
            listener.bind(("127.0.0.1", PORT))
        except OSError:
            raise FixtureError("The controller loopback port is in use.") from None
    names = docker.run("container", "ls", "--all", "--format", "{{.Names}}").splitlines()
    require(not set(CONTAINERS).intersection(names), "A routed worker name already exists.")
    routes = json.loads(command("IPv4 route inspection", "ip", "-j", "-4", "route", "show", "table", "all"))
    occupied = [ip_network(route["dst"], strict=False) for route in routes if route.get("dst", "default") != "default"]
    for network_id in docker.run("network", "ls", "--quiet", "--no-trunc").splitlines():
        require(CONTAINER_ID.fullmatch(network_id), "Invalid Docker network identity.")
        network = docker.project("network", "inspect", "--format",
                                 '{"name":{{json .Name}},"subnets":{{json .IPAM.Config}}}', network_id)
        require(network["name"] != BRIDGE, "The routed bridge already exists.")
        occupied.extend(ip_network(entry["Subnet"], strict=False)
                        for entry in network["subnets"] or () if entry.get("Subnet"))
    require(not any(other.version == 4 and subnet.overlaps(other) for other in occupied),
            "The proposed routed subnet overlaps a host or Docker network.")


def rollback_snapshot(docker):
    services = production_snapshot(docker)
    web = services["web"]["id"]
    configuration = docker.project(
        "inspect", "--type", "container", "--format",
        '{"working_dir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},'
        '"files":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},'
        '"booking_mounts":[{{range .Mounts}}{{if eq .Destination "/app/data"}}'
        '{"type":{{json .Type}},"name":{{json .Name}},"rw":{{json .RW}}}{{end}}{{end}}]}',
        web,
    )
    files = configuration["files"].split(",")
    require(Path(configuration["working_dir"]).is_absolute() and files
            and all(Path(path).is_absolute() and Path(path).is_file() for path in files)
            and configuration["booking_mounts"] == [{"type": "volume", "name": VOLUME, "rw": True}],
            "The fixed Compose files or booking mount differ.")
    volume = docker.project("volume", "inspect", "--format",
                            '{"name":{{json .Name}},"driver":{{json .Driver}},"path":{{json .Mountpoint}},'
                            '"created":{{json .CreatedAt}}}', VOLUME)
    require(volume["name"] == VOLUME and volume["driver"] == "local"
            and Path(volume["path"]).is_absolute() and volume["created"], "The booking volume differs.")
    return {"services": services, "fixed_project": configuration, "booking_volume": volume}


def host_capacity(docker, before):
    info = docker.project("info", "--format", '{"id":{{json .ID}},"os":{{json .OSType}},'
                          '"system":{{json .OperatingSystem}},"storage":{{json .DockerRootDir}}}')
    require(info["os"] == "linux" and "docker desktop" not in info["system"].lower()
            and re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", info["id"])
            and Path(info["storage"]).is_absolute(), "The Docker daemon is not native Linux.")
    images, sizes = {}, {}
    for kind, service in (("piper", "piper"), ("vosk", "asr")):
        image_id = before["services"][service]["image"]
        record = docker.project("image", "inspect", "--format",
                                '{"id":{{json .Id}},"os":{{json .Os}},"arch":{{json .Architecture}},'
                                '"bytes":{{json .Size}}}', image_id)
        require(record["id"] == image_id and IMAGE_ID.fullmatch(image_id) and record["os"] == "linux"
                and record["arch"] == "arm64" and type(record["bytes"]) is int and record["bytes"] > 0,
                "A fixed worker image is not a cached native ARM64 image.")
        images[kind], sizes[image_id] = image_id, record["bytes"]
    resources = host_resources(info["storage"])
    require((resources["cpu_count"] or 0) >= 2
            and (resources["memory_available_mib"] or 0) * 1024 ** 2 >= 4.5 * GIB + RESERVE
            and (resources["disk_free_mib"] or 0) * 1024 ** 2 >= RESERVE + sum(sizes.values()),
            "Host resources are below the rehearsed pilot budget.")
    return info, images, sizes


def verify_identity():
    account = pwd.getpwnam(ACCOUNT)
    group = grp.getgrnam(ACCOUNT)
    password = command("Controller account lock validation", "passwd", "--status", ACCOUNT).split()
    require(account.pw_uid == OWNER_ID and account.pw_gid == OWNER_ID and account.pw_shell == "/usr/sbin/nologin"
            and account.pw_dir == str(BASE / "state") and group.gr_gid == OWNER_ID and not group.gr_mem
            and pwd.getpwuid(OWNER_ID).pw_name == grp.getgrgid(OWNER_ID).gr_name == ACCOUNT
            and password[:2] == [ACCOUNT, "L"] and os.getgrouplist(ACCOUNT, OWNER_ID) == [OWNER_ID],
            "The controller identity differs from the locked pilot account.")


def verify_resume_identity():
    operator = BASE / "operator"
    for path, mode in ((BASE, 0o755), (operator, 0o700)):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == mode
                and info.st_uid == info.st_gid == 0, "The partial pilot directory permissions differ.")
    require(set(BASE.iterdir()) == {operator} and not any(operator.iterdir()),
            "The partial pilot contains initialized or unexpected state.")
    verify_identity()


def reserve_paths(*, resume_identity):
    operator = BASE / "operator"
    if resume_identity:
        verify_resume_identity()
    else:
        BASE.mkdir(mode=0o755)
        BASE.chmod(0o755)
        operator.mkdir(mode=0o700)
        command("Controller group creation", "groupadd", "--gid", str(OWNER_ID), ACCOUNT)
        command("Controller account creation", "useradd", "--uid", str(OWNER_ID), "--gid", ACCOUNT,
                "--home-dir", str(BASE / "state"), "--no-create-home", "--no-log-init",
                "--shell", "/usr/sbin/nologin", ACCOUNT)
        verify_identity()
    for name in ("traffic", "state"):
        path = BASE / name
        # Exclusive traffic-directory creation also serializes explicit identity resumption.
        path.mkdir(mode=0o700)
        os.chown(path, OWNER_ID, OWNER_ID)
    return operator


def write_private(path, value, *, owner=0):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        os.fchown(output.fileno(), owner, owner)
        output.write(value)
        output.flush()
        os.fsync(output.fileno())


def routed_environment(subnet, images, token):
    targets, env = [], {
        "HOTEL_ROUTED_PIPER_IMAGE": images["piper"],
        "HOTEL_ROUTED_VOSK_IMAGE": images["vosk"],
        "HOTEL_ROUTED_SPEECH_SUBNET": str(subnet),
        "HOTEL_ROUTED_TRAFFIC_DIR": str(BASE / "traffic"),
        "HOTEL_SPEECH_CONTROL_TOKEN": token,
        "HOTEL_SPEECH_CONTROL_HOST": PROJECT,
    }
    for offset, (model, role, task, port, _), name in zip(range(2, 6), SPECS, CONTAINERS, strict=True):
        address = str(subnet.network_address + offset)
        env[f"HOTEL_ROUTED_{model.upper()}_{role.upper()}_IP"] = address
        targets.append({"name": name, "task": task, "base_url": f"http://{address}:{port}", "languages": ["en", "pl"]})
    env["HOTEL_SPEECH_ROUTE_TARGETS"] = json.dumps(targets, separators=(",", ":"))
    return env, targets


def compose_command(env_file):
    return (*DOCKER, "compose", "--project-name", PROJECT, "--env-file", str(env_file),
            *(arg for path in FILES for arg in ("-f", path)),
            "--profile", "routed-primary", "--profile", "routed-standby")


def verify_compose(routed_env, images, subnet, offline_env):
    rendered = json.loads(command("Routed Compose config", *compose_command("/dev/null"),
                                  "config", "--format", "json", env=offline_env | routed_env))
    require(set(rendered["services"]) == {"web", *SERVICES}
            and rendered["networks"]["routed-speech"]["name"] == BRIDGE
            and rendered["networks"]["routed-speech"]["internal"] is True
            and rendered["networks"]["routed-speech"]["ipam"]["config"] == [{"subnet": str(subnet)}],
            "The routed Compose services or bridge do not match the pilot.")
    for service, name, (model, role, _, _, memory) in zip(SERVICES, CONTAINERS, SPECS, strict=True):
        spec = rendered["services"][service]
        address = routed_env[f"HOTEL_ROUTED_{model.upper()}_{role.upper()}_IP"]
        require(spec["container_name"] == name and spec["image"] == images[model]
                and spec["pull_policy"] == "never" and spec["restart"] == "no"
                and spec["read_only"] is True and spec["user"] == "10001:10001"
                and spec["cpus"] == 0.4 and int(spec["mem_limit"]) == int(spec["memswap_limit"]) == memory
                and spec["cap_drop"] == ["ALL"] and spec["security_opt"] == ["no-new-privileges"]
                and spec["networks"] == {"routed-speech": {"ipv4_address": address}}
                and spec["profiles"] == [f"routed-{role}"]
                and all(not spec.get(key) for key in ("depends_on", "build", "volumes", "ports", "privileged")),
                "The routed Compose worker is not isolated from the active stack.")


def manifest(docker, info, images, sizes, targets):
    bridge_id = docker.run("network", "inspect", "--format", "{{.Id}}", BRIDGE)
    require(CONTAINER_ID.fullmatch(bridge_id), "The new bridge identity is invalid.")
    deployments = []
    for service, (model, _, _, _, memory), target in zip(SERVICES, SPECS, targets, strict=True):
        worker = docker.project(
            "inspect", "--type", "container", "--format",
            '{"id":{{json .Id}},"state":{{json .State.Status}},"user":{{json .Config.User}},'
            '"root_readonly":{{json .HostConfig.ReadonlyRootfs}},"swap":{{json .HostConfig.MemorySwap}},'
            '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
            '"service":{{json (index .Config.Labels "com.docker.compose.service")}}}',
            target["name"],
        )
        require(CONTAINER_ID.fullmatch(worker["id"]) and worker["state"] == "created"
                and worker["user"] == "10001:10001" and worker["root_readonly"] is True
                and worker["swap"] == memory and worker["project"] == PROJECT and worker["service"] == service,
                "A new routed worker's stopped configuration differs from the pilot.")
        deployments.append(target | {
            "container_id": worker["id"], "image_id": images[model], "nano_cpus": 400_000_000,
            "memory_bytes": memory, "owner": "hotel-ai-routed", "network_id": bridge_id,
            "address": urlsplit(target["base_url"]).hostname,
        })
    return {
        "version": 1, "actor": PROJECT, "port": PORT, "docker_socket": str(SOCKET), "daemon_id": info["id"],
        "traffic_path": str(BASE / "traffic" / "traffic.sqlite3"),
        "journal_path": str(BASE / "state" / "controller.sqlite3"),
        "policy": {
            "storage_path": info["storage"], "topology_confirmed": True, "cpu_budget_nanos": 1_200_000_000,
            "memory_budget_bytes": 4 * GIB, "memory_reserve_bytes": RESERVE, "disk_reserve_bytes": RESERVE,
            "artifact_bytes": sorted(sizes.items()),
        },
        "deployments": deployments,
    }


def prepare(subnet, *, resume_identity=False):
    verify_release()
    docker = LocalDocker()
    verify_no_conflicts(docker, subnet, resume_identity=resume_identity)
    before = rollback_snapshot(docker)
    info, images, sizes = host_capacity(docker, before)
    token = secrets.token_hex(32)
    routed_env, targets = routed_environment(subnet, images, token)
    # The server overlay needs this unused value for interpolation. Both
    # commands use explicit env files and never read the fixed site's .env.
    offline_env = ENV | {"DJANGO_SECRET_KEY": "offline-named-worker-creation-only"}
    verify_compose(routed_env, images, subnet, offline_env)
    health = ProductionHealth()
    with health.monitor():
        try:
            health.check()
            operator = reserve_paths(resume_identity=resume_identity)
            write_private(operator / "rollback.json", json.dumps(before, indent=2) + "\n")
            env_file = operator / "routed.env"
            write_private(env_file, "".join(f"{key}='{value}'\n" for key, value in routed_env.items()))
            health.check()
            command("Named routed worker creation", *compose_command(env_file), "create", "--no-build",
                    "--pull", "never", "--no-recreate", *SERVICES, env=offline_env, timeout=60)
            configuration = manifest(docker, info, images, sizes, targets)
            config_file = BASE / "state" / "controller.json"
            write_private(config_file, json.dumps(configuration, indent=2) + "\n", owner=OWNER_ID)
            controller = ("runuser", "-u", ACCOUNT, "-g", ACCOUNT, "-G", "docker", "--", "/usr/bin/python3", "-B")
            runtime_env = ENV | {"HOTEL_SPEECH_CONTROL_TOKEN": token}
            health.check()
            command("Offline controller initialization", *controller, "-m", "speech_controller.initialize",
                    "--config", str(config_file), "--primary-stt", f"{PROJECT}-routed-vosk-primary",
                    "--primary-tts", f"{PROJECT}-routed-piper-primary", env=runtime_env, timeout=60)
            command("Offline controller startup check", *controller, "-m", "speech_controller.runtime",
                    "--config", str(config_file), "--check", env=runtime_env, timeout=60)
            for name in CONTAINERS:
                require(docker.run("inspect", "--type", "container", "--format", "{{.State.Status}}", name) == "created",
                        "A prepared worker is no longer in its original stopped state.")
        finally:
            require(rollback_snapshot(docker) == before, "Production container or booking volume identity changed.")
            require(health.sample(), "Production health changed during provisioning.")
    health.check()
    health.report()
    print("Private controller stores validated and all four routed workers remain stopped.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-stopped", action="store_true", required=True)
    parser.add_argument("--subnet", type=private_subnet, required=True)
    parser.add_argument("--resume-identity", action="store_true",
                        help="Resume only an existing locked identity with empty private operator state.")
    args = parser.parse_args(argv)
    try:
        prepare(args.subnet, resume_identity=args.resume_identity)
    except FixtureError as error:
        raise SystemExit(f"{error} Inspect any existing pilot artifacts before repeating provisioning.") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("Pilot configuration unavailable. Inspect any pilot artifacts locally before repeating provisioning.") from None


if __name__ == "__main__":
    main()
