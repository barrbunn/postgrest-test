# Testing (pytest + Behave)

Host-side test suite for the pgrmapper components. It does not use the
compose stack: each run starts a disposable Postgres container, mock IdP /
gateway / PostgREST servers and real pgrmapper processes. See
[plan.md](plan.md) for the design and decisions.

## Quick start

```sh
scripts/test.sh                    # pytest (unit/component) + behave (integration)
scripts/test.sh pytest             # pytest only, e.g.:
scripts/test.sh pytest tests/unit/test_query_policy.py -k reject
scripts/test.sh behave             # all features
scripts/test.sh behave tests/features/query_policy.feature
```

Prerequisites: `uv` and a running podman machine. `scripts/test.sh` syncs the
`test` dependency group and fails before running anything if podman is not
available. Integration pytest tests are skipped when podman is missing;
behave fails fast.

## What is covered

| Suite | Component |
|---|---|
| `tests/unit/test_query_policy.py` | `transform_query` matrix: `allow`/`enforce`/`reject` across selects, filters, order, logic trees, embeds, aliases, JSON paths |
| `tests/unit/test_harness_*.py` | harness itself: ports/readiness, ephemeral postgres, scenario rendering and provisioning, server boot |
| `tests/unit/test_mock_*.py` | mock gateway claim checks, mock PostgREST matcher/spy, mock IdP JWKS |
| `tests/features/routing.feature` | live-schema routing, 404/405, health, root |
| `tests/features/identity_gateway.feature` | service JWT → embedded user JWT → JWKS chain, role header contract |
| `tests/features/identity_legacy.feature` | HS256 tokens, anonymous fallback, empty-rule blocking |
| `tests/features/query_policy.feature` | policy behavior end to end, asserting the exact request PostgREST receives |
| `tests/features/postgrest_interaction.feature` | header forwarding, status/body passthrough, unmatched-route failures |

## How the harness works

```
before_all      ephemeral postgres container (random port, disposable)
                IdP RSA keys (ephemeral) + mock IdP (JWKS)
before_scenario fresh database provisioned from tests/fixtures/scenarios/<name>
                via the real `pgprovision apply schemas`
                mock PostgREST (strict URL map + request spy), started lazily
                pgrmapper process bound to that database and PostgREST
                mock gateway (nginx mimic), started lazily
after_scenario  stops the processes, drops the database and the roles
after_all       stops the container, removes the session temp dir
```

- Scenario roles are cluster-global, so the renderer appends a per-test
  suffix (`editor_a1b2c3`) to every role defined in `roles.yaml`/`users.yaml`
  and rewrites all references. Steps use the logical names and resolve them
  through the scenario handle.
- Mock PostgREST route keys are `"METHOD /path?query"`; query parameters are
  matched order-insensitively. Responses may carry `status`, `json` or
  `text`, `headers` and `content_type`. Unmatched requests return 500 and are
  still recorded. `GET /__requests` returns the spy (newest first),
  `POST /__reset` clears it.
- pgrmapper runs with `PGMAPPER_CACHE_TTL=0` and `PGMAPPER_JWKS_TTL=0` so
  scenario changes are visible immediately.

## Adding tests

Pytest (pure, no containers):

```python
from pgrmapper.app import transform_query

def test_something(monkeypatch):
    monkeypatch.setenv("PGMAPPER_QUERY_POLICY", "reject")
    query, error = transform_query("select=secret", "users", "editor", RULES)
```

Behave (integration): add scenarios to an existing feature or a new
`tests/features/<name>.feature` and reuse the steps in
`tests/steps/common_steps.py`:

```gherkin
Scenario: example
  Given the scenario "users_grants"
  And the query policy is "enforce"
  And a postgrest response for "GET /users?select=user_id" returning JSON []
  When I request GET "/users" with query "select=user_id,status" through the gateway as "editor"
  Then the response status is 200
  And postgrest received the last request "GET /users?select=user_id"
```

- A new database scenario is a directory under
  `tests/fixtures/scenarios/<name>/` with all seven pgprovision yaml files
  (`tables`, `users`, `roles`, `functions`, `grants`, `postgrest`,
  `access_filter`; empty documents are fine).
- Declare PostgREST routes before the first request in the scenario: the mock
  is started lazily and reads its map once.
- Logs for a scenario live in the session temp dir printed at startup; failed
  scenarios also print the pgrmapper log tail, the PostgREST spy and the last
  response to stderr.

## Notes

- Test dependencies live in the `test` dependency group; the driver image
  (`uv sync --frozen`) does not install them.
- Python 3.12 is pinned with `.python-version` to match the driver image.
- The suite never touches a provisioned stack instance (no compose, no
  `.instance`, no published ports).
