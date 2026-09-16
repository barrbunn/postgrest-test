#!/bin/sh
# Obtain a user JWT from the local keycloak-mockup (authorization-code flow
# driven entirely with curl), the "northbound" leg of the Apigee mimic.
#
# Usage: scripts/idp-token.sh <username> <password>
#   TOKEN=$(scripts/idp-token.sh alice alice123)
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

set -a
if [ -f .env ]; then
    . ./.env
fi
set +a

USERNAME="${1:?usage: idp-token.sh <username> <password>}"
PASSWORD="${2:?usage: idp-token.sh <username> <password>}"
IDP_URL="${IDP_URL:-http://localhost:${IDP_PORT:-5151}}"
REALM="${IDP_REALM:-myrealm}"
CLIENT_ID="${IDP_CLIENT_ID:-myclient}"
REDIRECT_URI="$IDP_URL/cb"

# 1. Login (form POST) -> 302 with ?code=...
LOCATION=$(curl -sS -D - -o /dev/null -X POST \
    "$IDP_URL/realms/$REALM/protocol/openid-connect/auth" \
    -d "username=$USERNAME" \
    -d "password=$PASSWORD" \
    -d "client_id=$CLIENT_ID" \
    -d "redirect_uri=$REDIRECT_URI" \
    -d "response_type=code" \
    -d "scope=openid" \
    | awk '/^[Ll]ocation:/ { sub("\r$", "", $2); print $2 }')

CODE=$(python3 -c "
import sys, urllib.parse
loc = sys.argv[1]
print(urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get('code', [''])[0])
" "$LOCATION")

[ -n "$CODE" ] || {
    echo "error: no authorization code in redirect: $LOCATION" >&2
    exit 1
}

# 2. Exchange the code for tokens
curl -sS -X POST "$IDP_URL/realms/$REALM/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=authorization_code" \
    -d "code=$CODE" \
    -d "redirect_uri=$REDIRECT_URI" \
    -d "client_id=$CLIENT_ID" \
    | python3 -c 'import json, sys; data = json.load(sys.stdin); print(data.get("access_token") or (sys.stderr.write(json.dumps(data) + "\n") or exit(1)))'
