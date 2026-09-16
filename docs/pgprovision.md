# pgprovision — schema provisioning tool

`pgprovision` builds test schemas for PostgREST from yaml templates. SQL is
never written by hand: it is **generated** from yaml, **saved** as versionable
`.sql` files, and then **applied** to the database as a separate step. This
lets you commit the SQL and apply different versions when testing.

Run it inside the driver container:

```sh
podman compose -p "$(cat .instance)" exec driver sh
```

## Commands

### `pgprovision create`

Generates SQL files from yaml templates into the SQL output dir
(`data/provision/sql` by default). Never touches the database.

```sh
pgprovision create schemas     # all domains: tables, roles, users, functions, grants, postgrest, access-filter
pgprovision create tables      # tables.yaml      -> sql/tables.sql
pgprovision create roles       # roles.yaml       -> sql/roles.sql
pgprovision create users       # users.yaml       -> sql/users.sql
pgprovision create functions   # functions.yaml   -> sql/functions.sql
pgprovision create grants      # grants.yaml      -> sql/grants.sql
pgprovision create postgrest   # postgrest.yaml   -> sql/postgrest.sql
pgprovision create access-filter  # access_filter.yaml -> sql/access-filter.sql
```

Each generated file starts with a header comment naming the source yaml, so
the versioned SQL is traceable.

### `pgprovision apply`

Runs the generated SQL files against the database.

```sh
pgprovision apply schemas     # applies in order: tables, roles, users, functions, grants, postgrest
pgprovision apply tables      # applies sql/tables.sql
pgprovision apply roles       # applies sql/roles.sql
pgprovision apply users       # applies sql/users.sql
pgprovision apply functions   # applies sql/functions.sql
pgprovision apply grants      # applies sql/grants.sql
pgprovision apply postgrest   # applies sql/postgrest.sql (in-database config)
pgprovision apply access-filter  # applies sql/access-filter.sql (replaces the ruleset)
```

### `pgprovision access-filter`

Provisions the internal table used by the `pgrmapper` filtering proxy
(`docs/pgrmapper.md`), from `access_filter.yaml`:

```yaml
access_filter:
  - role: anon
    table: todos
    visible_columns: [id, title]
  - role: editor
    table: todos
    visible_columns: [id, title, done, priority]
```

The table is created in the `PGMAPPER_SCHEMA` schema (default `pgrmapper`,
shared with pgrmapper via the same env var), outside `db-schemas`, so
PostgREST never exposes it. Unlike the other domains, **apply replaces the
ruleset**: `CREATE ... IF NOT EXISTS` + `DELETE FROM` + `INSERT` the yaml
rows, so re-applying after editing the yaml just works. `teardown
access-filter` drops the table.

**Hot reload**: pgrmapper reads the access_filter table on every request, so
editing the yaml + `create access-filter` + `apply access-filter` takes
effect on the next request — no re-provisioning, no container restart.
An empty `visible_columns: []` blocks the table for that role (the proxy
answers 403).

Generated SQL is plain `CREATE`/`GRANT` — applying to an existing object
fails by design. `apply` prints the database error plus a hint and exits 1
(no traceback). Use `pgprovision teardown schemas` to clean up first, or
re-provision for a fresh volume.

### `pgprovision teardown`

Drops the objects defined in the yaml templates — the inverse of `apply`.

```sh
pgprovision teardown schemas    # all defined objects (config, functions, tables, roles, users)
pgprovision teardown postgrest  # reset the in-database PostgREST settings
pgprovision teardown functions  # drop the functions from functions.yaml
pgprovision teardown tables     # drop the tables from tables.yaml (CASCADE)
pgprovision teardown roles      # drop the roles from roles.yaml
pgprovision teardown users      # drop the roles from users.yaml
```

- Idempotent (`DROP ... IF EXISTS`); safe to run on an already-clean
  database.
- Grants are removed automatically with their tables/roles, so there is no
  `teardown grants`.
- Only objects listed in the yamls are dropped — the baseline roles
  (`anon`/`authenticator`) and anything else in the database are untouched.

Typical test cycle without re-provisioning:

```sh
pgprovision teardown schemas && pgprovision apply schemas && pgprovision check schemas
```

### `pgprovision check`

Compares the live database against the yaml definitions. Exit code 0 when
everything matches, 1 when mismatches are found.

```sh
pgprovision check schemas        # verify all tables, users, roles, grants
pgprovision check schema todos   # verify a single table
```

Notes:

- Tables/columns present in the database but not in the yaml are reported as
  `UNMANAGED` (extra tables are informational; an extra column on a
  yaml-managed table is a mismatch).
- Postgres makes primary-key columns implicitly `NOT NULL`; the generator
  emits that explicitly and the checker expects it, so `not_null` is only
  needed for non-PK columns.
- Column `type` comparison allows common aliases (`int`, `serial`, `bool`,
  `varchar`, `string`, ...).

### `pgprovision show`

Shows the **live** database schema to help build PostgREST requests.

```sh
pgprovision show schemas        # list tables in the schema
pgprovision show schema todos   # columns, required fields, POST example, grants
```

```
table todos (schema public)
  id     integer  PRIMARY KEY, NOT NULL
  title  text     NOT NULL
  done   boolean  DEFAULT false

required for POST: id, title

POST /todos with body:
{
  "id": 0,
  "title": "example",
  "done": false
}

grants: anon=select, editor=delete,insert,select,update, test_anon=select
```

`required for POST` are the columns that are `NOT NULL` (or primary key) and
have no default — those must appear in a POST body. The example body shows a
sane value per column type. Grants are shown excluding the table owner's
implicit privileges.

### `pgprovision reload-schema` / `reload-config`

PostgREST builds its schema cache at startup and does not see tables created
afterwards until the cache is reloaded. Likewise, in-database configuration
changes (see below) only take effect after a config reload:

```sh
pgprovision reload-schema   # NOTIFY pgrst, 'reload schema'
pgprovision reload-config   # NOTIFY pgrst, 'reload config'
# equivalent, with psql:
psql "$DATABASE_URL" -c "NOTIFY pgrst, 'reload schema'"
psql "$DATABASE_URL" -c "NOTIFY pgrst, 'reload config'"
```

Set `PGPROVISION_AUTORELOAD_SCHEMAS=true` (default `false`) to make every
`apply` send both reloads automatically.

## In-database PostgREST configuration

`postgrest.yaml` sets PostgREST configuration **in the database**, which
overrides the container env vars and — unlike env vars — can be changed on a
running server via `NOTIFY pgrst, 'reload config'` (no container rebuild, no
restart, no enforced function names):

```yaml
postgrest:
  role: authenticator
  settings:
    db_pre_request: auth.check_token
```

generates:

```sql
ALTER ROLE authenticator SET pgrst.db_pre_request = 'auth.check_token';
```

- Setting keys are the in-database names from the PostgREST config docs
  (`db_pre_request`, `db_max_rows`, `jwt_aud`, ...), values are strings.
- `teardown postgrest` resets the settings (`ALTER ROLE ... RESET pgrst.*`).
- This is the legacy `ALTER ROLE` mechanism (PostgREST now recommends a
  `db-pre-config` function instead), chosen here because the rig's `app`
  role is superuser and every value stays fully dynamic. See the
  [PostgREST configuration docs](https://docs.postgrest.org/en/v16/references/configuration.html#in-database-configuration).

### Pre-request hook example

The classic use case is a `db-pre-request` function, e.g. the tutorial's
"Immediate Revocation" pattern. Define it in `functions.yaml`:

```yaml
functions:
  - name: auth.check_token
    returns: void
    language: plpgsql
    body: |
      begin
        if current_setting('request.jwt.claims', true)::json->>'iss' <>
           current_setting('request.headers', true)::json->>'origin' then
          raise insufficient_privilege using hint = 'Token origin mismatch';
        end if;
      end
```

Schema-qualified names get their schema created automatically
(`CREATE SCHEMA IF NOT EXISTS` + `GRANT USAGE ... TO PUBLIC`), so the
function is callable for every request role. Wire it up in `postgrest.yaml`
(`db_pre_request: auth.check_token`), apply, and PostgREST calls it before
every request — verified by toggling it live with `teardown postgrest` /
`apply postgrest` + `reload-config`.

## Exposing schemas through PostgREST

To serve a table through the REST API you need two things:

1. **Privileges for the `anon` role** — anonymous HTTP requests run as the
   role configured in `PGRST_DB_ANON_ROLE` (here: `anon`, created by
   `db/init`). `grants.yaml` must grant it permissions on the table (the
   example grants `SELECT` to `anon`).
2. **A schema cache reload** — see above. With
   `PGPROVISION_AUTORELOAD_SCHEMAS=true`, `apply` does this for you.

```sh
export PGPROVISION_SCHEMAS_ROOT=data/provision/examples/todos
export PGPROVISION_AUTORELOAD_SCHEMAS=true
pgprovision create schemas && pgprovision apply schemas
curl http://postgrest:3000/todos   # -> []
```

For authenticated (write) requests, see the JWT tool: [docs/pgjwt.md](pgjwt.md).

## Yaml templates

The template dir defaults to `./data/provision/schemas` (see the env vars
below). The examples in `data/provision/examples/todos/` are a complete
scenario:

### `tables.yaml`

```yaml
tables:
  - name: todos
    columns:
      - name: id
        type: integer
        primary_key: true
      - name: title
        type: text
        not_null: true
      - name: done
        type: boolean
        default: false
  - name: grants
    columns:
      - name: grant_id
        type: integer
        primary_key: true
      - name: user_id
        type: integer
        not_null: true
        references: users.user_id
```

`type` is any SQL type string. `default` accepts strings, numbers, booleans
and `null`. `references: <table>.<column>` generates a `FOREIGN KEY`
constraint — required for PostgREST to expose embedded resources (see
`data/provision/examples/roles_api` for a multi-table example).

### `users.yaml`

```yaml
users:
  - name: testuser
    password: testuser_password
    login: true
```

### `roles.yaml`

```yaml
roles:
  - name: test_anon
    login: false
  - name: editor
    login: false
    grant_to: [authenticator]
```

`grant_to` makes the authenticator a member of the role (generates
`GRANT <role> TO authenticator;`), which is required for PostgREST to switch
into that role via a JWT. `users.yaml` entries support the same fields plus
`password`.

### `grants.yaml`

```yaml
grants:
  - user: anon
    tables: [todos]
    permissions: [select]
  - user: editor
    tables: [todos]
    permissions: [select, insert, update, delete]
  - user: test_anon
    tables: [todos]
    permissions: [select]
```

Binds one user (or role) to one or more tables with the same permissions
(`select`, `insert`, `update`, `delete`). `anon` is what anonymous PostgREST
requests run as; `editor` is the role a JWT can switch into (see its
`grant_to` above).

### `functions.yaml`

See the pre-request example above: entries have `name` (schema-qualified
gets its schema auto-created), `returns` (default `void`), `language`
(default `plpgsql`), optional `args` (raw SQL argument list) and `body`
(SQL function body). Generated with `CREATE OR REPLACE FUNCTION ... AS $$ ... $$`.

### `postgrest.yaml`

See the in-database configuration section above: `role` (default
`authenticator`) and `settings` (map of `pgrst.*` names to values).

The example users/roles deliberately avoid the baseline roles
`anon`/`authenticator` created by `db/init/01-roles.sh`, since re-`CREATE`ing
them would fail on apply. Grants may reference `anon` freely — `GRANT` is
additive.

## End-to-end example

```sh
export PGPROVISION_SCHEMAS_ROOT=data/provision/examples/todos
export PGPROVISION_AUTORELOAD_SCHEMAS=true
pgprovision create schemas
pgprovision apply schemas
pgprovision check schemas
pgprovision show schema todos
TOKEN=$(pgjwt --role editor --exp 1h)
curl -H "Authorization: Bearer $TOKEN" http://postgrest:3000/todos
```

## Configuration (defaults + environment overrides)

| Setting | Default | Environment variable |
|---|---|---|
| yaml template dir | `./data/provision/schemas` | `PGPROVISION_SCHEMAS_ROOT` |
| tables template | `tables.yaml` | `PGPROVISION_TABLES_FILE` |
| users template | `users.yaml` | `PGPROVISION_USERS_FILE` |
| roles template | `roles.yaml` | `PGPROVISION_ROLES_FILE` |
| grants template | `grants.yaml` | `PGPROVISION_GRANTS_FILE` |
| functions template | `functions.yaml` | `PGPROVISION_FUNCTIONS_FILE` |
| postgrest settings template | `postgrest.yaml` | `PGPROVISION_POSTGREST_FILE` |
| access filter template | `access_filter.yaml` | `PGPROVISION_ACCESS_FILTER_FILE` |
| pgrmapper schema | `pgrmapper` | `PGMAPPER_SCHEMA` |
| generated SQL dir | `./data/provision/sql` | `PGPROVISION_SQL_DIR` |
| database URL | built from `POSTGRES_*` envs | `PGPROVISION_DB_URL` |
| database schema | `public` | `PGPROVISION_DB_SCHEMA` |
| auto-reload schema cache on apply | `false` | `PGPROVISION_AUTORELOAD_SCHEMAS` |

Everything follows this pattern: sensible default, overridable by an
environment variable.

## Related

- [docs/pgrmapper.md](pgrmapper.md) — the read-only filtering proxy in front of PostgREST
- [docs/pgjwt.md](pgjwt.md) — JWT generation for PostgREST authorization
- [docs/pgmkcurl.md](pgmkcurl.md) — build curl commands for PostgREST
- [docs/provision.md](provision.md) — stack lifecycle (`scripts/provision.sh`)
- [docs/infra/pgproxy.md](infra/pgproxy.md) — why the pgproxy sidecars exist
