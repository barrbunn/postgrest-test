#!/bin/sh
# Generate a JWT on the host for API access through the gateway.
# Sources .env for PGRST_JWT_SECRET, then runs the pgjwt CLI via uv.
#
# Usage: scripts/jwt.sh [pgjwt options], e.g.:
#   scripts/jwt.sh --role editor --exp 1h
#   TOKEN=$(scripts/jwt.sh --role manager --exp 1h)
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

set -a
if [ -f .env ]; then
    . ./.env
fi
set +a

: "${PGRST_JWT_SECRET:?PGRST_JWT_SECRET is not set in .env}"
export PGRST_JWT_SECRET

exec uv run pgjwt "$@"
