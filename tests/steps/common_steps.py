"""Step definitions shared by the integration features."""

from __future__ import annotations

import json
import time

import httpx
from behave import given, then, when
from jose import jwt

from tests.environment import (
    add_route,
    db_role,
    ensure_gateway,
    ensure_mapper,
    postgrest_spy,
)
from tests.harness.mocks.postgrest import canonical_key, key_from_spec
from tests.harness.pgrmapper import TEST_JWT_SECRET
from tests.harness.scenario import provision_scenario


def _perform(context, method: str, path: str, query: str = "",
             headers: dict | None = None, through_gateway: bool = False) -> None:
    base = ensure_gateway(context).url if through_gateway else ensure_mapper(context).url
    url = f"{base}{path}" + (f"?{query}" if query else "")
    context.response = httpx.request(method, url, headers=headers or {}, timeout=10)


def _hs256(role: str, expires_in: int = 300) -> str:
    return jwt.encode({"role": role, "exp": int(time.time()) + expires_in},
                      TEST_JWT_SECRET, algorithm="HS256")


def _user_jwt(context, role: str, **kwargs) -> str:
    return context.keys.user_jwt(db_role(context, role), **kwargs)


def _direct_headers(context, role: str, token: str | None = None) -> dict:
    service = token if token is not None else context.keys.service_jwt(
        _user_jwt(context, role), db_role(context, role)
    )
    return {"Authorization": f"Bearer {service}", "X-User-Role": db_role(context, role)}


# --- Given --------------------------------------------------------------------

@given('the scenario "{name}"')
def step_scenario(context, name):
    if context.scenario_handle is not None:
        raise AssertionError("the scenario is already provisioned")
    context.scenario_handle = provision_scenario(context.postgres, name)


@given('the query policy is "{policy}"')
def step_query_policy(context, policy):
    if context.mapper is not None:
        raise AssertionError("the query policy must be set before the first request")
    context.policy = policy


@given('a postgrest response for "{spec}" returning JSON {body}')
def step_route_json(context, spec, body):
    add_route(context, spec, {"status": 200, "json": json.loads(body)})


@given('a postgrest response for "{spec}" with status {status:d}')
def step_route_status(context, spec, status):
    add_route(context, spec, {"status": status})


@given('a postgrest response for "{spec}" with status {status:d} and JSON {body}')
def step_route_status_json(context, spec, status, body):
    add_route(context, spec, {"status": status, "json": json.loads(body)})


# --- When: through the mock gateway -------------------------------------------

@when('I request GET "{path}" with query "{query}" through the gateway as "{role}"')
def step_gateway_get_query(context, path, query, role):
    _perform(context, "GET", path, query=query,
             headers={"Authorization": f"Bearer {_user_jwt(context, role)}"},
             through_gateway=True)


@when('I request GET "{path}" through the gateway without a token')
def step_gateway_no_token(context, path):
    _perform(context, "GET", path, through_gateway=True)


@when('I request GET "{path}" through the gateway as "{role}" '
      'with an expired user JWT')
def step_gateway_expired(context, path, role):
    token = _user_jwt(context, role, expires_in=-10)
    _perform(context, "GET", path,
             headers={"Authorization": f"Bearer {token}"}, through_gateway=True)


@when('I request GET "{path}" through the gateway as "{role}" '
      'with a user JWT for audience "{audience}"')
def step_gateway_wrong_audience(context, path, role, audience):
    token = _user_jwt(context, role, audience=audience)
    _perform(context, "GET", path,
             headers={"Authorization": f"Bearer {token}"}, through_gateway=True)


@when('I request GET "{path}" through the gateway as "{role}" '
      'with a tampered user JWT')
def step_gateway_tampered(context, path, role):
    token = _user_jwt(context, role)
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    _perform(context, "GET", path,
             headers={"Authorization": f"Bearer {tampered}"}, through_gateway=True)


@when('I request GET "{path}" through the gateway as "{role}"')
def step_gateway_get(context, path, role):
    _perform(context, "GET", path,
             headers={"Authorization": f"Bearer {_user_jwt(context, role)}"},
             through_gateway=True)


# --- When: directly against pgrmapper -----------------------------------------

@when('I request GET "{path}" with query "{query}" directly without a token')
def step_direct_anon_query(context, path, query):
    _perform(context, "GET", path, query=query)


@when('I request GET "{path}" directly without a token')
def step_direct_anon(context, path):
    _perform(context, "GET", path)


@when('I request GET "{path}" with query "{query}" directly with HS256 role "{role}"')
def step_direct_hs256(context, path, query, role):
    headers = {"Authorization": f"Bearer {_hs256(db_role(context, role))}"}
    _perform(context, "GET", path, query=query, headers=headers)


@when('I request GET "{path}" with query "{query}" directly with an expired HS256 token')
def step_direct_hs256_expired(context, path, query):
    headers = {"Authorization": f"Bearer {_hs256('editor', expires_in=-10)}"}
    _perform(context, "GET", path, query=query, headers=headers)


@when('I request GET "{path}" with query "{query}" directly with an invalid HS256 token')
def step_direct_hs256_invalid(context, path, query):
    headers = {"Authorization": "Bearer not-a-jwt"}
    _perform(context, "GET", path, query=query, headers=headers)


@when('I request GET "{path}" directly with an invalid service JWT as "{role}"')
def step_direct_bad_service(context, path, role):
    _perform(context, "GET", path, headers=_direct_headers(context, role, "not-a-jwt"))


@when('I request GET "{path}" directly with an expired service JWT as "{role}"')
def step_direct_expired_service(context, path, role):
    token = context.keys.service_jwt(_user_jwt(context, role), db_role(context, role),
                                     expires_in=-10)
    _perform(context, "GET", path, headers=_direct_headers(context, role, token))


@when('I request GET "{path}" directly with a service JWT without embedded '
      'user JWT as "{role}"')
def step_direct_no_embedded_user(context, path, role):
    token = jwt.encode(
        {"iss": "apigee", "sub": "alice", "role": db_role(context, role),
         "exp": int(time.time()) + 60},
        context.keys.gateway_private_pem, algorithm="RS256",
    )
    _perform(context, "GET", path, headers=_direct_headers(context, role, token))


@when('I request GET "{path}" with query "{query}" directly with role header '
      '"{header_role}" and user JWT role "{jwt_role}"')
def step_direct_role_header(context, path, query, header_role, jwt_role):
    user = _user_jwt(context, jwt_role)
    service = context.keys.service_jwt(user, db_role(context, header_role))
    _perform(context, "GET", path, query=query,
             headers=_direct_headers(context, header_role, service))


@when('I send a POST request to "{path}"')
def step_post(context, path):
    _perform(context, "POST", path)


# --- Then ----------------------------------------------------------------------

@then('the response status is {status:d}')
def step_status(context, status):
    assert context.response is not None, "no response recorded"
    assert context.response.status_code == status, (
        f"expected {status}, got {context.response.status_code}: "
        f"{context.response.text[:300]}"
    )


@then('the response error mentions "{fragment}"')
def step_error(context, fragment):
    body = context.response.json()
    assert fragment in body.get("error", ""), f"error was: {body!r}"


@then('the response JSON is {body}')
def step_response_json(context, body):
    assert context.response.json() == json.loads(body)


@then('postgrest received no requests')
def step_no_requests(context):
    spy = postgrest_spy(context)
    assert spy == [], f"postgrest received: {spy}"


@then('postgrest received {count:d} requests')
def step_request_count(context, count):
    spy = postgrest_spy(context)
    assert len(spy) == count, f"postgrest received {len(spy)}: {spy}"


@then('postgrest received the last request "{spec}"')
def step_last_request(context, spec):
    spy = postgrest_spy(context)
    assert spy, "postgrest received no requests"
    entry = spy[0]
    actual = canonical_key(entry["method"], entry["path"], entry["query"])
    expected = key_from_spec(spec)
    assert actual == expected, f"expected {expected!r}, got {actual!r}"
