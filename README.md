# PostgREST test rig with Apigee-style gateway

A local test rig for PostgREST, wired like a production GCP deployment: the
client gets a user JWT from an identity provider, an nginx gateway mimics
Apigee (VerifyJWT + GenerateJWT + mTLS), a filtering microservice rewrites
`?select=` projections per role, and PostgREST never connects to the
database directly — a pgproxy sidecar makes the remote Postgres look local.

```
host ── nginx gateway (:8080) ──(njs: VerifyJWT + GenerateJWT + mTLS)── pgrmapper ── postgrest ══ pgproxy ══ postgres
  │         (filters ?select= by role)                          (PGRST_DB_URI → 127.0.0.1:5432)
  └── idp (keycloak-mockup :5151, user JWTs + JWKS)               driver (tests/debug, DATABASE_URL → 127.0.0.1:5432)
```

## Requirements

- **macOS (Apple Silicon)** with **Podman 6** and a podman machine
  (`podman compose` delegates to docker-compose v5.4.0; no `docker` CLI
  needed)
- **uv** on the host (host-side scripts run the python project)
- **curl** + **python3** on the host (`jq` optional, for pretty output)
- Network access at build/provision time (the stack itself is on an
  internal network with no internet at runtime)
- ~2 GB free in the podman VM for image builds

## Setup

```sh
cp .env.example .env            # edit ports/secrets if needed
./scripts/provision.sh          # tears down any previous instance,
                                # generates test certificates,
                                # starts pgr-test-NN (unique name every run)
```

That brings up 8 containers: `postgres`, `postgrest`, `driver`, `pgrmapper`,
`gateway` (nginx, the only API entry), `idp` (keycloak-mockup) and the two
pgproxy sidecars. Only the gateway (`NGINX_PORT`, default 8080) and the IdP
(`IDP_PORT`, default 5151) are reachable from the host.

### Load the example schema

```sh
podman compose -p "$(cat .instance)" exec driver sh
export PGPROVISION_SCHEMAS_ROOT=data/provision/examples/todos
export PGPROVISION_AUTORELOAD_SCHEMAS=true
pgprovision create schemas && pgprovision apply schemas
```

This creates the `todos` table, the `editor`/`manager`/`viewer` roles,
grants, a pre-request function and the pgrmapper column-visibility rules.

### Get a token and call the API (from the host)

```sh
ALICE=$(scripts/idp-token.sh alice alice123)        # user JWT from the IdP
curl -H "Authorization: Bearer $ALICE" http://localhost:8080/todos
```

IdP users (`idp/config.example.yaml`): alice/`editor`, bob/`manager`,
carol/`viewer`. Each role sees a different column projection. Requests
without a valid user JWT are rejected with 401 by the gateway.

## The tools

| Tool | Purpose | Docs |
|---|---|---|
| `pgprovision` | create/apply/check/teardown/show schemas from yaml; access_filter rules; PostgREST in-database config | `docs/pgprovision.md` |
| `pgjwt` | generate JWTs (legacy HS256, driver-side flows) | `docs/pgjwt.md` |
| `pgmkcurl` | build curl commands for PostgREST | `docs/pgmkcurl.md` |
| `pgrmapper` | read-only filtering proxy (access_filter projections) | `docs/pgrmapper.md` |
| `scripts/provision.sh` | stack lifecycle, uniquely named instances | `docs/provision.md` |
| `scripts/idp-token.sh` | user JWT from the IdP (host) | — |
| `scripts/jwt.sh` | legacy JWT from `.env` (host) | — |

More background:

- `docs/infra/apigee-mimic.md` — the full GCP JWT flow and how it is mimicked
- `docs/infra/pgproxy.md` — why the pgproxy sidecars exist and are vendored

## Notes and gotchas

- Re-provisioning gives a fresh database; `pgprovision teardown schemas` +
  `apply schemas` resets it without re-provisioning.
- Editing `idp/config.example.yaml` (users) or `src/` code used by baked
  containers requires `rm certs/ca.crt && ./scripts/provision.sh` to take
  effect (certs are only regenerated when missing).
- The podman VM disk is shared with other local projects; if builds fail
  with "no space left on device", run `podman image prune -f`.
- `.env`, `.instance`, `certs/` are gitignored; never commit secrets.
