"""Integration smoke test: real pgrmapper process against the mocks."""

from __future__ import annotations

import time

import httpx
import pytest
from jose import jwt

from tests.harness.pgrmapper import TEST_JWT_SECRET, start_pgrmapper
from tests.harness.scenario import provision_scenario
from tests.harness.servers import start_mock
from tests.harness.tokens import Keys

pytestmark = pytest.mark.integration


def test_pgrmapper_gateway_and_legacy_paths(postgres, tmp_path):
    scenario = provision_scenario(postgres, "users_grants")
    keys = Keys.generate()
    postgrest = idp = mapper = None
    try:
        postgrest = start_mock(
            "postgrest",
            {"routes": {"GET /users?select=user_id": {
                "status": 200, "json": [{"user_id": 1}]}}},
            log_dir=tmp_path / "postgrest",
            ready_path="/__requests",
        )
        idp = start_mock(
            "idp",
            {"public_pem": keys.idp_public_pem, "kid": keys.kid,
             "realm": "test", "issuer": keys.idp_issuer},
            log_dir=tmp_path / "idp",
            ready_path="/realms/test/protocol/openid-connect/certs",
        )
        mapper = start_pgrmapper(
            database_url=scenario.db_url,
            upstream_url=postgrest.url,
            keys=keys,
            log_dir=tmp_path / "pgrmapper",
            idp_jwks_url=f"{idp.url}/realms/test/protocol/openid-connect/certs",
        )
        editor = scenario.role("editor")

        response = httpx.get(f"{mapper.url}/users", params={"select": "user_id"},
                             headers=keys.gateway_headers(editor))
        assert response.status_code == 200
        assert response.json() == [{"user_id": 1}]

        response = httpx.get(
            f"{mapper.url}/users",
            params={"select": "user_id", "status": "eq.active"},
            headers=keys.gateway_headers(editor),
        )
        assert response.status_code == 403
        assert "users.status" in response.json()["error"]

        spy = httpx.get(f"{postgrest.url}/__requests").json()
        assert [entry["path"] for entry in spy] == ["/users"]

        token = jwt.encode({"role": editor, "exp": int(time.time()) + 300},
                           TEST_JWT_SECRET, algorithm="HS256")
        response = httpx.get(f"{mapper.url}/users", params={"select": "user_id"},
                             headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == [{"user_id": 1}]
    finally:
        for server in (mapper, idp, postgrest):
            if server is not None:
                server.stop()
        scenario.teardown()
