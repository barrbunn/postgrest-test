"""Unit tests for the mock PostgREST route matcher and spy."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.harness.mocks.postgrest import (
    build_response,
    canonical_key,
    create_app,
    index_routes,
    key_from_spec,
    match_route,
)


def test_canonical_key_is_order_insensitive():
    assert canonical_key("get", "/users", "select=user_id&limit=2") == \
        canonical_key("GET", "/users", "limit=2&select=user_id")


def test_canonical_key_preserves_duplicate_params():
    assert canonical_key("GET", "/users", "id=gt.1&id=lt.9") == \
        "GET /users?id=gt.1&id=lt.9"


def test_key_from_spec_normalizes():
    assert key_from_spec("GET /users?limit=2&select=user_id") == \
        "GET /users?limit=2&select=user_id"


def test_match_route_hits_and_misses():
    index = index_routes({"GET /users?select=user_id": {"status": 200, "json": []}})
    assert match_route(index, "GET", "/users", "select=user_id") == {
        "status": 200, "json": [],
    }
    assert match_route(index, "GET", "/users", "select=user_name") is None
    assert match_route(index, "GET", "/other", "select=user_id") is None


def test_build_response_variants():
    json_response = build_response({"status": 201, "json": {"a": 1}})
    assert json_response.status_code == 201
    assert json_response.headers["content-type"].startswith("application/json")

    text_response = build_response({"text": "nope"})
    assert text_response.status_code == 200
    assert text_response.body == b"nope"

    empty = build_response({"status": 204})
    assert empty.status_code == 204
    assert empty.body == b""


def test_app_serves_mapped_response_and_records_spy():
    app = create_app(routes={
        "GET /users?select=user_id": {"status": 200, "json": [{"user_id": 1}]},
    })
    client = TestClient(app)

    response = client.get("/users", params={"select": "user_id"})
    assert response.status_code == 200
    assert response.json() == [{"user_id": 1}]

    spy = client.get("/__requests").json()
    assert len(spy) == 1
    assert spy[0]["method"] == "GET"
    assert spy[0]["path"] == "/users"
    assert spy[0]["query"] == "select=user_id"

    client.post("/__reset")
    assert client.get("/__requests").json() == []


def test_app_unmatched_returns_500_and_still_records():
    app = create_app(routes={})
    client = TestClient(app)

    response = client.get("/users", params={"select": "secret"})
    assert response.status_code == 500
    assert "no route for GET /users" in response.json()["error"]
    assert len(client.get("/__requests").json()) == 1
