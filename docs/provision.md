# Provisioning

`scripts/provision.sh` manages the lifecycle of the PostgREST test stack and
guarantees that it never collides with other local containers or networks.

## Why

Other containers with their own Postgres instances run on this machine. To
avoid accidental name collisions, every provisioning run creates a **new
instance name** (`pgr-test-01`, `pgr-test-02`, ...) and passes it to compose
with `-p`. This prefixes everything the stack creates:

| Resource | Name |
|---|---|
| Containers | `pgr-test-NN-postgres-1`, `pgr-test-NN-postgrest-1`, `pgr-test-NN-driver-1`, `pgr-test-NN-pgrmapper-1`, `pgr-test-NN-gateway-1`, `pgr-test-NN-idp-1`, `pgr-test-NN-pgproxy-postgrest-1`, `pgr-test-NN-pgproxy-driver-1` |
| Network | `pgr-test-NN_internal` |
| Volume | `pgr-test-NN_pgdata` |

## Usage

```sh
scripts/provision.sh          # down any previous instance, up a fresh one
scripts/provision.sh down     # tear down all instances of this stack
scripts/provision.sh status   # show current instance and its containers
```

## Behavior

1. **Pick a name**: next number after the state file (`.instance`) or the
   highest numbered `pgr-test-*` project found via `compose ls --all`.
2. **Tear down** every previous instance of this stack, including the legacy
   default project name (`postgrest-pgproxy`), with `--remove-orphans
   --volumes`. Data is deliberately thrown away: each instance starts from a
   fresh volume, so `db/init/01-roles.sh` runs on every provision.
3. **Bring up** `podman compose -p <name> up -d --build`. `.env` is copied
   from `.env.example` if missing, and the test mTLS certificates are
   generated into `certs/` when missing (`scripts/gen-certs.sh`).

The current instance name is stored in `.instance` (gitignored). Use it to
talk to a running instance:

```sh
podman compose -p "$(cat .instance)" exec driver sh
podman compose -p "$(cat .instance)" ps
```

Notes:

- **Only the nginx gateway (`NGINX_PORT`, default 8080) and the IdP
  (`IDP_PORT`, default 5151) publish host ports.** Everything else is
  reachable only from containers on the internal network. From the host:

  ```sh
  ALICE=$(scripts/idp-token.sh alice alice123)          # user JWT from the IdP
  curl -H "Authorization: Bearer $ALICE" http://localhost:8080/todos
  ```

  The gateway mimics Apigee: verifies the user JWT, signs a service JWT and
  forwards to pgrmapper over mTLS (see `docs/infra/apigee-mimic.md`). The
  `pgrmapper` container runs the code baked into the image — after changing
  `src/pgrmapper`, re-provision to rebuild it (or run a live copy in the
  driver for debugging).
- PostgREST retries the database connection on startup; right after
  provisioning it may answer `503` for a few seconds until its pgproxy
  sidecar is listening.
- Recreating the `postgrest` container alone fails (its pgproxy sidecar pins
  its network namespace via `network_mode: service:`), so always use
  `provision.sh down` / `provision.sh` rather than compose `restart`/`down`
  on individual services.
