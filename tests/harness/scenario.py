"""Ephemeral scenario databases provisioned through the real pgprovision CLI.

A scenario is a directory of pgprovision yaml files under
`tests/fixtures/scenarios/<name>/`. For each test:

1. the yaml is rendered into a temp dir and every role defined by the
   scenario gets a unique suffix (roles are cluster-global and would
   otherwise collide across tests); all role references are rewritten;
2. a fresh database is created in the session postgres container;
3. the rendered scenario is applied with `pgprovision apply schemas`;
4. the handle exposes the DB URL, role mapping and fixture paths;
5. teardown drops the roles, the database and the temp dir.

The scenario yaml keeps logical role names; tests and BDD steps refer to
those names and resolve them through the handle.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tests.harness.postgres import PostgresInstance

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "scenarios"

YAML_FILES = (
    "tables.yaml",
    "users.yaml",
    "roles.yaml",
    "functions.yaml",
    "grants.yaml",
    "postgrest.yaml",
    "access_filter.yaml",
)

_EMPTY_DOCS: dict[str, dict] = {
    "tables.yaml": {"tables": []},
    "users.yaml": {"users": []},
    "roles.yaml": {"roles": []},
    "functions.yaml": {"functions": []},
    "grants.yaml": {"grants": []},
    "postgrest.yaml": {"postgrest": {"settings": {}}},
    "access_filter.yaml": {"access_filter": []},
}


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _defined_roles(docs: dict[str, dict]) -> list[str]:
    names = [entry["name"] for entry in docs["roles.yaml"].get("roles") or []]
    names += [entry["name"] for entry in docs["users.yaml"].get("users") or []]
    return names


def render_scenario(source: Path, dest: Path, suffix: str) -> dict[str, str]:
    """Render scenario yaml into dest with role names suffixed.

    Returns the logical-name -> database-name mapping.
    """
    docs = {name: (_load_yaml(source / name) or _EMPTY_DOCS[name]) for name in YAML_FILES}
    mapping = {name: f"{name}_{suffix}" for name in _defined_roles(docs)}

    def rename(reference: str) -> str:
        return mapping.get(reference, reference)

    for entry in docs["roles.yaml"].get("roles") or []:
        entry["name"] = rename(entry["name"])
        entry["grant_to"] = [rename(role) for role in entry.get("grant_to") or []]
    for entry in docs["users.yaml"].get("users") or []:
        entry["name"] = rename(entry["name"])
        entry["grant_to"] = [rename(role) for role in entry.get("grant_to") or []]
    for entry in docs["grants.yaml"].get("grants") or []:
        entry["user"] = rename(entry["user"])
    for entry in docs["access_filter.yaml"].get("access_filter") or []:
        entry["role"] = rename(entry["role"])

    dest.mkdir(parents=True, exist_ok=True)
    for name in YAML_FILES:
        (dest / name).write_text(yaml.safe_dump(docs[name], sort_keys=False))
    return mapping


def _pgprovision() -> list[str]:
    executable = shutil.which("pgprovision")
    if executable:
        return [executable]
    return [sys.executable, "-m", "provision.cli"]


def _run(command: list[str], *, db_url: str, schemas_root: Path,
         sql_dir: Path, check: bool = True) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PGPROVISION_DB_URL": db_url,
        "PGPROVISION_SCHEMAS_ROOT": str(schemas_root),
        "PGPROVISION_SQL_DIR": str(sql_dir),
        "PGPROVISION_DB_SCHEMA": "public",
        "PGMAPPER_SCHEMA": "pgrmapper",
        "PGPROVISION_AUTORELOAD_SCHEMAS": "false",
    }
    result = subprocess.run(
        _pgprovision() + command, env=env, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"pgprovision {' '.join(command)} failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


@dataclass
class ScenarioHandle:
    name: str
    suffix: str
    database: str
    postgres: PostgresInstance
    schemas_root: Path
    sql_dir: Path
    tmp_dir: Path
    roles: dict[str, str] = field(default_factory=dict)
    _torn_down: bool = False

    @property
    def db_url(self) -> str:
        return self.postgres.url(self.database)

    def role(self, logical_name: str) -> str:
        """Database name of a scenario role."""
        return self.roles.get(logical_name, logical_name)

    def create(self, *domains: str) -> None:
        _run(["create", *domains], db_url=self.db_url,
             schemas_root=self.schemas_root, sql_dir=self.sql_dir)

    def apply(self, *domains: str) -> None:
        _run(["apply", *domains], db_url=self.db_url,
             schemas_root=self.schemas_root, sql_dir=self.sql_dir)

    def teardown(self) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        try:
            # roles are cluster-global: drop them once the database (and the
            # privileges inside it) is gone; connect through the admin db
            self.postgres.drop_database(self.database)
            _run(["teardown", "roles"], db_url=self.postgres.url("postgres"),
                 schemas_root=self.schemas_root, sql_dir=self.sql_dir, check=False)
        finally:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def __enter__(self) -> "ScenarioHandle":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.teardown()


def provision_scenario(postgres: PostgresInstance, name: str,
                       fixtures_root: Path | None = None) -> ScenarioHandle:
    """Create a fresh database and apply the scenario into it."""
    source = (fixtures_root or FIXTURES_ROOT) / name
    if not source.is_dir():
        raise FileNotFoundError(f"scenario fixtures not found: {source}")

    suffix = uuid.uuid4().hex[:6]
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"pgr-test-{name}-"))
    handle = ScenarioHandle(
        name=name,
        suffix=suffix,
        database=f"pgr_t_{suffix}",
        postgres=postgres,
        schemas_root=tmp_dir / "scenario",
        sql_dir=tmp_dir / "sql",
        tmp_dir=tmp_dir,
    )
    handle.roles = render_scenario(source, handle.schemas_root, suffix)
    postgres.create_database(handle.database)
    try:
        handle.create("schemas")
        handle.apply("schemas")
    except Exception:
        handle.teardown()
        raise
    return handle
