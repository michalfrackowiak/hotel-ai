#!/bin/sh
# Staffed cutover on the native production ARM64 host.
set -eu
umask 077
test "$(id -u)" = 0
test "$(uname -m)" = aarch64
base=/var/lib/hotel-ai-speech
operator=$base/operator
release=/opt/hotel-ai-speech/d56d1a4ccded8f551d3281d5016a7334bffc9425
fixed=/home/ubuntu/hotel-ai
snippet=/etc/nginx/snippets/hotel-ai-reception.inc
unit=hotel-ai-speech-pilot.service
site=https://92.5.19.10
volume=hotel-ai_hotel-data
root=$(dirname "$(realpath "$0")")
primary_piper=hotel-ai-routed-piper-primary
primary_vosk=hotel-ai-routed-vosk-primary
test "$(stat -c '%u:%a' "$operator")" = 0:700
exec 9>"$operator/activation.lock"
flock -n 9
test "$(stat -c '%F:%u:%a' "$snippet")" = 'regular file:0:644'
test "$(stat -c '%u:%a' "$root")" = 0:700
test "$(systemctl show "$unit" -p LoadState --value)" = not-found
test -z "$(ss -H -ltn 'sport = :8093')"
test -f "$fixed/.env"
nginx -t

docker() { timeout 80s /usr/bin/docker --host unix:///run/docker.sock "$@"; }
production_health() { curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8080/healthz/ >/dev/null; }
public_status() {
    curl --noproxy '*' --connect-to 92.5.19.10:443:127.0.0.1:443 \
        --silent --show-error --max-time 8 -o /dev/null -w '%{http_code}' "$site$1"
}
wait_for_public_status() {
    for attempt in $(seq 1 20); do
        if [ "$(public_status "$1")" = "$2" ]; then return; fi
        sleep 0.25
    done
    echo "Nginx did not return HTTP $2 for $1." >&2
    return 1
}
work=$(mktemp -d "$operator/cutover.XXXXXXXX")
python3 - "$operator" "$base/state/controller.json" > "$work/ids" <<'PY'
import json
from pathlib import Path
import re
import subprocess
import sys

operator = Path(sys.argv[1])
load = lambda path: json.loads(path.read_text())
images = load(operator / "web-images.json")
rollback = load(operator / "rollback.json")
manifest = load(Path(sys.argv[2]))
def docker(*args):
    return subprocess.check_output(["/usr/bin/docker", "--host", "unix:///run/docker.sock", *args], text=True).strip()
for key in ("candidate", "rollback"):
    image = images[key + "_id"]
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    assert docker("image", "inspect", "--format", "{{.Id}} {{.Architecture}}", images[key + "_tag"]) == image + " arm64"
    print(image)
assert images["rollback_id"] == rollback["services"]["web"]["image"]
for service in ("web", "piper", "asr"):
    record = rollback["services"][service]
    name = f"hotel-ai-{service}-1"
    fmt = "{{.Id}} {{.Image}} {{.State.StartedAt}} {{.State.Health.Status}} {{.HostConfig.NetworkMode}}"
    deployed, image, started, health, network = docker("container", "inspect", "--format", fmt, name).split()
    assert re.fullmatch(r"[a-f0-9]{64}", deployed) and image == record["image"] and health == "healthy"
    if service == "web":
        assert network == "hotel-ai_default"
    else:
        assert deployed == record["id"] and started == record["started"]
    print(deployed)
names = ("hotel-ai-routed-piper-primary", "hotel-ai-routed-vosk-primary",
         "hotel-ai-routed-piper-standby", "hotel-ai-routed-vosk-standby")
deployments = {record["name"]: record for record in manifest["deployments"]}
assert set(names) == deployments.keys()
for name in names:
    record = deployments[name]
    assert docker("container", "inspect", "--format", "{{.Id}} {{.Image}} {{.State.Status}}", name) == (
        f'{record["container_id"]} {record["image_id"]} created')
    print(record["container_id"])
PY
{
    read -r candidate
    read -r rollback
    read -r old_web
    read -r old_piper
    read -r old_vosk
    read -r new_piper
    read -r new_vosk
    read -r standby_piper
    read -r standby_vosk
} < "$work/ids"
test "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}' "$old_web")" = "$volume"
test "$(docker ps -q --filter "volume=$volume" --no-trunc)" = "$old_web"
production_health
test "$(public_status /admin/login/)" = 200
test "$(public_status /static/admin/css/base.css)" = 200
test "$(public_status /api/availability/)" = 404
cp -p -- "$snippet" "$work/reception.inc"
printf 'location / { return 503; }\n' > "$work/maintenance.inc"
cat > "$work/no-migrations.yaml" <<'YAML'
services:
  web:
    command:
      - gunicorn
      - config.wsgi:application
      - --bind
      - 0.0.0.0:8000
      - --workers
      - "1"
      - --threads
      - "4"
YAML
routed() {
    HOTEL_WEB_IMAGE=$candidate docker compose -p hotel-ai --env-file "$fixed/.env" --env-file "$operator/routed.env" \
        -f "$release/compose.yaml" -f "$release/compose.server.yaml" -f "$release/compose.speech-routed.yaml" \
        -f "$operator/compose.web-image.yaml" "$@"
}
fixed_web() {
    HOTEL_WEB_IMAGE=$rollback docker compose -p hotel-ai --env-file "$fixed/.env" \
        -f "$fixed/compose.yaml" -f "$fixed/compose.server.yaml" -f "$fixed/compose.piper.yaml" \
        -f "$fixed/compose.asr.yaml" -f "$operator/compose.web-image.yaml" -f "$work/no-migrations.yaml" "$@"
}
routed config -q
fixed_web config -q
python3 - "$operator/routed.env" "$work/controller.env" <<'PY'
import os
import re
import sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
for line in source.read_text().splitlines():
    match = re.fullmatch(r"HOTEL_SPEECH_CONTROL_TOKEN='([0-9a-f]{64})'", line)
    if match:
        break
else:
    raise SystemExit("Controller token missing from private pilot configuration.")
with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
    stream.write("HOTEL_SPEECH_CONTROL_TOKEN=" + match[1] + "\n")
PY

swap_snippet() {
    temp=$(mktemp "$(dirname "$snippet")/.hotel-ai.XXXXXXXX")
    install -m 0644 "$1" "$temp" && mv -f -- "$temp" "$snippet" && nginx -t && systemctl reload nginx
}
web_stopped=0
rollback_cutover() {
    result=$?
    trap - EXIT
    trap '' HUP INT TERM
    echo 'Cutover interrupted. Restoring fixed service.' >&2
    if [ "$web_stopped" = 0 ]; then
        swap_snippet "$work/reception.inc" && production_health || exit 2
    else
        swap_snippet "$work/maintenance.inc" || exit 2
        current=$(docker ps -q --filter 'name=^/hotel-ai-web-1$' --no-trunc) || exit 2
        if [ -n "$current" ]; then docker stop -t 25 "$current" >/dev/null || exit 2; fi
        if [ "$(systemctl show "$unit" -p LoadState --value)" = loaded ]; then systemctl stop "$unit" || exit 2; fi
        for worker in "$new_piper" "$new_vosk"; do
            running=$(docker inspect --format '{{.State.Running}}' "$worker") || exit 2
            if [ "$running" = true ]; then docker stop -t 12 "$worker" >/dev/null || exit 2; fi
        done
        docker start "$old_piper" "$old_vosk" >/dev/null || exit 2
        fixed_web up -d --no-deps --no-build --pull never --wait --wait-timeout 50 web || exit 2
        production_health || exit 2
        python3 "$release/scripts/smoke_piper.py" http://127.0.0.1:8080 --asr || exit 2
        swap_snippet "$work/reception.inc" || exit 2
        if ! wait_for_public_status /healthz/ 200 || ! wait_for_public_status /admin/login/ 200 \
            || ! wait_for_public_status /static/admin/css/base.css 200 \
            || ! wait_for_public_status /api/availability/ 404; then
            swap_snippet "$work/maintenance.inc" || true
            exit 2
        fi
    fi
    echo 'Fixed production service restored.' >&2
    exit "$result"
}
trap rollback_cutover EXIT
trap 'exit 1' HUP INT TERM

swap_snippet "$work/maintenance.inc"
wait_for_public_status / 503
web_stopped=1
docker stop -t 30 "$old_web" >/dev/null
test -z "$(docker ps -q --filter "volume=$volume")"
echo 'Guest traffic paused and the booking writer stopped.'

mkdir -m 0700 "$work/backup" "$work/backup/rollback-check"
chown 10001:10001 "$work/backup" "$work/backup/rollback-check"
docker run --rm --pull never -i --network none --read-only --user 10001:10001 --cap-drop ALL \
    --security-opt no-new-privileges --memory 512m --memory-swap 512m --cpus 0.5 \
    --mount "type=volume,source=$volume,target=/source,readonly,volume-nocopy" \
    --mount "type=bind,source=$work/backup,target=/backup" \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m,uid=10001,gid=10001,mode=0700 \
    "$candidate" timeout 70s python - < "$root/verify_cutover_backup.py"
(cd "$work/backup" && sha256sum --check bookings.sha256)
docker run --rm --pull never -i --network none --read-only --user 10001:10001 --cap-drop ALL \
    --security-opt no-new-privileges --memory 512m --memory-swap 512m --cpus 0.5 \
    --mount "type=bind,source=$work/backup/rollback-check,target=/restore,readonly" \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,uid=10001,gid=10001,mode=0700 \
    -e 'DJANGO_DB_PATH=/tmp/rollback.sqlite3' -e 'DJANGO_SECRET_KEY=fictional-rollback-check' \
    "$rollback" timeout 20s python - <<'PY'
from contextlib import closing
import os
import sqlite3
with closing(sqlite3.connect("file:/restore/restored.sqlite3?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect(os.environ["DJANGO_DB_PATH"])) as target:
        source.backup(target)
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from django.test import Client
from bookings.models import Room
Room.objects.count()
client = Client()
for path in ("/healthz/", "/?lang=en", "/?lang=pl", "/admin/login/"):
    assert client.get(path, HTTP_HOST="localhost").status_code == 200
print("Rollback image reads the migrated disposable copy.")
PY
rm -- "$work/backup/rollback-check/restored.sqlite3"
rmdir -- "$work/backup/rollback-check"
chown -R root:root "$work/backup"
sync -f "$work/backup"
echo 'Fresh booking backup and both image compatibility checks passed.'

docker stop -t 20 "$old_piper" "$old_vosk" >/dev/null
docker start "$new_piper" "$new_vosk" >/dev/null
cd "$release"
set -a
. "$work/controller.env"
set +a
runuser -u hotel-ai-speech -g hotel-ai-speech -G docker -- /usr/bin/python3 -B -m speech_controller.runtime \
    --config "$base/state/controller.json" --check
runuser -u hotel-ai-speech -g hotel-ai-speech -G docker -- /usr/bin/python3 -B - \
    "$base/state/controller.json" "$primary_piper" "$primary_vosk" <<'PY'
import os
import sys
from speech_controller.docker_control import DockerControls
from speech_controller.endpoint_binding import BoundReadiness
from speech_controller.runtime_config import RuntimeConfiguration

config = RuntimeConfiguration.load(sys.argv[1], os.environ["HOTEL_SPEECH_CONTROL_TOKEN"])
ready = BoundReadiness(DockerControls(config.docker_socket, config.daemon_id, config.approvals),
                       config.targets, config.bindings)
for task, name in zip(("tts", "stt"), sys.argv[2:], strict=True):
    for language in ("en", "pl"):
        if not ready(name, (language, task)):
            raise SystemExit(f"Bound readiness failed for {language} {task}.")
        print(f"Bound readiness passed for {language} {task}.", flush=True)
PY
unset HOTEL_SPEECH_CONTROL_TOKEN
systemd-run --unit="$unit" --service-type=exec --working-directory="$release" \
    --property=User=hotel-ai-speech --property=Group=hotel-ai-speech --property=SupplementaryGroups=docker \
    --property="EnvironmentFile=$work/controller.env" --property=Restart=on-failure --property=RestartSec=3s \
    /usr/bin/python3 -B -m speech_controller.runtime --config "$base/state/controller.json"
python3 - "$work/controller.env" "$primary_piper" "$primary_vosk" <<'PY'
import json
from pathlib import Path
import sys
from time import sleep
from urllib.request import Request, urlopen

token = Path(sys.argv[1]).read_text().strip().split("=", 1)[1]
request = Request("http://127.0.0.1:8093/v1/status", headers={"Authorization": "Bearer " + token})
for attempt in range(25):
    try:
        with urlopen(request, timeout=2) as response:
            status = json.load(response)
        break
    except OSError:
        sleep(0.25)
else:
    raise SystemExit("No authenticated controller status.")
assert status["blocked"] is False and status["observed"] == status["state"]
assert not status["pending_requests"] and not status["pending_recoveries"]
assert status["state"]["revision"] == 0
assert {tuple(slot): name for slot, name in status["state"]["routes"]} == {
    (lang, task): name for lang in ("en", "pl") for task, name in zip(("tts", "stt"), sys.argv[2:], strict=True)}
print("Authenticated controller status verified at revision zero.")
PY

routed up -d --no-deps --no-build --pull never --wait --wait-timeout 50 web
test "$(docker inspect --format '{{.Image}} {{.HostConfig.NetworkMode}}' hotel-ai-web-1)" = "$candidate host"
test "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}' hotel-ai-web-1)" = "$volume"
test "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/run/hotel-ai-traffic"}}{{.Source}}{{end}}{{end}}' hotel-ai-web-1)" = "$base/traffic"
test "$(ss -H -ltn 'sport = :8080' | awk '{print $4}')" = 127.0.0.1:8080
test "$(ss -H -ltn 'sport = :8093' | awk '{print $4}')" = 127.0.0.1:8093
test "$(docker inspect --format '{{.State.Status}}' "$standby_piper" "$standby_vosk" | sort -u)" = created
production_health
python3 "$release/scripts/smoke_piper.py" http://127.0.0.1:8080 --asr
test "$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/admin/login/)" = 200
swap_snippet "$work/reception.inc"
wait_for_public_status /healthz/ 200
wait_for_public_status /admin/login/ 200
wait_for_public_status /static/admin/css/base.css 200
wait_for_public_status /api/availability/ 404
wait_for_public_status '/?lang=en' 200
wait_for_public_status '/?lang=pl' 200
trap - EXIT HUP INT TERM
echo 'Routed speech pilot is live. Verified backup retained in the private operator cutover directory.'
