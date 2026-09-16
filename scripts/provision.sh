#!/bin/sh
# Provision (or tear down) a uniquely named instance of the PostgREST test stack.
#
# Usage:
#   scripts/provision.sh          # down the previous instance, up a fresh pgr-test-NN
#   scripts/provision.sh down     # tear down the current instance
#   scripts/provision.sh status   # show the current instance and its containers
#
# Every provision run creates a new instance name (pgr-test-01, pgr-test-02, ...)
# and passes it to compose via -p, so containers (<name>-<service>-1), the
# network (<name>_internal) and the volume (<name>_pgdata) never collide with
# other local stacks.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PREFIX="pgr-test"
LEGACY_PROJECT="postgrest-pgproxy"
STATE_FILE="$ROOT/.instance"

if command -v podman >/dev/null 2>&1; then
    COMPOSE="podman compose"
elif command -v docker >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    echo "error: neither podman nor docker was found in PATH" >&2
    exit 1
fi

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

# Every project of ours that may be running: any pgr-test-* instance plus the
# legacy default project name.
our_projects() {
    $COMPOSE ls --all 2>/dev/null \
        | awk 'NR > 1 { print $1 }' \
        | grep -E "^(pgr-test-[0-9]+|${LEGACY_PROJECT})$" || true
}

down_all() {
    local project
    for project in $(our_projects); do
        echo ">>> tearing down previous instance '$project'"
        $COMPOSE -p "$project" down --remove-orphans --volumes
    done
    rm -f "$STATE_FILE"
}

next_name() {
    local n=1
    if [ -f "$STATE_FILE" ]; then
        n=$(awk -F- '{ print $NF + 1 }' "$STATE_FILE")
    fi
    local highest
    highest=$(our_projects | awk -F- '/^pgr-test-[0-9]+$/ { if ($NF+0 > m) m=$NF+0 } END { print m+0 }')
    if [ "$highest" -ge "$n" ]; then
        n=$((highest + 1))
    fi
    printf '%s-%02d\n' "$PREFIX" "$n"
}

up_new() {
    local name="$1"
    [ -f .env ] || {
        echo ">>> .env not found, copying .env.example"
        cp .env.example .env
    }
    if [ ! -f certs/ca.crt ]; then
        echo ">>> test certificates missing, generating them"
        "$ROOT/scripts/gen-certs.sh"
    fi
    echo "$name" > "$STATE_FILE"
    echo ">>> provisioning instance '$name'"
    $COMPOSE -p "$name" up -d --build
    echo ">>> '$name' is up:"
    $COMPOSE -p "$name" ps
}

status() {
    if [ -f "$STATE_FILE" ]; then
        name=$(cat "$STATE_FILE")
        echo "current instance: $name"
        $COMPOSE -p "$name" ps
    else
        echo "no instance provisioned"
    fi
}

case "${1:-}" in
    "" | up | provision)
        name=$(next_name)
        down_all
        up_new "$name"
        ;;
    down | teardown)
        down_all
        echo ">>> all instances down"
        ;;
    status | ps)
        status
        ;;
    -h | --help | help)
        usage
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac
