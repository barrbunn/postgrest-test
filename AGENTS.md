# AGENTS.md

## What this project is

A local test rig for PostgREST, wired exactly like a production deployment
would be: PostgREST does **not** connect to the database directly. Instead a
pgproxy sidecar shares its network namespace and exposes the remote Postgres
as a plain `127.0.0.1:5432` ("local database"). A test driver container uses
the same proxied path.

The pgproxy sidecars are deliberately **separate containers** attached to the
service container via `network_mode: service:` — the compose equivalent of a
Kubernetes pod with a sidecar. That is the intended, standard pattern; do not
"merge" the proxy into the postgrest/driver images.

```
host ── nginx gateway (:NGINX_PORT) ──(njs: VerifyJWT + GenerateJWT + mTLS)── pgrmapper ── postgrest ══ pgproxy ══ postgres
  │         (filters ?select= by role)                         (PGRST_DB_URI → 127.0.0.1:5432)
  └── idp (keycloak-mockup :IDP_PORT, user JWTs + JWKS)          driver (tests/debug: postgrest:3000,
                                                                         DATABASE_URL → 127.0.0.1:5432)
```

Only the gateway and the idp publish host ports. The gateway mimics Apigee:
njs verifies the user JWT from the IdP, signs a service JWT (RS256) embedding
it, and forwards to pgrmapper over mTLS with the role in `X-User-Role` (see
`docs/infra/apigee-mimic.md`). The driver keeps direct internal access and
can run a live-edited pgrmapper for debugging (legacy HS256 auth).

## Layout

- `compose.yaml` — 8 services: `postgres`, `postgrest`, `driver`,
  `pgrmapper` (filtering proxy), `gateway` (nginx + njs, Apigee mimic),
  `idp` (keycloak-mockup), and the two pgproxy sidecars
  (`network_mode: service:`). All config comes from `.env`.
- `.env.example` — the only config file; copied to `.env` automatically.
- `scripts/provision.sh` — the ONLY supported way to start/stop the stack
  (see `docs/provision.md`). Creates uniquely named instances (`pgr-test-NN`)
  so nothing collides with other local Postgres containers.
- `scripts/jwt.sh` — host-side JWT generation (legacy HS256): sources `.env`
  and runs the `pgjwt` CLI via uv.
- `scripts/idp-token.sh` — host-side user JWT acquisition from the IdP
  (authorization-code flow via curl): `scripts/idp-token.sh alice alice123`.
- `scripts/gen-certs.sh` — generates the test CA, the mTLS certs, the IdP
  RSA keypair, the gateway JWT signing keypair, `idp-config.yaml` and the
  PostgREST JWK set (`jwt-secrets.json`) into `certs/` (gitignored).
  `provision.sh` regenerates them when missing.
- `nginx/` — `nginx.conf` (main config with the njs module),
  `conf.d/gateway.conf` (Apigee-mimic routing + mTLS backend) and
  `conf.d/apigee.js` (VerifyJWT/GenerateJWT handler).
- `idp/config.example.yaml` — keycloak-mockup config reference (users/roles);
  `gen-certs.sh` renders it into `certs/idp-config.yaml` with the IdP private
  key inlined.
- `pgproxy/` — vendored multi-arch Go TCP relay (env contract:
  `PGPROXY_LISTEN`, `PGPROXY_TARGET`). Rationale: `docs/infra/pgproxy.md`.
- `db/init/01-roles.sh` — creates `anon`/`authenticator` roles on a fresh
  volume.
- `driver/` — minimal test container (python + curl + psql + jq + uv, sleeps).
  Runs the python project below; `./src`, `./data`, `pyproject.toml` and
  `uv.lock` are mounted so local edits (including CLI entry points) are live
  in the container.
- `pyproject.toml` / `uv.lock` — uv project at the repo root with four
  packages: `src/provision`, `src/pgjwt`, `src/pgmkcurl` and `src/pgrmapper`
  (see below).
- `data/provision/` — yaml templates (default working dir `schemas/`,
  example scenarios under `examples/<scenario>/`) and generated SQL
  (`sql/`, versioned in git).

## Python tooling (pgprovision + pgjwt + pgrmapper)

Managed by **uv**. The driver image installs the project editable at build
time (`uv sync --frozen`), so you edit `src/` locally and run in the driver.
**Dependency changes require re-provisioning** (runtime has no internet):
`scripts/provision.sh` rebuilds the driver image. Full usage:
`docs/pgprovision.md` (provisioning) and `docs/pgjwt.md` (authorization).

### Intended workflow

```sh
# 1. fresh stack instance
scripts/provision.sh
# 2. everything runs inside the driver:
podman compose -p "$(cat .instance)" exec driver sh
export PGPROVISION_SCHEMAS_ROOT=data/provision/examples/todos
export PGPROVISION_AUTORELOAD_SCHEMAS=true        # optional: apply auto-reloads PostgREST's schema cache
pgprovision create schemas                        # 3. generate versionable SQL from yaml
pgprovision apply schemas                         # 4. run it against the DB
pgprovision check schemas                         # 5. verify DB == yaml (exit 1 on mismatch)
pgprovision show schema todos                     # 6. inspect columns + POST example + grants
TOKEN=$(pgjwt --role editor --exp 1h)             # 7. authorize as a role via JWT
eval "$(pgmkcurl --role editor post todos --data '{"id": 1, "title": "x"}')"
eval "$(pgmkcurl get todos --query "select=title")"
pgprovision teardown schemas                      # 8. reset the schema without re-provisioning
```

### Commands

- `pgprovision create {schemas|tables|users|roles|functions|grants|postgrest|access-filter}` —
  generates SQL from yaml templates into `data/provision/sql/` (versioned).
- `pgprovision apply {schemas|tables|users|roles|functions|grants|postgrest|access-filter}` —
  runs the generated SQL against Postgres (schemas order: tables, roles,
  users, functions, grants, postgrest, access-filter). The access-filter
  domain replaces the ruleset on every apply (idempotent), unlike the
  plain-CREATE domains.
- `pgprovision check schemas` / `pgprovision check schema <table>` — compares
  the live database against the yaml definitions; exit 1 on mismatch.
- `pgprovision teardown {schemas|access-filter|postgrest|functions|tables|users|roles}` —
  drops/resets the yaml-defined objects (idempotent); the way to clean up
  before re-applying without re-provisioning.
- `pgprovision reload-schema` — `NOTIFY pgrst, 'reload schema'` so PostgREST
  picks up tables created after it started. `pgprovision reload-config` —
  `NOTIFY pgrst, 'reload config'` to apply in-database config changes.
  `PGPROVISION_AUTORELOAD_SCHEMAS=true` makes every `apply` send both
  (default `false`).
- `pgprovision show schemas` / `pgprovision show schema <table>` — display the
  live schema (columns, required fields, example POST body, grants).
- `pgjwt` — JWT generator for PostgREST (python-jose, HS256, secret from
  `PGRST_JWT_SECRET`, which the driver also has): `pgjwt --role editor --exp 1h`.
  PostgREST runs the request as the `role` claim; that role must be granted
  to `authenticator` (yaml `grant_to`, baseline `anon` is pre-granted) and
  have table grants. See `docs/pgjwt.md`.
- `pgmkcurl` — builds curl commands for PostgREST (prints, doesn't run):
  `pgmkcurl --role editor post todos --data '{"id": 1, "title": "x"}'`,
  `pgmkcurl get todos --query "select=title&done=eq.false"`. See
  `docs/pgmkcurl.md`.
- The driver now has a `psql` client (`psql "$DATABASE_URL"` goes through
  pgproxy).
- `pgrmapper` — read-only FastAPI filtering proxy in front of PostgREST:
  validates GET routes against the live DB schema per request, extracts the
  role, looks up `(role, table)` in the pgrmapper `access_filter` table and
  rewrites `?select=` to the visible columns, then forwards the request to
  PostgREST. Two identity modes: **gateway mode** (Apigee mimic — verifies
  the RS256 service JWT from nginx and the embedded user JWT against the IdP
  JWKS, role from `X-User-Role`) and **legacy mode** (HS256
  `PGRST_JWT_SECRET`, role from the JWT; anonymous when no token). Runs in
  its own container (baked image, `gateway`'s single backend); run a
  live-edited plaintext copy manually in the driver for debugging
  (`pgrmapper`, port 8000). See `docs/pgrmapper.md` and
  `docs/infra/apigee-mimic.md`.

All paths/names follow the pattern *sensible default + env override*:
`PGPROVISION_SCHEMAS_ROOT` (`./data/provision/schemas`),
`PGPROVISION_TABLES_FILE`/`_USERS_FILE`/`_ROLES_FILE`/`_GRANTS_FILE`/
`_FUNCTIONS_FILE`/`_POSTGREST_FILE`/`_ACCESS_FILTER_FILE` (`tables.yaml` etc.),
`PGPROVISION_SQL_DIR` (`./data/provision/sql`),
`PGPROVISION_DB_URL` (default built from `POSTGRES_*` envs),
`PGPROVISION_DB_SCHEMA` (`public`), `PGMAPPER_SCHEMA` (`pgrmapper`, shared
with pgrmapper).

Yaml shape (see `data/provision/examples/<scenario>/*.yaml` for examples):
tables
(`name`, `columns` with `name`/`type`/`primary_key`/`not_null`/`default`),
users (`name`, `password`, `login`, `grant_to`), roles (`name`,
`login: false`, `grant_to`), grants
(`user`, `tables`, `permissions`), functions (`name`, `returns`, `language`,
`args`, `body`), postgrest (`role`, `settings` map of in-database
`pgrst.*` values — overrides container env, applied via 'reload config'
without a restart), access_filter (`role`, `table`, `visible_columns` — for
the pgrmapper proxy; apply replaces the ruleset). Generated SQL is plain
`CREATE`/`GRANT`/`ALTER ROLE` statements — re-applying to an existing object
fails by design (functions use `CREATE OR REPLACE`); each provision starts
from a fresh volume.

## Environment facts (do not fight these)

- **No `docker` CLI.** Use `podman` (6.1, arm64 VM). `podman compose` delegates
  to docker-compose v5.4.0 and works for this stack.
- Apple Silicon: amd64-only images run under qemu-user emulation where **Go
  binaries crash at startup** (`taggedPointerPack`). Only use multi-arch
  images.
- The compose network is `internal: true`: containers have **no internet at
  runtime**. Anything the driver needs must be installed in
  `driver/Dockerfile` (build-time network is fine).

## Common tasks

```sh
scripts/provision.sh                     # fresh instance
podman compose -p "$(cat .instance)" exec driver sh   # shell in driver
podman compose -p "$(cat .instance)" ps  # status
scripts/provision.sh down                # tear everything down
```

```sh
# from the host (through the API gateway, port from .env NGINX_PORT):
ALICE=$(scripts/idp-token.sh alice alice123)          # user JWT from the IdP
curl -H "Authorization: Bearer $ALICE" http://localhost:8080/todos
TOKEN=$(scripts/jwt.sh --role manager --exp 1h)       # legacy HS256 JWT (driver-side flows)
```

(In-driver tool usage: see "Intended workflow" above; paths in the driver
are relative to `/app` and the mounts mirror the repo.)

- Tests run from the `driver`: `curl http://postgrest:3000` (through
  pgproxy), direct DB at `postgres:5432`, proxied DB at `127.0.0.1:5432`
  (`DATABASE_URL`).
- **Only the nginx gateway (`NGINX_PORT`) and the idp (`IDP_PORT`) publish
  host ports.** Everything else (postgres, postgrest, pgrmapper) is reachable
  only from containers on the internal network. The gateway routes to
  pgrmapper (mTLS + service JWT), which forwards to PostgREST.

## Gotchas

- `PGRST_JWT_SECRET` must be at least 32 characters or postgrest exits.
- Cannot recreate the `postgrest` container alone: `pgproxy-postgrest` pins
  its network namespace. Always use `provision.sh`.
- Right after provisioning, PostgREST may return `503` for a few seconds
  until its pgproxy sidecar is listening (it retries automatically).
- `db/init/*` only runs on a fresh volume; `provision.sh down` removes
  volumes, so every provision starts clean.
- New python dependencies require a driver image rebuild (no runtime
  internet): change `pyproject.toml`, run `uv lock`, then `provision.sh`.
- The example provision yamls deliberately avoid the baseline roles
  `anon`/`authenticator` — generated SQL is plain `CREATE`, so applying a
  role that `db/init` already created fails by design. Grants may reference
  `anon` freely (GRANT is additive); anonymous PostgREST requests run as
  `anon`, so it needs table grants for the API to serve the table.
- Keep the pgproxy env contract (`PGPROXY_LISTEN`, `PGPROXY_TARGET`) stable —
  it matches the upstream `dgvan/pgproxy` image so the two are drop-in
  interchangeable (see `docs/infra/pgproxy.md`).
- `.env` and `.instance` are gitignored; never commit secrets.
