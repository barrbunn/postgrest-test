"""Behave hooks: session infrastructure plus a per-scenario stack.

Session scope: ephemeral Postgres, the IdP key pair and the mock IdP.
Scenario scope: a fresh database provisioned from the feature's scenario,
a mock PostgREST (started lazily once all routes are declared) and a
pgrmapper subprocess bound to them. The mock gateway is started lazily when
a step goes through it.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import httpx
from behave.model_core import Status

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.harness import postgres as postgres_harness  # noqa: E402
from tests.harness.pgrmapper import PgrmapperServer, start_pgrmapper  # noqa: E402
from tests.harness.scenario import ScenarioHandle, provision_scenario  # noqa: E402
from tests.harness.servers import MockServer, start_mock  # noqa: E402
from tests.harness.tokens import Keys  # noqa: E402

IDP_REALM = "test"


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def before_all(context) -> None:
    if not postgres_harness.podman_available():
        raise RuntimeError(
            "podman is not available or the podman machine is not running"
        )
    context.session_dir = Path(tempfile.mkdtemp(prefix="pgr-behave-"))
    context.postgres = postgres_harness.start_postgres()
    context.keys = Keys.generate()
    context.idp = start_mock(
        "idp",
        {
            "public_pem": context.keys.idp_public_pem,
            "kid": context.keys.kid,
            "realm": IDP_REALM,
            "issuer": context.keys.idp_issuer,
        },
        log_dir=context.session_dir / "idp",
        ready_path=f"/realms/{IDP_REALM}/protocol/openid-connect/certs",
    )
    _log(f">>> behave harness dir: {context.session_dir}")


def before_scenario(context, scenario) -> None:
    context.scenario_handle = None
    context.postgrest = None
    context.mapper = None
    context.gateway = None
    context.routes = {}
    context.policy = "reject"
    context.response = None
    context.artifact_dir = Path(
        tempfile.mkdtemp(prefix="scenario-", dir=context.session_dir)
    )


def after_scenario(context, scenario) -> None:
    if scenario.status == Status.failed:
        _log(f"--- diagnostics for failed scenario: {scenario.name} ---")
        if context.mapper is not None:
            _log(f"[pgrmapper log tail]\n{context.mapper.process.log_tail()}")
        if context.postgrest is not None:
            try:
                spy = postgrest_spy(context)
                _log(f"[postgrest spy]\n{spy}")
            except httpx.HTTPError:
                pass
        if context.response is not None:
            _log(f"[last response] {context.response.status_code} "
                 f"{context.response.text[:500]}")
    for server in (context.gateway, context.mapper, context.postgrest):
        if server is not None:
            server.stop()
    context.gateway = None
    context.mapper = None
    context.postgrest = None
    if context.scenario_handle is not None:
        context.scenario_handle.teardown()
        context.scenario_handle = None
    shutil.rmtree(context.artifact_dir, ignore_errors=True)


def after_all(context) -> None:
    if getattr(context, "idp", None) is not None:
        context.idp.stop()
    if getattr(context, "postgres", None) is not None:
        context.postgres.stop()
    if getattr(context, "session_dir", None) is not None:
        shutil.rmtree(context.session_dir, ignore_errors=True)


def require_scenario(context) -> ScenarioHandle:
    if context.scenario_handle is None:
        raise AssertionError(
            'scenario not provisioned; add: Given the scenario "users_grants"'
        )
    return context.scenario_handle


def db_role(context, logical: str) -> str:
    """Database role name for a logical scenario role."""
    return require_scenario(context).roles.get(logical, logical)


def ensure_postgrest(context) -> MockServer:
    if context.postgrest is None:
        context.postgrest = start_mock(
            "postgrest",
            {"routes": context.routes},
            log_dir=context.artifact_dir,
            ready_path="/__requests",
        )
    return context.postgrest


def ensure_mapper(context) -> PgrmapperServer:
    if context.mapper is None:
        scenario = require_scenario(context)
        postgrest = ensure_postgrest(context)
        context.mapper = start_pgrmapper(
            database_url=scenario.db_url,
            upstream_url=postgrest.url,
            keys=context.keys,
            log_dir=context.artifact_dir,
            idp_jwks_url=f"{context.idp.url}/realms/{IDP_REALM}"
                         "/protocol/openid-connect/certs",
            policy=context.policy,
        )
    return context.mapper


def ensure_gateway(context) -> MockServer:
    if context.gateway is None:
        mapper = ensure_mapper(context)
        context.gateway = start_mock(
            "gateway",
            {
                "idp_public_pem": context.keys.idp_public_pem,
                "gateway_private_pem": context.keys.gateway_private_pem,
                "pgrmapper_url": mapper.url,
                "issuer": context.keys.idp_issuer,
                "audience": context.keys.audience,
            },
            log_dir=context.artifact_dir,
        )
    return context.gateway


def add_route(context, spec: str, response: dict) -> None:
    if context.postgrest is not None:
        raise AssertionError(
            "postgrest routes must be declared before the first request"
        )
    context.routes[spec] = response


def postgrest_spy(context) -> list[dict]:
    if context.postgrest is None:
        return []
    return httpx.get(f"{context.postgrest.url}/__requests").json()
