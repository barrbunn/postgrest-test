#!/bin/sh
# Run a SQL statement inside the driver container (via psql, through the
# pgproxy sidecar). The write path for the roles_api scenario: the API
# gateway front is read-only, so mutations go straight to the database.
#
# Usage: sql.sh '<SQL>'
#   sql.sh "INSERT INTO roles (role_id, role_name) VALUES (1, 'editor');"
#
# Env:
#   PGR_TEST_INSTANCE   compose project/instance name (default: .instance)
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)"
cd "$ROOT"

SQL="${1:?usage: sql.sh '<SQL>'}"

if [ -n "${PGR_TEST_INSTANCE:-}" ]; then
    INSTANCE="$PGR_TEST_INSTANCE"
else
    INSTANCE=$(cat "$ROOT/.instance")
fi

podman compose -p "$INSTANCE" exec driver sh -c 'psql "$DATABASE_URL" -c "$1"' sh "$SQL"
