"""pytest fixtures and collection hooks shared by the test suite."""

from __future__ import annotations

import pytest

from tests.harness import postgres as postgres_harness


def pytest_collection_modifyitems(config, items):
    if postgres_harness.podman_available():
        return
    skip = pytest.mark.skip(
        reason="podman not available; integration tests need a running podman machine"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def postgres():
    """Ephemeral Postgres container for the whole session (integration only)."""
    instance = postgres_harness.start_postgres()
    try:
        yield instance
    finally:
        instance.stop()
