# pgmkcurl — build curl commands for PostgREST

`pgmkcurl` prints ready-to-run `curl` commands for PostgREST, so manual API
testing doesn't mean typing long command lines. It is a builder: it does not
execute anything, it only outputs the command.

## Usage

Run inside the driver container. Host and auth options come before the
subcommand:

```sh
pgmkcurl [--host URL] [--token JWT | --role NAME] <get|post|put|patch|delete> PATH [--query Q] [--data JSON]
```

| Option | Description |
|---|---|
| `--host` | PostgREST base URL. Default: `$POSTGREST_URL` env, else `http://postgrest:3000` |
| `--token, -t` | Raw JWT to send as `Authorization: Bearer ...` |
| `--role, -r` | Generate a JWT for this database role on the fly (via pgjwt). Mutually exclusive with `--token` |
| `--query, -q` | URL-encoded query string, appended after `?` (or `&` if the path already has one), e.g. `select=title&done=eq.false` |
| `--data, -d` | JSON body, passed verbatim to `curl -d` (post/put/patch) |

The output is a single shell-quoted command you can copy or `eval`.

## Examples

```sh
# Read: only title, not done
pgmkcurl get todos --query "select=title&done=eq.false"
# curl -sS 'http://postgrest:3000/todos?select=title&done=eq.false'

# Insert as the editor role (JWT generated on the fly)
pgmkcurl --role editor post todos --data '{"id": 1, "title": "x", "done": false}'
# curl -sS -X POST -H 'Authorization: Bearer eyJhbGci...' -H 'Content-Type: application/json' -d '{"id": 1, "title": "x", "done": false}' http://postgrest:3000/todos

# Update a row (filter can live in the path or --query)
pgmkcurl --role editor patch "todos?id=eq.1" --data '{"done": true}'

# Delete with an explicit token
pgmkcurl --token "$TOKEN" delete todos --query "id=eq.1"
```

Run the output directly:

```sh
eval "$(pgmkcurl --role editor post todos --data '{"id": 1, "title": "x"}')"
eval "$(pgmkcurl get todos --query "select=title")" | jq .
```

## Notes

- `--query` must already be URL-encoded (PostgREST query syntax, e.g.
  `order=id.desc&limit=5`); it is appended verbatim.
- `--data` is passed through unchanged — PostgREST accepts single objects,
  arrays for bulk insert, and special constructs (e.g. `Prefer`-style bodies
  are headers, not relevant here).
- Role prerequisites are the same as for `pgjwt` (see `docs/pgjwt.md`): the
  role must exist, be granted to `authenticator` and have table privileges.

See [docs/pgjwt.md](pgjwt.md) and [docs/pgprovision.md](pgprovision.md).
