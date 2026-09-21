#!/bin/sh
# Run the host test suite: pytest (unit/component) and behave (integration).
#
# Usage:
#   scripts/test.sh                 # pytest, then behave
#   scripts/test.sh pytest [args]   # pytest only (extra args go to pytest)
#   scripts/test.sh behave [args]   # behave only (extra args go to behave)
#
# Requires uv and a running podman machine (ephemeral postgres container).
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is not installed" >&2
    exit 1
fi

if ! command -v podman >/dev/null 2>&1 || ! podman info >/dev/null 2>&1; then
    echo "error: podman is not available or the podman machine is not running" >&2
    exit 1
fi

uv sync --group test

mode="${1:-all}"
if [ "$#" -gt 0 ]; then
    shift
fi

run_pytest() {
    uv run pytest tests/unit "$@"
}

run_behave() {
    if [ "$#" -gt 0 ]; then
        uv run behave "$@"
    elif ls tests/features/*.feature >/dev/null 2>&1; then
        uv run behave tests/features
    else
        echo ">>> no feature files yet under tests/features, skipping behave"
    fi
}

case "$mode" in
    all)
        run_pytest "$@"
        run_behave "$@"
        ;;
    pytest)
        run_pytest "$@"
        ;;
    behave)
        run_behave "$@"
        ;;
    *)
        echo "usage: scripts/test.sh [all|pytest|behave] [args...]" >&2
        exit 1
        ;;
esac
