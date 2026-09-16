#!/bin/sh
# Generate the test CA and mTLS certificates used between the nginx gateway
# (TLS client) and pgrmapper (TLS server). Regenerates everything.
#
#   certs/ca.crt|ca.key        test CA (trusted by both sides)
#   certs/server.crt|server.key pgrmapper server cert (SAN DNS:pgrmapper)
#   certs/client.crt|client.key gateway client cert
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p certs
cd certs

# 1. Test CA
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365 \
    -keyout ca.key -out ca.crt -subj "/CN=pgr-test-mtls-ca"

# 2. Server certificate for pgrmapper (name must match the upstream host)
openssl req -newkey rsa:2048 -nodes -sha256 \
    -keyout server.key -out server.csr -subj "/CN=pgrmapper"
printf "subjectAltName=DNS:pgrmapper,DNS:localhost\nextendedKeyUsage=serverAuth\n" > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days 365 -sha256 -extfile server.ext

# 3. Client certificate for the gateway
openssl req -newkey rsa:2048 -nodes -sha256 \
    -keyout client.key -out client.csr -subj "/CN=gateway"
printf "extendedKeyUsage=clientAuth\n" > client.ext
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out client.crt -days 365 -sha256 -extfile client.ext

rm -f server.csr client.csr server.ext client.ext

# 4. IdP RSA keypair (signs the user JWTs). The private key is inlined into
#    the generated mockup config; the public key verifies them in the gateway.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out idp-private.pem
openssl rsa -in idp-private.pem -pubout -out idp-public.pem

# 5. Gateway JWT signing keypair (signs the service JWT sent to pgrmapper).
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out gateway-jwt.key
openssl rsa -in gateway-jwt.key -pubout -out gateway-jwt-public.pem

# 6. Render the keycloak-mockup config with the IdP private key inlined.
python3 - "$ROOT/idp/config.example.yaml" idp-private.pem > idp-config.yaml <<'PYEOF'
import sys

example_path, key_path = sys.argv[1], sys.argv[2]
with open(example_path) as f:
    content = f.read()
with open(key_path) as f:
    key = f.read()
block = "private_key_pem: |\n" + "\n".join("  " + line for line in key.splitlines())
content = content.replace("# private_key_pem: generated", block)
sys.stdout.write(content)
PYEOF

# 7. JWK set for PostgREST: verifies both the RS256 service JWTs (gateway
#    public key) and the legacy HS256 tokens (PGRST_JWT_SECRET).
set -a
if [ -f "$ROOT/.env" ]; then
    . "$ROOT/.env"
fi
set +a
MOD_HEX=$(openssl rsa -pubin -in gateway-jwt-public.pem -modulus -noout | cut -d= -f2)
HS_SECRET="${PGRST_JWT_SECRET:-}"
python3 - "$MOD_HEX" "$HS_SECRET" > jwt-secrets.json <<'PYEOF'
import base64, json, sys

n_hex, hs_secret = sys.argv[1], sys.argv[2]
n_b64 = base64.urlsafe_b64encode(bytes.fromhex(n_hex)).decode().rstrip("=")
k_b64 = base64.urlsafe_b64encode(hs_secret.encode()).decode().rstrip("=")
doc = {
    "keys": [
        {"kty": "oct", "kid": "hmac", "alg": "HS256", "use": "sig", "k": k_b64},
        {"kty": "RSA", "kid": "gateway", "alg": "RS256", "use": "sig", "n": n_b64, "e": "AQAB"},
    ]
}
print(json.dumps(doc))
PYEOF

echo "generated certs/: ca.crt, server.crt, server.key, client.crt, client.key,"
echo "                  idp-private.pem, idp-public.pem, gateway-jwt.key,"
echo "                  gateway-jwt-public.pem, idp-config.yaml, jwt-secrets.json"
