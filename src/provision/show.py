"""Show the live database schema, to help build PostgREST requests."""

import json

import typer

from provision import config, db

_EXAMPLE_VALUES = {
    "integer": 0,
    "bigint": 0,
    "smallint": 0,
    "real": 0.0,
    "double precision": 0.0,
    "numeric": 0.0,
    "text": "example",
    "character varying": "example",
    "boolean": False,
    "timestamp with time zone": "2026-01-01T00:00:00Z",
    "timestamp without time zone": "2026-01-01T00:00:00",
    "date": "2026-01-01",
    "json": {},
    "jsonb": {},
}


def _example_value(data_type: str):
    return _EXAMPLE_VALUES.get(data_type)


def render_schemas() -> None:
    with db.connect() as conn:
        live = db.db_tables(conn)
    if not live:
        typer.echo(f"no tables in schema {config.db_schema()}")
        return
    typer.echo(f"tables in schema {config.db_schema()}:")
    for name, cols in sorted(live.items()):
        typer.echo(f"  {name} ({len(cols)} columns)")


def render_table(name: str) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            owner = cur.fetchone()[0]
        live = db.db_tables(conn)
        live_grants = db.db_grants(conn)
        live_fks = db.db_foreign_keys(conn)
    if name not in live:
        typer.echo(f"error: table {name} not found in schema {config.db_schema()}", err=True)
        raise typer.Exit(code=1)

    cols = live[name]
    typer.echo(f"table {name} (schema {config.db_schema()})")
    typer.echo()
    width = max(len(c["name"]) for c in cols) + 2
    for c in cols:
        flags = []
        if c["pk"]:
            flags.append("PRIMARY KEY")
        if c["nullable"] == "NO":
            flags.append("NOT NULL")
        if c["default"] is not None:
            flags.append(f"DEFAULT {c['default']}")
        ref = live_fks.get(name, {}).get(c["name"])
        if ref:
            flags.append(f"REFERENCES {ref[0]}({ref[1]})")
        suffix = f"  {', '.join(flags)}" if flags else ""
        typer.echo(f"  {c['name'].ljust(width)}{c['type']}{suffix}")

    required = [c["name"] for c in cols if (c["pk"] or c["nullable"] == "NO") and c["default"] is None]
    typer.echo()
    typer.echo("required for POST: " + (", ".join(required) if required else "none"))

    body = {c["name"]: _example_value(c["type"]) for c in cols}
    typer.echo()
    typer.echo(f"POST /{name} with body:")
    typer.echo(json.dumps(body, indent=2))

    table_grants: dict[str, list[str]] = {}
    for grantee, table, priv in sorted(live_grants):
        if table == name and grantee != owner:
            table_grants.setdefault(grantee, []).append(priv)
    typer.echo()
    if table_grants:
        typer.echo("grants: " + ", ".join(f"{g}={','.join(p)}" for g, p in sorted(table_grants.items())))
    else:
        typer.echo("grants: none")
