"""Unit tests for the mock gateway claim checks (no upstream involved)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.harness.mocks.gateway import build_service_claims, create_app
from tests.harness.tokens import Keys


def _client(keys: Keys, **overrides) -> TestClient:
    config = {
        "idp_public_pem": keys.idp_public_pem,
        "gateway_private_pem": keys.gateway_private_pem,
        "pgrmapper_url": "http://127.0.0.1:9",
        "issuer": keys.idp_issuer,
        "audience": keys.audience,
    }
    config.update(overrides)
    return TestClient(create_app(**config))


def _get(client: TestClient, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/users", headers=headers)


def test_missing_token_is_401():
    response = _get(_client(Keys.generate()), None)
    assert response.status_code == 401
    assert response.json() == {"error": "missing user JWT"}


def test_garbage_token_is_401():
    response = _get(_client(Keys.generate()), "not-a-jwt")
    assert response.status_code == 401
    assert response.json() == {"error": "invalid user JWT"}


def test_expired_token_is_401():
    keys = Keys.generate()
    token = keys.user_jwt("editor", expires_in=-10)
    response = _get(_client(keys), token)
    assert response.status_code == 401
    assert response.json() == {"error": "user JWT expired"}


def test_wrong_audience_is_401():
    keys = Keys.generate()
    token = keys.user_jwt("editor", audience="someone-else")
    response = _get(_client(keys), token)
    assert response.status_code == 401
    assert response.json() == {"error": "unexpected audience"}


def test_wrong_issuer_is_401():
    keys = Keys.generate()
    token = keys.user_jwt("editor", issuer="http://evil/realms/x")
    response = _get(_client(keys), token)
    assert response.status_code == 401
    assert response.json() == {"error": "unexpected issuer"}


def test_token_signed_by_another_key_is_401():
    keys = Keys.generate()
    other = Keys.generate()
    response = _get(_client(keys), other.user_jwt("editor"))
    assert response.status_code == 401
    assert response.json() == {"error": "invalid user JWT"}


def test_build_service_claims_embeds_user_jwt():
    claims = build_service_claims("user.jwt.here", "editor", sub="alice")
    assert claims["user_jwt"] == "user.jwt.here"
    assert claims["role"] == "editor"
    assert claims["iss"] == "apigee"
    assert claims["sub"] == "alice"
