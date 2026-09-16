# pgproxy selection and usage

## Why a proxy layer at all?

In this stack the real PostgreSQL server runs in its own container on an
internal network. Both the PostgREST container and the test driver container
connect to it through a pgproxy sidecar that shares their network namespace.
This makes the remote database appear as a plain `127.0.0.1:<port>` service
("a local database") to:

- **PostgREST** — `PGRST_DB_URI` simply points at `127.0.0.1`.
- **The driver** — tests exercise the exact same path (`DATABASE_URL`) that
  the production-facing app uses.

The proxy layer also gives us a place to observe and shape traffic later:
query logging, latency injection, and connection dropping for failure-mode
testing of PostgREST (e.g. schema-cache reload and reconnection behavior).

## Candidate evaluation

`pgproxy` is a heavily reused name. Candidates considered (as of 2026-09):

| Project | Stars / pulls | Last activity | Notes |
|---|---|---|---|
| `dgvan/pgproxy` (Docker Hub) | 4.5K pulls | 2025 | Purpose-built simple TCP proxy. Documented env config (`PGPROXY_LISTEN`, `PGPROXY_TARGET`, timeouts, conn limits). 5.8 MB. No public source repo. amd64 only. |
| `phamviet/pgproxy` (Docker Hub) | 6.8K pulls | 2024 | No description, no documentation, single `latest` tag, 51 MB. Not usable with confidence. |
| `wgliang/pgproxy` (GitHub) | 223 stars | 2017 | Most-starred. SQL rewriting/filtering experiment, abandoned, no Docker image. Overkill and dead. |
| Tailscale `pgproxy` | part of tailscale repo | active | Requires Tailscale connectivity on both ends; not suitable for a local compose network. |
| `mcfunley/pgproxy` | 28 stars | 2015 | Python unit-test proxy, abandoned. |
| `mikelfx/pgproxy`, `bexio/pgproxy`, `hasuraci/pgproxy` | <1K pulls | 2024–2025 | No docs, single-arch, unclear config. |

## Decision

On merit, the winner is **`dgvan/pgproxy`** — the most-used image that is
actually a simple, documented, purpose-built PostgreSQL proxy. We adopted its
environment contract (`PGPROXY_LISTEN`, `PGPROXY_TARGET`) so any future switch
is a one-line compose change.

**Why we vendored our own build anyway** (verified against this repo's test
environment, podman 6.1 on Apple Silicon):

1. `dgvan/pgproxy:1.0` is **amd64-only** (no arm64 manifest, no public source
   to rebuild).
2. Under podman's qemu-user emulation the image **crashes at startup** with a
   Go runtime error (`taggedPointerPack invalid packing` in `netpollopen`) —
   a known qemu-user/Go runtime incompatibility. Verified by running the
   image and trying `GODEBUG=asyncpreemptoff=1`.
3. podman 6.1 offers no Rosetta option, so there is no way to run that image
   in this environment without installing Docker Desktop.

So `pgproxy/` in this repo is a ~40-line Go TCP relay with:

- the same env contract (`PGPROXY_LISTEN`, default `:6432`, and
  `PGPROXY_TARGET`, required);
- a multi-arch build (native `arm64` + `amd64` via `golang:1.24-alpine`
  builder, `alpine:3.20` runtime, runs as non-root);
- a verified end-to-end smoke test: `psql` connecting to a remote Postgres
  through the proxy on `127.0.0.1` (done with podman before adoption).

It is intentionally dumb (no pooling, no parsing, no TLS) — the same design
point as `dgvan/pgproxy` — which is all a test rig needs.

## Wiring in compose

```
postgres (internal net)  <-- pgproxy-postgrest  ~~  postgrest (PGRST_DB_URI=127.0.0.1)
                            network_mode: service:postgrest

postgres (internal net)  <-- pgproxy-driver     ~~  driver (DATABASE_URL=127.0.0.1)
                            network_mode: service:driver
```

- `pgproxy-postgrest` and `pgproxy-driver` run `network_mode: service:<X>`,
  sharing the target container's network namespace, so their listener shows
  up on that container's loopback.
- Both dial the upstream `PGPROXY_TARGET=postgres:5432` over the `internal`
  network.
- Startup race is a non-issue: PostgREST retries the DB connection on startup
  (changelog #742, since v5.2), and services use `restart: unless-stopped`.

## Switching to the upstream image

On an amd64 host (or Docker Desktop with Rosetta) you can drop in the
upstream image by replacing, in both pgproxy services of `compose.yaml`:

```yaml
    build: ./pgproxy
    image: postgrest-pgproxy/pgproxy:local
```

with:

```yaml
    image: dgvan/pgproxy:1.0
```

The env contract is the same, so no other change is needed.
