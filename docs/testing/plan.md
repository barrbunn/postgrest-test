# Testing plan — pytest + Behave for pgrmapper (v1)

Status: proposed. Implementation starts after review.

## 1. Purpose

Integration-test the pgrmapper components independently of the full compose
stack:

- **routing + identity chain**: live-schema GET routing, 404/405/health, and
  the gateway-mode service-JWT → embedded user-JWT → IdP JWKS chain (plus the
  legacy HS256 path);
- **query policy vs PostgREST**: `PGMAPPER_QUERY_POLICY` =
  `allow`/`enforce`/`reject` for filters, order, logic trees, embeds and
  aliases, asserting both the HTTP result and the exact request that reaches
  PostgREST.

Each component is exercised against real infrastructure where it matters
(a real Postgres, a real pgrmapper process) and mocks where the real
dependency would add no coverage (nginx, IdP, PostgREST).

## 2. Confirmed decisions

| Topic | Decision |
|---|---|
| Runtime | Host, `uv`-managed Python 3.12 venv (`uv run`); not the driver image |
| Postgres | One ephemeral `podman` `postgres:16-alpine` container per session, random host port; one fresh database per test |
| Mock wiring | Real local HTTP servers (uvicorn subprocesses) for mock IdP, mock gateway, mock PostgREST; pgrmapper runs as its own process |
| BDD | Real Behave (`tests/features`) + pytest for unit/component tests; `scripts/test.sh` runs both |
| DB fixtures | Reuse the `provision` package (pgprovision) with a miniature yaml scenario per test |
| Mock gateway | Thin HTTP mock that verifies the user JWT, mints the service JWT exactly like `nginx/conf.d/apigee.js` and forwards to pgrmapper |
| pgrmapper process | Per-test subprocess (fresh DB pool, JWKS and rule caches) |
| Roles | Per-test suffix injected into the yaml (e.g. `admin_a1b2c3`); BDD output keeps the logical name |
| PostgREST mock map | Strict exact matching (`METHOD /path?query`, query order-insensitive); unmatched → 500 + spy record |

v1 component scope (confirmed): **pgrmapper routing + identity chain** and
**query policy vs mock PostgREST**. Caching/refresh, the pgprovision CLI
suite, the njs handler and mTLS are future phases (section 11).

## 3. Architecture

```
pytest / behave (host, uv venv)
   │
   │  scenario fixture: create DB ── pgprovision apply ── pgrmapper subprocess
   ▼
┌────────────────────────────────────────────────────────────────────┐
│ test harness (tests/harness)                                       │
│  ├─ postgres session container (podman, 127.0.0.1:<port>)          │
│  ├─ mock IdP        :<p1>  JWKS + discovery                        │
│  ├─ mock gateway    :<p2>  user JWT → service JWT → pgrmapper      │
│  ├─ mock PostgREST  :<p3>  route-map JSON + request spy            │
│  └─ pgrmapper       :<p4>  PGMAPPER_UPSTREAM → :p3                  │
└────────────────────────────────────────────────────────────────────┘
```

Component independence:

- routing tests talk to pgrmapper directly (no gateway);
- identity tests go through the mock gateway (or send hand-crafted service
  JWTs directly for negative cases);
- policy tests speak gateway-mode through the mock gateway and assert the
  mock PostgREST spy;
- mock IdP/PostgREST/gateway have small pytest unit tests themselves, so a
  harness bug is not misread as a pgrmapper bug.

## 4. Harness specification

### 4.1 Postgres session fixture

- `podman run -d --rm --name pgr-test-suite-pg -p 127.0.0.1:<port>:5432
  -e POSTGRES_PASSWORD=test postgres:16-alpine` (multi-arch image; the port
  is allocated by binding port 0 in Python and closing the socket).
- Readiness: poll `podman exec … pg_isready` (timeout 30s, 250ms interval).
- Bootstrap once per session (mirrors `db/init/01-roles.sh`):
  `CREATE ROLE anon NOLOGIN; CREATE ROLE authenticator NOINHERIT LOGIN …;
  GRANT anon TO authenticator;` — pgprovision role/grants fixtures need these.
- Container removed in a session finalizer (also on failure).

### 4.2 Per-test database and pgprovision fixtures

Per test (or per Behave scenario):

1. `CREATE DATABASE pgr_t_<token>` via the container's `postgres` database.
2. Render the scenario's yaml from `tests/fixtures/scenarios/<name>/` into a
   temp dir:
   - append `_<token>` to every role defined in `roles.yaml` / `users.yaml`;
   - rewrite the same names wherever they are referenced (`grants.yaml`
     `user`, `access_filter.yaml` `role`, role-valued `grant_to`);
   - baseline roles (`anon`, `authenticator`) are never suffixed.
3. Apply with the real CLI:
   `pgprovision apply schemas` with env
   `PGPROVISION_DB_URL=postgresql://postgres:test@127.0.0.1:<port>/pgr_t_<token>`,
   `PGPROVISION_SCHEMAS_ROOT=<temp>/scenarios/<name>`,
   `PGPROVISION_SQL_DIR=<temp>/sql` (keeps generated SQL out of the repo),
   `PGPROVISION_DB_SCHEMA=public`, `PGMAPPER_SCHEMA=pgrmapper`.
   Every scenario ships all seven yaml files (`users: []` when unused);
   `apply schemas` covers tables → roles → users → functions → grants →
   postgrest → access-filter.
4. Teardown: `DROP DATABASE pgr_t_<token> WITH (FORCE)`, then
   `pgprovision teardown roles` (suffixed names) and `DROP ROLE` for any
   leftovers; failures are logged, never fatal (the cluster dies with the
   session container).

A scenario is intentionally tiny, e.g.:

```yaml
# tests/fixtures/scenarios/users_grants/tables.yaml
tables:
  - name: users
    columns:
      - {name: user_id, type: integer, primary_key: true}
      - {name: user_name, type: text, not_null: true}
      - {name: status, type: text, default: active}
```

```yaml
# .../access_filter.yaml
access_filter:
  - {role: editor, table: users, visible_columns: [user_id, user_name]}
```

### 4.3 Mock IdP

FastAPI app in a uvicorn subprocess; config (realm, kid, audience, key path)
passed via a generated JSON file.

- `GET /realms/<realm>/.well-known/openid-configuration`
- `GET /realms/<realm>/protocol/openid-connect/certs` → JWKS built from the
  ephemeral IdP RSA public key (`kid` matches the JWT header).
- User JWTs are minted by the harness (section 4.7), not fetched here; the
  mock only publishes keys, which is all pgrmapper verifies against.

### 4.4 Mock gateway (nginx mimic)

FastAPI app in a uvicorn subprocess with the same claim rules as
`nginx/conf.d/apigee.js`:

- any `GET /...` with `Authorization: Bearer <user JWT>`:
  verify RS256 against the mock-IdP public key, check `exp` / `iss` /
  `aud`, `role = realm_access.roles[0]` (fallback `anon`);
- mint the service JWT (`iss: apigee`, `sub`, `role`, `user_jwt`,
  `exp: now+60`) with the gateway private key;
- forward to pgrmapper with `Authorization: Bearer <service JWT>` and
  `X-User-Role: <role>`; return status, content-type and body unchanged;
- failures → the same 401 JSON bodies as the njs handler.

Tests that need malformed service JWTs (bad signature, expired, wrong
audience, missing embedded user JWT) bypass the mock gateway and call
pgrmapper directly — that is the contract nginx normally guarantees.

### 4.5 Mock PostgREST

FastAPI app in a uvicorn subprocess, loading a route-map JSON at startup.

- Key format: `"METHOD /path?query"`, e.g.
  `"GET /users?select=user_id,user_name"`.
- Matching: method case-insensitive; path exact; query compared as a decoded
  multiset of key/value pairs (order-insensitive, duplicates preserved).
- Response value:

```json
{
  "status": 200,
  "json": [{"user_id": 1, "user_name": "alice"}],
  "headers": {"x-extra": "1"},
  "content_type": "application/json"
}
```

  `json` (any JSON value) and `text` (raw string) are mutually exclusive;
  `status` defaults to 200, `content_type` to `application/json` when `json`
  is present, else `text/plain`.
- Reserved paths: `GET /__requests` returns the spy log
  (`[{method, path, query, headers}]`, newest first) and `POST /__reset`
  clears it.
- Unmatched request → `500 {"error": "mock postgrest: no route for …"}`; the
  request is still recorded in the spy, so failures are loud and debuggable.

### 4.6 pgrmapper under test

- One `pgrmapper` console-script process per test on a free port with:
  - `PGREMAPPER_HOST=127.0.0.1`, `PGREMAPPER_PORT=<port>`
  - `PGMAPPER_DB_URL=postgresql://…/pgr_t_<token>`
  - `PGMAPPER_DB_SCHEMA=public`, `PGMAPPER_SCHEMA=pgrmapper`
  - `PGMAPPER_UPSTREAM=http://127.0.0.1:<mock-postgrest>`
  - `PGMAPPER_GATEWAY_JWT_PUBLIC_KEY=<temp>/gateway-jwt-public.pem`
  - `PGMAPPER_IDP_JWKS_URL=http://127.0.0.1:<mock-idp>/realms/<realm>/protocol/openid-connect/certs`
  - `PGMAPPER_IDP_AUDIENCE=<audience>`
  - `PGMAPPER_QUERY_POLICY=<scenario policy>`
  - `PGMAPPER_CACHE_TTL=0`, `PGMAPPER_JWKS_TTL=0` (no cross-step staleness)
  - `PGRST_JWT_SECRET=<test secret>` (legacy mode)
- Readiness: poll `GET /health` (timeout 10s).
- stdout/stderr captured to the session artifact dir; on failure the tail is
  printed with the test name.

### 4.7 Keys and tokens

- Session-scoped ephemeral RS256 key pairs: IdP key (user JWTs) and gateway
  key (service JWTs), written to the session temp dir as PEM (`kid` derived
  from the JWK thumbprint, or a fixed `test-kid`).
- Helpers:
  - `user_jwt(role, sub="alice", aud=…, iss=…, exp=…)` — IdP key;
  - `service_jwt(user_jwt, role, iss="apigee", exp=…)` — gateway key;
  - `gateway_headers(role, user_jwt=None)` — mints the chain and returns
    `{"Authorization": "Bearer <service>", "X-User-Role": role}`.

## 5. Test asset layout

```
tests/
  harness/
    __init__.py
    postgres.py        # session container, per-test DB, DROP helpers
    scenario.py        # yaml renderer (role suffix) + pgprovision runner
    servers.py         # `python -m tests.harness.servers <kind>` entry point
    mocks/
      idp.py           # JWKS app
      gateway.py       # nginx-mimic app
      postgrest.py     # route-map app + spy
    pgrmapper.py       # spawn/poll/stop pgrmapper, env assembly
    tokens.py          # keypair + JWT helpers
    ports.py           # free-port allocation, readiness polling
  fixtures/
    scenarios/<name>/{tables,users,roles,grants,functions,postgrest,access_filter}.yaml
    postgrest/<name>.json          # route maps
  unit/
    test_query_policy.py           # transform_query matrix per policy
    test_split_top_level.py
    test_tokens.py
    test_postgrest_matcher.py
    test_gateway_claims.py
  features/
    routing.feature
    identity_gateway.feature
    identity_legacy.feature
    query_policy.feature
    postgrest_interaction.feature
  steps/
    common_steps.py                # Given/When/Then against the harness
  environment.py                   # behave session/scenario hooks
```

## 6. Test matrix

### 6.1 pytest (no server, in-process imports)

| Test | What it pins |
|---|---|
| `transform_query` matrix | every policy × {select, filter, order, `and`/`or`/`not.and`, embed select, embed wildcard, alias, JSON path, unknown column}, asserting exact rewritten query or error |
| `split_top_level` | nested parens, quotes, braces, ranges |
| token helpers | RS256 round-trip, aud/iss/exp claims |
| matcher | query normalization, duplicate params, unmatched behavior |
| gateway claims | role extraction, 401 bodies |

These run in milliseconds and are the first line of defence; behave covers the
same behavior through real processes.

### 6.2 Behave features (integration)

Examples (one per component; full set below):

```gherkin
@routing
Scenario: unknown table is not forwarded
  Given the scenario "users_grants"
  And a postgrest response for "GET /users?select=user_id" returning 200 and []
  When I request GET /nope through the gateway as "editor"
  Then the response status is 404
  And postgrest received no requests

@policy-reject
Scenario: hidden column cannot be used as a filter
  Given the scenario "users_grants"
  And the query policy is "reject"
  When I request GET /users with query "select=user_id&status=eq.disabled" through the gateway as "editor"
  Then the response status is 403
  And the response error mentions "users.status"
  And postgrest received no requests

@policy-enforce
Scenario: hidden filter is stripped before forwarding
  Given the scenario "users_grants"
  And the query policy is "enforce"
  And a postgrest response for "GET /users?select=user_id" returning 200 and []
  When I request GET /users with query "select=user_id&status=eq.disabled&limit=2" through the gateway as "editor"
  Then the response status is 200
  And postgrest received the last request "GET /users?select=user_id&limit=2"

@identity-gateway
Scenario: expired user JWT is rejected at the gateway
  Given the scenario "users_grants"
  When I request GET /users through the gateway with an expired user JWT as "editor"
  Then the response status is 401

@identity-legacy
Scenario: anonymous request sees the anon projection
  Given the scenario "users_grants"
  And a postgrest response for "GET /users?select=user_id,user_name" returning 200 and []
  When I request GET /users directly without a token
  Then the response status is 200
  And postgrest received the last request "GET /users?select=user_id,user_name"
```

Feature coverage:

| Feature | Scenarios |
|---|---|
| `routing.feature` | existing table forwarded; unknown table 404 (no upstream call); `/health` 200; root `/` forwarded; POST/PATCH/DELETE → 405 |
| `identity_gateway.feature` | valid chain passes role from `X-User-Role`; missing/`Bearer` garbage → 401; expired/wrong-audience/tampered user JWT → 401; service JWT bad signature / expired / missing embedded `user_jwt` → 401 |
| `identity_legacy.feature` | HS256 role claim used; no token → `anon`; invalid token → `anon` |
| `query_policy.feature` | reject/enforce/allow across select, filters, order, `or=`/`not.and=`, embed select, embed wildcard, alias, JSON path; 403 message; spy assertions for forwarded URLs |
| `postgrest_interaction.feature` | Authorization/Accept forwarded; upstream status + content-type passthrough; pgrmapper 404/403 paths never call PostgREST |

## 7. Running the suite

```
scripts/test.sh              # uv sync --group test && pytest tests/unit && behave tests/features
scripts/test.sh pytest       # unit/component only
scripts/test.sh behave       # BDD only, optionally a feature file
scripts/test.sh behave tests/features/query_policy.feature
```

`pyproject.toml` gains a `[dependency-groups] test` group (`pytest`,
`behave`; `uvicorn`/`httpx`/`jose` are already project dependencies). The
driver image runs `uv sync --frozen`, which does **not** install the `test`
group, so the image is unaffected. `uv.lock` is regenerated with
`uv lock` on the host.

The script preflights `podman machine` availability and exits with a clear
message (the suite needs a running podman machine on macOS).

## 8. Determinism and diagnostics

- No `sleep`-based waiting: readiness polling with timeouts on postgres,
  each HTTP mock and pgrmapper.
- Caches pinned to TTL 0; per-test pgrmapper process removes all cross-test
  state; strict route maps make unexpected upstream requests fail loudly.
- Every subprocess's stdout/stderr goes to a session temp dir (path printed
  at session start); on failure the harness prints the pgrmapper log tail,
  the mock gateway's last exchange and the PostgREST spy.
- Ports are allocated per server from ephemeral ports; collisions fail the
  readiness poll with the port in the message.

## 9. Implementation phases

1. Scaffolding: `test` dependency group, `scripts/test.sh`, harness package,
   postgres session fixture, ports/readiness helpers.
2. pgprovision scenario renderer + per-test DB lifecycle.
3. Mock servers (IdP, gateway, PostgREST) + their pytest unit tests.
4. pgrmapper runner + pytest policy/unit matrix.
5. Behave environment/steps + `routing.feature` + `identity_*.feature`.
6. `query_policy.feature` + `postgrest_interaction.feature`.
7. `docs/testing/README.md` (how to run, how to add scenarios) and an
   `AGENTS.md` pointer.

## 10. Out of scope (future phases)

- pgrmapper caching/refresh behavior (JWKS TTL, access_filter TTL, live
  schema changes, pool limits).
- pgprovision CLI round-trips (`create/apply/check/teardown` parity).
- `nginx/conf.d/apigee.js` njs unit tests (needs nginx + njs harness) and
  real mTLS between gateway and pgrmapper.
- Real PostgREST, RLS/column-grant behaviors, performance/load tests, CI
  wiring.
