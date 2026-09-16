"""Configuration: sensible defaults overridable via environment variables.

Every path, file name and connection setting used by pgprovision follows the
same pattern: a default value, overridable with an environment variable.
"""

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def schemas_root() -> Path:
    return Path(_env("PGPROVISION_SCHEMAS_ROOT", "./data/provision/schemas"))


def sql_dir() -> Path:
    return Path(_env("PGPROVISION_SQL_DIR", "./data/provision/sql"))


def tables_file() -> Path:
    return schemas_root() / _env("PGPROVISION_TABLES_FILE", "tables.yaml")


def users_file() -> Path:
    return schemas_root() / _env("PGPROVISION_USERS_FILE", "users.yaml")


def roles_file() -> Path:
    return schemas_root() / _env("PGPROVISION_ROLES_FILE", "roles.yaml")


def grants_file() -> Path:
    return schemas_root() / _env("PGPROVISION_GRANTS_FILE", "grants.yaml")


def functions_file() -> Path:
    return schemas_root() / _env("PGPROVISION_FUNCTIONS_FILE", "functions.yaml")


def postgrest_file() -> Path:
    return schemas_root() / _env("PGPROVISION_POSTGREST_FILE", "postgrest.yaml")


def access_filter_file() -> Path:
    return schemas_root() / _env("PGPROVISION_ACCESS_FILTER_FILE", "access_filter.yaml")


def mapper_schema() -> str:
    return _env("PGMAPPER_SCHEMA", "pgrmapper")


def db_url() -> str:
    return _env(
        "PGPROVISION_DB_URL",
        "postgresql://{user}:{password}@{host}:{port}/{db}".format(
            user=_env("POSTGRES_USER", "app"),
            password=_env("POSTGRES_PASSWORD", "app_password"),
            host=_env("POSTGRES_HOST", "postgres"),
            port=_env("POSTGRES_PORT", "5432"),
            db=_env("POSTGRES_DB", "app"),
        ),
    )


def db_schema() -> str:
    return _env("PGPROVISION_DB_SCHEMA", "public")


def autoreload_schemas() -> bool:
    return _env("PGPROVISION_AUTORELOAD_SCHEMAS", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def sql_file(domain: str) -> Path:
    return sql_dir() / f"{domain}.sql"
