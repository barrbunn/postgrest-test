"""Unit tests for key generation, JWT minting and the mock IdP JWKS."""

from __future__ import annotations

from fastapi.testclient import TestClient
from jose import jwt

from tests.harness.mocks import idp as idp_mock
from tests.harness.tokens import Keys, generate_rsa_keypair, jwks


def test_generated_keypair_is_usable():
    private_pem, public_pem = generate_rsa_keypair()
    token = jwt.encode({"sub": "x"}, private_pem, algorithm="RS256")
    assert jwt.decode(token, public_pem, algorithms=["RS256"])["sub"] == "x"


def test_user_jwt_roundtrip_and_claims():
    keys = Keys.generate()
    token = keys.user_jwt("editor", sub="alice")
    claims = jwt.decode(token, keys.idp_public_pem, algorithms=["RS256"],
                        audience=keys.audience)
    assert claims["realm_access"] == {"roles": ["editor"]}
    assert claims["iss"] == keys.idp_issuer
    assert claims["sub"] == "alice"


def test_service_jwt_embeds_user_jwt():
    keys = Keys.generate()
    user = keys.user_jwt("editor")
    service = keys.service_jwt(user, "editor", sub="alice")
    claims = jwt.decode(service, keys.gateway_public_pem, algorithms=["RS256"])
    assert claims["role"] == "editor"
    assert claims["user_jwt"] == user
    assert claims["iss"] == "apigee"


def test_gateway_headers_shape():
    keys = Keys.generate()
    headers = keys.gateway_headers("manager")
    assert headers["X-User-Role"] == "manager"
    service = headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(service, keys.gateway_public_pem, algorithms=["RS256"])
    assert claims["role"] == "manager"


def test_keys_write_and_jwks_shape(tmp_path):
    keys = Keys.generate()
    paths = keys.write(tmp_path)
    assert paths["gateway_public"].name == "gateway-jwt-public.pem"
    assert paths["idp_public"].read_text() == keys.idp_public_pem

    document = jwks(keys.idp_public_pem, keys.kid)
    key = document["keys"][0]
    assert (key["kid"], key["kty"], key["alg"], key["use"]) == \
        ("test-kid", "RSA", "RS256", "sig")


def test_mock_idp_serves_discovery_and_certs():
    keys = Keys.generate()
    app = idp_mock.create_app(public_pem=keys.idp_public_pem, kid=keys.kid,
                              realm="test", issuer="http://idp.test/realms/test")
    client = TestClient(app)

    discovery = client.get("/realms/test/.well-known/openid-configuration").json()
    assert discovery["jwks_uri"] == \
        "http://idp.test/realms/test/protocol/openid-connect/certs"

    certs = client.get("/realms/test/protocol/openid-connect/certs").json()
    assert certs["keys"][0]["kid"] == keys.kid
