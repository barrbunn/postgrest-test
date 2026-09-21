"""Unit tests that the mock server processes actually boot and serve."""

from __future__ import annotations

import httpx

from tests.harness.servers import start_mock
from tests.harness.tokens import Keys


def test_start_mock_idp(tmp_path):
    keys = Keys.generate()
    server = start_mock(
        "idp",
        {
            "public_pem": keys.idp_public_pem,
            "kid": keys.kid,
            "realm": "test",
            "issuer": keys.idp_issuer,
        },
        log_dir=tmp_path,
        ready_path="/realms/test/protocol/openid-connect/certs",
    )
    try:
        document = httpx.get(
            f"{server.url}/realms/test/protocol/openid-connect/certs"
        ).json()
        assert document["keys"][0]["kid"] == keys.kid
    finally:
        server.stop()


def test_start_mock_postgrest(tmp_path):
    server = start_mock(
        "postgrest",
        {"routes": {"GET /users?select=user_id": {"status": 200,
                                                  "json": [{"user_id": 7}]}}},
        log_dir=tmp_path,
        ready_path="/__requests",
    )
    try:
        response = httpx.get(f"{server.url}/users", params={"select": "user_id"})
        assert response.status_code == 200
        assert response.json() == [{"user_id": 7}]

        spy = httpx.get(f"{server.url}/__requests").json()
        assert [entry["path"] for entry in spy] == ["/users"]
    finally:
        server.stop()


def test_start_mock_gateway_rejects_anonymous(tmp_path):
    keys = Keys.generate()
    server = start_mock(
        "gateway",
        {
            "idp_public_pem": keys.idp_public_pem,
            "gateway_private_pem": keys.gateway_private_pem,
            "pgrmapper_url": "http://127.0.0.1:9",
            "issuer": keys.idp_issuer,
            "audience": keys.audience,
        },
        log_dir=tmp_path,
    )
    try:
        response = httpx.get(f"{server.url}/users")
        assert response.status_code == 401
        assert response.json() == {"error": "missing user JWT"}
    finally:
        server.stop()
