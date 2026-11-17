#!/bin/sh
# Disposable Gunicorn listener for an SSH-forwarded admin login rehearsal.
set -eu
umask 077

fail() { printf '%s\n' "$1" >&2; exit 1; }
[ "$#" -eq 2 ] || fail 'Supply a fixture port and candidate directory.'
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] && [ "$(id -u)" -ne 0 ] \
    || fail 'Use an ordinary user on the ARM64 Linux host.'
port=$1
case "$port" in [2-5][0-9][0-9][0-9][0-9]) ;; *) fail 'Use a port from 20000 to 59999.' ;; esac
source=$2
case "$source" in /tmp/hotel-ai-*/source) ;; *) fail 'Use a private temporary candidate directory.' ;; esac
case "$source" in *[!A-Za-z0-9/._-]*) fail 'Invalid candidate path.' ;; esac
[ "$(realpath -e -- "$source")" = "$source" ] && [ -d "$source" ] \
    && [ "$(realpath -e -- "$0")" = "$source/scripts/check_private_admin_listener.sh" ] \
    || fail 'Run the script from the exact extracted candidate.'
parent=$(dirname -- "$source")
[ "$(dirname -- "$parent")" = /tmp ] && [ "$(stat -c '%u %a' -- "$parent")" = "$(id -u) 700" ] \
    || fail 'Candidate parent must be private and owned by the operator.'
socket=$(realpath -e /var/run/docker.sock)
[ -S "$socket" ] || fail 'Local Docker socket unavailable.'

docker() {
    timeout 15s env -i PATH=/usr/bin:/bin /usr/bin/sudo -n /usr/bin/docker --host "unix://$socket" "$@"
}
health() {
    curl --noproxy '*' --fail --silent --max-time 2 "http://127.0.0.1:$1/healthz/" >/dev/null
}
listener() { ss -H -ltn "( sport = :$port )"; }
snapshot() {
    docker inspect --type container --format '{{.Id}} {{.Image}} {{.State.StartedAt}} {{.State.Health.Status}}' \
        hotel-ai-web-1 hotel-ai-piper-1 hotel-ai-asr-1
}
daemon=$(docker info --format '{{.ID}}')
[ -n "$daemon" ] && [ "$(docker info --format '{{.OSType}} {{.Architecture}}')" = 'linux aarch64' ] \
    || fail 'Expected the native ARM64 Docker daemon.'
before=$(snapshot)
[ "$(printf '%s\n' "$before" | awk '$4 == "healthy" {n++} END {print n+0}')" -eq 3 ] \
    && health 8080 || fail 'Production health preflight failed.'
[ -z "$(listener)" ] || fail 'Fixture port must be unused.'
web_id=$(printf '%s\n' "$before" | awk 'NR == 1 {print $1}')
image=$(printf '%s\n' "$before" | awk 'NR == 1 {print $2}')
printf '%s\n' "$web_id" | grep -Eq '^[0-9a-f]{64}$' || fail 'Expected a full production container ID.'
printf '%s\n' "$image" | grep -Eq '^sha256:[0-9a-f]{64}$' || fail 'Expected a cached image ID.'
[ "$(docker inspect --type container --format '{{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}}' "$web_id")" = 'hotel-ai web' ] \
    && [ "$(docker image inspect --format '{{.Architecture}}' "$image")" = arm64 ] \
    || fail 'The local image does not match the approved web service.'
IFS= read -r password || fail 'Expected the fixture password on standard input.'
printf '%s\n' "$password" | grep -Eq '^[0-9a-f]{64}$' || fail 'Invalid fixture password.'
secret=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
name=hotel-ai-listener-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
label=hotel-ai.cutover-fixture=$name
env_file=$(mktemp "$parent/cutover-env.XXXXXXXX")

cleanup() {
    status=$?
    trap - EXIT
    trap '' HUP INT TERM
    if ! current_daemon=$(docker info --format '{{.ID}}') || [ "$current_daemon" != "$daemon" ]; then
        printf '%s\n' 'Docker identity changed before cleanup.' >&2
        status=1
    elif ids=$(docker ps -aq --no-trunc --filter "label=$label"); then
        for id in $ids; do
            if printf '%s\n' "$id" | grep -Eq '^[0-9a-f]{64}$'; then
                docker rm --force "$id" >/dev/null || status=1
            else
                status=1
            fi
        done
        if ! ids=$(docker ps -aq --no-trunc --filter "label=$label") || [ -n "$ids" ]; then status=1; fi
    else
        status=1
    fi
    rm -f -- "$env_file" || status=1
    if ! after=$(snapshot) || [ "$after" != "$before" ] || ! health 8080 \
        || ! remaining_listener=$(listener) || [ -n "$remaining_listener" ]; then
        printf '%s\n' 'Post-rehearsal production or listener check failed.' >&2
        status=1
    fi
    if [ "$status" -eq 0 ]; then printf '%s\n' 'CLEAN: fixture removed and production state matched.'; fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
printf '%s\n' 'DJANGO_DB_PATH=/tmp/fixture.sqlite3' "DJANGO_SECRET_KEY=$secret" \
    "CUTOVER_FIXTURE_PASSWORD=$password" "CUTOVER_FIXTURE_PORT=$port" > "$env_file"
unset password secret

[ "$(docker info --format '{{.ID}}')" = "$daemon" ] || fail 'Docker identity changed before fixture creation.'
[ "$(snapshot)" = "$before" ] && health 8080 || fail 'Production changed before fixture creation.'
id=$(docker create --pull never --name "$name" --label "$label" --network host \
    --user 10001:10001 --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,uid=10001,gid=10001 \
    --cap-drop ALL --security-opt no-new-privileges --memory 512m --memory-swap 512m --cpus 0.25 --init \
    --env-file "$env_file" --workdir /stage-src --mount "type=bind,src=$source,dst=/stage-src,readonly" \
    "$image" sh -ec '
        python -c "from pathlib import Path; p=Path; assert p(\"/app/requirements.txt\").read_bytes().splitlines() == p(\"requirements.txt\").read_bytes().splitlines()"
        python manage.py migrate --noinput --verbosity 0
        python manage.py shell --verbosity 0 -c "import os; from django.contrib.auth import get_user_model; get_user_model().objects.create_superuser(\"cutover-fixture-staff\", password=os.environ[\"CUTOVER_FIXTURE_PASSWORD\"])"
        unset CUTOVER_FIXTURE_PASSWORD
        exec gunicorn config.wsgi:application --bind "127.0.0.1:$CUTOVER_FIXTURE_PORT" --workers 1 --threads 4
    ')
printf '%s\n' "$id" | grep -Eq '^[0-9a-f]{64}$' || fail 'Docker returned an unexpected fixture ID.'
[ "$(docker info --format '{{.ID}}')" = "$daemon" ] || fail 'Docker identity changed before fixture startup.'
docker start "$id" >/dev/null
rm -f -- "$env_file"
for attempt in $(seq 1 25); do
    health 8080 || fail 'Production health failed during startup.'
    if health "$port"; then break; fi
    sleep 1
done
health "$port" || fail 'The fixture did not become healthy.'
[ "$(listener | awk '{print $4}')" = "127.0.0.1:$port" ] || fail 'Fixture listener is not loopback-only.'
printf '%s\n' READY
deadline=$(( $(date +%s) + 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    health 8080 && health "$port" || fail 'A health check failed during the rehearsal.'
    sleep 1
done
