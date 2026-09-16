"""Generate SQL statements from the yaml documents."""

from provision import config
from provision.models import (
    AccessFilterDoc,
    Column,
    Function,
    FunctionsDoc,
    Grant,
    GrantsDoc,
    PostgrestConfigDoc,
    RolesDoc,
    Table,
    TablesDoc,
    UsersDoc,
)


def _escape_ident(value: str) -> str:
    return value.replace('"', '""')


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def tables(doc: TablesDoc) -> str:
    out = []
    for table in doc.tables:
        col_lines = []
        pk_columns = []
        for col in table.columns:
            line = f"  {_escape_ident(col.name)} {col.type}"
            if col.not_null or col.primary_key:
                line += " NOT NULL"
            if col.default is not None:
                line += f" DEFAULT {_literal(col.default)}"
            col_lines.append(line)
            if col.primary_key:
                pk_columns.append(_escape_ident(col.name))
        if pk_columns:
            col_lines.append(f"  PRIMARY KEY ({', '.join(pk_columns)})")
        out.append(f"CREATE TABLE {_escape_ident(table.name)} (\n" + ",\n".join(col_lines) + "\n);")
    return "\n\n".join(out) + ("\n" if out else "")


def users(doc: UsersDoc) -> str:
    out = []
    for user in doc.users:
        stmt = f"CREATE ROLE {_escape_ident(user.name)}"
        stmt += " LOGIN" if user.login else " NOLOGIN"
        if user.password is not None:
            stmt += f" PASSWORD '{user.password.replace(chr(39), chr(39) * 2)}'"
        stmt += ";"
        out.append(stmt)
        for target in user.grant_to:
            out.append(f"GRANT {_escape_ident(user.name)} TO {_escape_ident(target)};")
    return "\n".join(out) + ("\n" if out else "")


def roles(doc: RolesDoc) -> str:
    out = []
    for role in doc.roles:
        stmt = f"CREATE ROLE {_escape_ident(role.name)}"
        stmt += " LOGIN" if role.login else " NOLOGIN"
        stmt += ";"
        out.append(stmt)
        for target in role.grant_to:
            out.append(f"GRANT {_escape_ident(role.name)} TO {_escape_ident(target)};")
    return "\n".join(out) + ("\n" if out else "")


def grants(doc: GrantsDoc) -> str:
    out = []
    for grant in doc.grants:
        perms = ", ".join(p.upper() for p in grant.permissions)
        for table in grant.tables:
            out.append(f"GRANT {perms} ON TABLE {_escape_ident(table)} TO {_escape_ident(grant.user)};")
    return "\n".join(out) + ("\n" if out else "")


def drop_tables(doc: TablesDoc) -> str:
    out = [f"DROP TABLE IF EXISTS {_escape_ident(t.name)} CASCADE;" for t in doc.tables]
    return "\n".join(out) + ("\n" if out else "")


def drop_users(doc: UsersDoc) -> str:
    out = [f"DROP ROLE IF EXISTS {_escape_ident(u.name)};" for u in doc.users]
    return "\n".join(out) + ("\n" if out else "")


def drop_roles(doc: RolesDoc) -> str:
    out = [f"DROP ROLE IF EXISTS {_escape_ident(r.name)};" for r in doc.roles]
    return "\n".join(out) + ("\n" if out else "")


def _split_name(name: str) -> tuple[str | None, str]:
    parts = name.rsplit(".", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (None, parts[0])


def functions(doc: FunctionsDoc) -> str:
    out = []
    for schema in sorted({_split_name(f.name)[0] for f in doc.functions if _split_name(f.name)[0]}):
        out.append(f"CREATE SCHEMA IF NOT EXISTS {_escape_ident(schema)};")
        out.append(f"GRANT USAGE ON SCHEMA {_escape_ident(schema)} TO PUBLIC;")
    for f in doc.functions:
        schema, func = _split_name(f.name)
        qualified = f"{_escape_ident(schema)}.{_escape_ident(func)}" if schema else _escape_ident(func)
        out.append(
            f"CREATE OR REPLACE FUNCTION {qualified}({f.args}) RETURNS {f.returns}"
            f" LANGUAGE {f.language} AS $$ {f.body} $$;"
        )
    return "\n".join(out) + ("\n" if out else "")


def drop_functions(doc: FunctionsDoc) -> str:
    out = []
    for f in doc.functions:
        schema, func = _split_name(f.name)
        qualified = f"{_escape_ident(schema)}.{_escape_ident(func)}" if schema else _escape_ident(func)
        out.append(f"DROP FUNCTION IF EXISTS {qualified}({f.args});")
    return "\n".join(out) + ("\n" if out else "")


def postgrest_config(doc: PostgrestConfigDoc) -> str:
    out = []
    role = _escape_ident(doc.postgrest.role)
    for key, value in doc.postgrest.settings.items():
        out.append(f"ALTER ROLE {role} SET pgrst.{key} = '{value.replace(chr(39), chr(39) * 2)}';")
    return "\n".join(out) + ("\n" if out else "")


def reset_postgrest_config(doc: PostgrestConfigDoc) -> str:
    out = []
    role = _escape_ident(doc.postgrest.role)
    for key in doc.postgrest.settings:
        out.append(f"ALTER ROLE {role} RESET pgrst.{key};")
    return "\n".join(out) + ("\n" if out else "")


def access_filter(doc: AccessFilterDoc) -> str:
    schema = _escape_ident(config.mapper_schema())
    out = [
        f"CREATE SCHEMA IF NOT EXISTS {schema};",
        f"CREATE TABLE IF NOT EXISTS {schema}.access_filter (",
        "  role text NOT NULL,",
        '  "table" text NOT NULL,',
        "  visible_columns text[] NOT NULL,",
        '  PRIMARY KEY (role, "table")',
        ");",
        f"DELETE FROM {schema}.access_filter;",
    ]
    for row in doc.access_filter:
        cols = ", ".join("'" + c.replace(chr(39), chr(39) * 2) + "'" for c in row.visible_columns)
        out.append(
            f"INSERT INTO {schema}.access_filter (role, \"table\", visible_columns)"
            f" VALUES ('{row.role.replace(chr(39), chr(39) * 2)}',"
            f" '{row.table.replace(chr(39), chr(39) * 2)}', ARRAY[{cols}]);"
        )
    return "\n".join(out) + "\n"


def drop_access_filter() -> str:
    schema = _escape_ident(config.mapper_schema())
    return f"DROP TABLE IF EXISTS {schema}.access_filter;\n"
