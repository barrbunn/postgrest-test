"""Database access for pgprovision (apply + check)."""

from provision import config

import psycopg


def connect() -> psycopg.Connection:
    return psycopg.connect(config.db_url())


def execute_file(path) -> None:
    execute_sql(path.read_text())


def execute_sql(sql: str) -> None:
    if not sql.strip():
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def notify_reload_schema() -> None:
    """Tell PostgREST to rebuild its schema cache."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("NOTIFY pgrst, 'reload schema'")
        conn.commit()


def notify_reload_config() -> None:
    """Tell PostgREST to apply in-database configuration changes."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("NOTIFY pgrst, 'reload config'")
        conn.commit()


def db_functions(conn) -> set[str]:
    """Schema-qualified function names present in the database."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT n.nspname || '.' || p.proname FROM pg_catalog.pg_proc p"
            " JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'"
        )
        return {row[0] for row in cur.fetchall()}


def db_role_settings(conn, role: str) -> set[str]:
    """Role-level settings for `role` in the form pgrst.<key>=<value>."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT unnest(setconfig) FROM pg_catalog.pg_db_role_setting s"
            " JOIN pg_catalog.pg_roles r ON r.oid = s.setrole"
            " WHERE r.rolname = %s AND s.setdatabase = 0",
            (role,),
        )
        return {row[0] for row in cur.fetchall()}


def db_access_filter(conn) -> dict[tuple[str, str], list[str]]:
    """(role, table) -> visible_columns, from the pgrmapper access_filter table."""
    schema = config.mapper_schema().replace('"', '""')
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f'{schema}.access_filter',))
        if cur.fetchone()[0] is None:
            return {}
        cur.execute(f'SELECT role, "table", visible_columns FROM "{schema}".access_filter')
        return {(r[0], r[1]): list(r[2]) for r in cur.fetchall()}


def db_tables(conn) -> dict[str, list[dict]]:
    """name -> [column info]"""
    result: dict[str, list[dict]] = {}
    schema = config.db_schema()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = %s AND table_type = 'BASE TABLE'",
            (schema,),
        )
        names = [row[0] for row in cur.fetchall()]
        for name in names:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default"
                " FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = %s",
                (schema, name),
            )
            cols = [dict(zip(("name", "type", "nullable", "default"), row)) for row in cur.fetchall()]
            cur.execute(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc"
                " JOIN information_schema.key_column_usage kcu"
                "   ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema"
                " WHERE tc.table_schema = %s AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'",
                (schema, name),
            )
            pk = [row[0] for row in cur.fetchall()]
            for col in cols:
                col["pk"] = col["name"] in pk
            result[name] = cols
    return result


def db_roles(conn) -> dict[str, bool]:
    """name -> can login"""
    with conn.cursor() as cur:
        cur.execute("SELECT rolname, rolcanlogin FROM pg_roles")
        return {row[0]: row[1] for row in cur.fetchall()}


def db_grants(conn) -> set[tuple[str, str, str]]:
    """(grantee, table, privilege)"""
    schema = config.db_schema()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants"
            " WHERE table_schema = %s",
            (schema,),
        )
        return {(r[0], r[1], r[2].lower()) for r in cur.fetchall()}
