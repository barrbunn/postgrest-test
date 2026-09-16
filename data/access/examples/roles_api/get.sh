#!/bin/sh
# GET a resource through the API gateway (read path: nginx VerifyJWT ->
# mTLS -> pgrmapper filtering -> PostgREST).
#
# Usage: get.sh <path> [query-string]
#   get.sh grants "select=*,user(*),application(*),role(*)"
#   get.sh users
#
# Env:
#   PGR_TEST_GATEWAY_ADDRESS   gateway base URL (default http://localhost:$NGINX_PORT)
#   PGR_JWT_TOKEN              user JWT to use (skips fetching one from the IdP)
#   PGR_TEST_USER              IdP username (default alice, role editor)
#   PGR_TEST_PASSWORD          IdP password (default alice123)
#   PGR_TEST_INSTANCE          compose project/instance name (default: .instance)
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../../../.." && pwd)"
cd "$ROOT"

set -a
if [ -f .env ]; then
    . ./.env
fi
set +a

PATH_ARG="${1:?usage: get.sh <path> [query-string]}"
QUERY="${2:-}"

case "$QUERY" in
    *"="* | "")
        ;;
    *)
        echo "error: the query string must be URL-encoded key=value pairs (e.g. 'select=*'), got: '$QUERY'" >&2
        exit 1
        ;;
esac

GATEWAY="${PGR_TEST_GATEWAY_ADDRESS:-http://localhost:${NGINX_PORT:-8080}}"
USERNAME="${PGR_TEST_USER:-alice}"
PASSWORD="${PGR_TEST_PASSWORD:-alice123}"

if [ -n "${PGR_JWT_TOKEN:-}" ]; then
    TOKEN="$PGR_JWT_TOKEN"
else
    TOKEN=$("$ROOT/scripts/idp-token.sh" "$USERNAME" "$PASSWORD")
fi

URL="$GATEWAY/$PATH_ARG"
[ -n "$QUERY" ] && URL="$URL?$QUERY"

curl -sS -H "Authorization: Bearer $TOKEN" "$URL" \
    | python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin), indent=2))'
