# pgjwt — JWT generator for PostgREST

`pgjwt` generates HS256 JWTs for PostgREST authorization. It lives in the
driver container (part of the uv project, console script `pgjwt`).

## How PostgREST authorization works

PostgREST does not do passwords. Every request is authorized with a JWT:

1. The JWT is signed with the shared secret configured as `PGRST_JWT_SECRET`
   (HS256/HMAC). The same value is injected into the `postgrest` and
   `driver` containers from `.env`.
2. PostgREST reads the database role from the `role` claim and executes the
   query as that role (`SET LOCAL ROLE`), provided the connection role
   (`authenticator`) is a member of it.
3. Requests without a valid token run as the anonymous role
   (`PGRST_DB_ANON_ROLE`, here `anon`).

Prerequisites for a JWT role to actually work:

- **The role must exist** and be granted to `authenticator`. The baseline
  `anon` role is granted by `db/init/01-roles.sh`; yaml-defined roles need
  `grant_to: [authenticator]` (see `docs/pgprovision.md`).
- **The role needs table privileges** — granted via `grants.yaml`.

## Usage

Run inside the driver container:

```sh
pgjwt --role editor --exp 1h
# eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

Options:

| Option | Description |
|---|---|
| `--role, -r <name>` | Database role for the JWT `role` claim (required) |
| `--exp, -e <duration>` | Validity (`exp` claim), e.g. `1h`, `30m`, `2d` |
| `--claim, -c key=value` | Extra claims (repeatable); values that parse as JSON (numbers, booleans) keep their type |
| `--curl` | Also print a ready-to-use `curl` command |

The secret is read from the `PGRST_JWT_SECRET` environment variable (already
set in the driver); the command fails fast if it is missing.

## Examples

```sh
# Token for the editor role, valid for one hour
pgjwt --role editor --exp 1h

# Token plus a ready-made curl command
pgjwt --role editor --exp 1h --curl
# curl -H "Authorization: Bearer eyJhbGci..." http://postgrest:3000/todos

# Extra typed claims
pgjwt --role editor --claim user_id=42 --claim name=alice
```

Use the token with PostgREST:

```sh
TOKEN=$(pgjwt --role editor --exp 1h)
curl -X POST http://postgrest:3000/todos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": 1, "title": "hello jwt", "done": false}'
# -> 201 Created

curl http://postgrest:3000/todos
# -> [{"id":1,"title":"hello jwt","done":false}]
```

## Error cases

| Symptom | Cause |
|---|---|
| `401 permission denied for table todos` | The JWT role (or `anon` when there is no token) lacks the privilege — check `pgprovision show schema <table>` |
| `401 Expected 3 parts in JWT; got 1` | Malformed/garbage token |
| `401 JWT expired` | The `exp` claim passed |
| `401 Invalid signature` (PGRST3xx) | Token signed with a different secret than `PGRST_JWT_SECRET` |
| `PGRST301 ... role not in the spec` / role switching fails | The JWT role is not granted to `authenticator` (missing `grant_to` or `GRANT`) |

## Implementation

- Library: [python-jose](https://github.com/mpdavis/python-jose) (`jose.jwt`),
  the Python port of jose. Algorithm HS256.
- `--exp` values are parsed by `src/pgjwt/cli.py` (`1h`, `30m`, `2d`) and
  encoded as an absolute `exp` timestamp.
- The `--claim` parser preserves JSON types (e.g. `user_id=42` becomes the
  number 42, `active=true` a boolean, everything else a string).

See [docs/pgprovision.md](pgprovision.md) for provisioning roles and grants.
