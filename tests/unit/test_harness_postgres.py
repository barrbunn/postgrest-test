"""Integration test for the ephemeral Postgres session fixture."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_container_has_baseline_roles(postgres):
    with postgres.connect() as conn:
        roles = {row[0] for row in conn.execute("SELECT rolname FROM pg_roles")}
    assert {"anon", "authenticator"} <= roles


def test_per_test_database_lifecycle(postgres):
    database = "pgr_t_smoke"
    postgres.create_database(database)
    try:
        with postgres.connect(database) as conn:
            current = conn.execute("SELECT current_database()").fetchone()[0]
        assert current == database
    finally:
        postgres.drop_database(database)

    with postgres.connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
    assert exists is None
