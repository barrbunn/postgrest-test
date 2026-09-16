"""Compare the live database against the yaml definitions."""

import re
import typer

from provision import config, db, models

_TYPE_ALIASES = {
    "int": "integer",
    "int4": "integer",
    "serial": "integer",
    "varchar": "character varying",
    "string": "text",
    "bool": "boolean",
    "float8": "double precision",
}


def _normalize_type(t: str) -> str:
    return _TYPE_ALIASES.get(t.strip().lower(), t.strip().lower())


def _normalize_default(d: str | None) -> str | None:
    if d is None:
        return None
    d = d.strip().lower()
    d = re.sub(r"::[a-z0-9_ .\[\]]+$", "", d).strip()
    if len(d) >= 2 and d[0] == d[-1] == "'":
        d = d[1:-1]
    return d


def check_tables(doc: models.TablesDoc, live: dict[str, list[dict]]) -> list[str]:
    problems: list[str] = []
    for table in doc.tables:
        if table.name not in live:
            problems.append(f"table {table.name}: MISSING in database")
            continue
        db_cols = {c["name"]: c for c in live[table.name]}
        table_ok = True
        for col in table.columns:
            prefix = f"table {table.name}, column {col.name}:"
            if col.name not in db_cols:
                problems.append(f"{prefix} MISSING in database")
                table_ok = False
                continue
            actual = db_cols[col.name]
            if _normalize_type(col.type) != _normalize_type(actual["type"]):
                problems.append(f"{prefix} type mismatch: yaml {col.type} vs database {actual['type']}")
                table_ok = False
            expected_nullable = "NO" if (col.not_null or col.primary_key) else "YES"
            if actual["nullable"] != expected_nullable:
                problems.append(f"{prefix} nullability mismatch: yaml not_null={col.not_null or col.primary_key}")
                table_ok = False
            if col.default is not None and _normalize_default(actual["default"]) != _normalize_default(_pg_default(col.default)):
                problems.append(f"{prefix} default mismatch: yaml {col.default} vs database {actual['default']}")
                table_ok = False
            if actual["pk"] != col.primary_key:
                problems.append(f"{prefix} primary key mismatch: yaml primary_key={col.primary_key}")
                table_ok = False
        for db_col in db_cols.values():
            if not any(c.name == db_col["name"] for c in table.columns):
                problems.append(f"table {table.name}, column {db_col['name']}: UNMANAGED (only in database)")
                table_ok = False
        if table_ok:
            typer.echo(f"table {table.name}: OK")
    for name in live:
        if not any(t.name == name for t in doc.tables):
            typer.echo(f"table {name}: UNMANAGED (only in database, not checked)")
    return problems


def _pg_default(value) -> str:
    from provision.sqlgen import _literal
    return _literal(value)


def check_users_roles(
    users_doc: models.UsersDoc, roles_doc: models.RolesDoc, live_roles: dict[str, bool]
) -> list[str]:
    problems: list[str] = []
    for user in users_doc.users:
        if user.name not in live_roles:
            problems.append(f"user {user.name}: MISSING in database")
            continue
        if live_roles[user.name] != user.login:
            problems.append(f"user {user.name}: login mismatch: yaml {user.login} vs database {live_roles[user.name]}")
        else:
            typer.echo(f"user {user.name}: OK")
    for role in roles_doc.roles:
        if role.name not in live_roles:
            problems.append(f"role {role.name}: MISSING in database")
            continue
        if live_roles[role.name] != role.login:
            problems.append(f"role {role.name}: login mismatch: yaml {role.login} vs database {live_roles[role.name]}")
        else:
            typer.echo(f"role {role.name}: OK")
    return problems


def check_grants(doc: models.GrantsDoc, live: set[tuple[str, str, str]]) -> list[str]:
    problems: list[str] = []
    for grant in doc.grants:
        for table in grant.tables:
            for perm in grant.permissions:
                key = (grant.user, table, perm)
                if key not in live:
                    problems.append(f"grant {grant.user} {perm} on {table}: MISSING in database")
                else:
                    typer.echo(f"grant {grant.user} {perm} on {table}: OK")
    return problems


def check_functions(doc: models.FunctionsDoc, live: set[str]) -> list[str]:
    problems: list[str] = []
    for f in doc.functions:
        if f.name not in live:
            problems.append(f"function {f.name}: MISSING in database")
        else:
            typer.echo(f"function {f.name}: OK")
    return problems


def check_postgrest(doc: models.PostgrestConfigDoc, live: set[str]) -> list[str]:
    problems: list[str] = []
    for key, value in doc.postgrest.settings.items():
        expected = f"pgrst.{key}={value}"
        if expected not in live:
            problems.append(f"setting {doc.postgrest.role}.pgrst.{key}={value}: MISSING in database")
        else:
            typer.echo(f"setting {doc.postgrest.role}.pgrst.{key}={value}: OK")
    return problems


def check_access_filter(doc: models.AccessFilterDoc, live: dict[tuple[str, str], list[str]]) -> list[str]:
    problems: list[str] = []
    for row in doc.access_filter:
        key = (row.role, row.table)
        if key not in live:
            problems.append(f"access_filter {row.role}/{row.table}: MISSING in database")
        elif live[key] != row.visible_columns:
            problems.append(
                f"access_filter {row.role}/{row.table}: mismatch: yaml {row.visible_columns} vs database {live[key]}"
            )
        else:
            typer.echo(f"access_filter {row.role}/{row.table}: OK")
    return problems


def check_schemas() -> bool:
    tables_doc = models.load_tables()
    users_doc = models.load_users()
    roles_doc = models.load_roles()
    grants_doc = models.load_grants()
    functions_doc = models.load_functions()
    postgrest_doc = models.load_postgrest()
    access_filter_doc = models.load_access_filter()

    with db.connect() as conn:
        live_tables = db.db_tables(conn)
        live_roles = db.db_roles(conn)
        live_grants = db.db_grants(conn)
        live_functions = db.db_functions(conn)
        live_settings = db.db_role_settings(conn, postgrest_doc.postgrest.role)
        live_access_filter = db.db_access_filter(conn)

    problems = []
    problems += check_tables(tables_doc, live_tables)
    problems += check_users_roles(users_doc, roles_doc, live_roles)
    problems += check_grants(grants_doc, live_grants)
    problems += check_functions(functions_doc, live_functions)
    problems += check_postgrest(postgrest_doc, live_settings)
    problems += check_access_filter(access_filter_doc, live_access_filter)

    if problems:
        typer.echo("mismatches found:", err=True)
        for p in problems:
            typer.echo(f"  {p}", err=True)
        return False
    typer.echo("all checks passed")
    return True


def check_table(table_name: str) -> bool:
    tables_doc = models.load_tables()
    table = next((t for t in tables_doc.tables if t.name == table_name), None)
    if table is None:
        typer.echo(f"error: table {table_name} is not defined in {config.tables_file()}", err=True)
        return False
    with db.connect() as conn:
        live = db.db_tables(conn)
    problems = check_tables(models.TablesDoc(tables=[table]), {table_name: live[table_name]} if table_name in live else {})
    if problems:
        for p in problems:
            typer.echo(f"  {p}", err=True)
        return False
    return True
