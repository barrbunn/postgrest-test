"""Tests for the pgprovision scenario harness (renderer + DB lifecycle)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.harness.scenario import FIXTURES_ROOT, provision_scenario, render_scenario

SCENARIO = "users_grants"


def test_render_suffixes_roles_and_references(tmp_path: Path):
    dest = tmp_path / "scenario"
    roles = render_scenario(FIXTURES_ROOT / SCENARIO, dest, "abc123")

    assert roles == {
        "editor": "editor_abc123",
        "manager": "manager_abc123",
        "viewer": "viewer_abc123",
    }
    assert all((dest / name).exists() for name in
               ("tables.yaml", "roles.yaml", "grants.yaml", "access_filter.yaml"))

    roles_doc = yaml.safe_load((dest / "roles.yaml").read_text())
    assert {entry["name"] for entry in roles_doc["roles"]} == set(roles.values())
    assert all(entry["grant_to"] == ["authenticator"] for entry in roles_doc["roles"])

    grants_doc = yaml.safe_load((dest / "grants.yaml").read_text())
    assert {entry["user"] for entry in grants_doc["grants"]} == set(roles.values())

    filter_doc = yaml.safe_load((dest / "access_filter.yaml").read_text())
    assert {entry["role"] for entry in filter_doc["access_filter"]} == set(roles.values())

    tables_doc = yaml.safe_load((dest / "tables.yaml").read_text())
    assert [table["name"] for table in tables_doc["tables"]] == ["users", "grants"]


def test_render_fills_missing_domain_files(tmp_path: Path):
    source = tmp_path / "partial"
    source.mkdir()
    (source / "tables.yaml").write_text("tables: []\n")
    dest = tmp_path / "rendered"

    roles = render_scenario(source, dest, "deadbe")

    assert roles == {}
    for name in ("users.yaml", "roles.yaml", "grants.yaml",
                 "functions.yaml", "postgrest.yaml", "access_filter.yaml"):
        assert (dest / name).exists(), name


pytestmark = pytest.mark.integration


def test_provision_scenario_roundtrip(postgres):
    scenario = provision_scenario(postgres, SCENARIO)
    try:
        with postgres.connect(scenario.database) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public'"
            )}
            assert "users" in tables

            rules = conn.execute(
                'SELECT role, "table", visible_columns FROM pgrmapper.access_filter'
            ).fetchall()
            assert {row[0] for row in rules} == set(scenario.roles.values())

            for logical, db_role in scenario.roles.items():
                granted = conn.execute(
                    "SELECT has_table_privilege(%s, 'public.users', 'SELECT')",
                    (db_role,),
                ).fetchone()[0]
                assert granted, f"{logical} ({db_role}) has no SELECT on users"
    finally:
        scenario.teardown()

    with postgres.connect() as conn:
        database_exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (scenario.database,)
        ).fetchone()
        leftover_roles = conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            (list(scenario.roles.values()),),
        ).fetchall()
    assert database_exists is None
    assert leftover_roles == []
