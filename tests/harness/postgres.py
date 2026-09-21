"""Session-scoped ephemeral Postgres container for integration tests.

One container per session on a random host port; each test gets a fresh
database. The container is disposable: nothing here depends on the compose
stack or the driver.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass

import psycopg

from tests.harness.ports import free_port, wait_for_tcp

IMAGE = "postgres:16-alpine"
PASSWORD = "test"
_CONTAINER_PREFIX = "pgr-test-suite-pg"

# Mirrors db/init/01-roles.sh: the roles pgprovision fixtures grant to.
BOOTSTRAP_SQL = """
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD 'auth_password';
GRANT anon TO authenticator;
"""


class PodmanUnavailable(RuntimeError):
    pass


def podman_available(timeout: float = 10.0) -> bool:
    try:
        subprocess.run(
            ["podman", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@dataclass
class PostgresInstance:
    name: str
    port: int

    @property
    def host(self) -> str:
        return "127.0.0.1"

    def url(self, database: str = "postgres", user: str = "postgres") -> str:
        return f"postgresql://{user}:{PASSWORD}@{self.host}:{self.port}/{database}"

    def connect(self, database: str = "postgres") -> psycopg.Connection:
        return psycopg.connect(self.url(database), autocommit=True)

    def create_database(self, database: str) -> None:
        with self.connect() as conn:
            conn.execute(f'CREATE DATABASE "{database}"')

    def drop_database(self, database: str) -> None:
        with self.connect() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')

    def stop(self) -> None:
        subprocess.run(
            ["podman", "rm", "-f", self.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def _wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with self.connect() as conn:
                    conn.execute("SELECT 1")
                return
            except psycopg.Error as ex:
                last_error = ex
                time.sleep(0.25)
        raise TimeoutError(f"postgres {self.name} not ready after {timeout}s: {last_error}")

    def bootstrap_roles(self) -> None:
        with self.connect() as conn:
            conn.execute(BOOTSTRAP_SQL)


def start_postgres(name: str | None = None) -> PostgresInstance:
    """Start the session container, wait for it and create baseline roles."""
    if not podman_available():
        raise PodmanUnavailable(
            "podman is not available or the podman machine is not running"
        )
    container = name or f"{_CONTAINER_PREFIX}-{uuid.uuid4().hex[:6]}"
    port = free_port()
    subprocess.run(
        [
            "podman", "run", "-d", "--rm",
            "--name", container,
            "-p", f"127.0.0.1:{port}:5432",
            "-e", f"POSTGRES_PASSWORD={PASSWORD}",
            IMAGE,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    wait_for_tcp("127.0.0.1", port, timeout=30)
    instance = PostgresInstance(name=container, port=port)
    instance._wait_ready()
    instance.bootstrap_roles()
    return instance
