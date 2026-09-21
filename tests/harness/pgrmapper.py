"""Run pgrmapper as a real process against the harness mocks.

Per-test process (fresh DB pool, JWKS and rule caches) with env
`PGMAPPER_*` assembled from the scenario, the mock services and the session
keys.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.harness import ports
from tests.harness.proc import Process
from tests.harness.tokens import Keys

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_JWT_SECRET = "test_jwt_secret_at_least_32_characters_long"


def _command() -> list[str]:
    executable = shutil.which("pgrmapper")
    if executable:
        return [executable]
    return [sys.executable, "-c", "from pgrmapper.server import main; main()"]


@dataclass
class PgrmapperServer:
    port: int
    url: str
    process: Process

    def stop(self) -> None:
        self.process.stop()


def start_pgrmapper(*, database_url: str, upstream_url: str, keys: Keys,
                    log_dir: Path, idp_jwks_url: str, policy: str = "reject",
                    db_schema: str = "public", mapper_schema: str = "pgrmapper",
                    anon_role: str = "anon", jwt_secret: str = TEST_JWT_SECRET,
                    cache_ttl: str = "0", jwks_ttl: str = "0") -> PgrmapperServer:
    port = ports.free_port()
    key_paths = keys.write(log_dir / "keys")
    env = {
        "PGREMAPPER_HOST": "127.0.0.1",
        "PGREMAPPER_PORT": str(port),
        "PGMAPPER_DB_URL": database_url,
        "PGMAPPER_DB_SCHEMA": db_schema,
        "PGMAPPER_SCHEMA": mapper_schema,
        "PGMAPPER_UPSTREAM": upstream_url,
        "PGMAPPER_ANON_ROLE": anon_role,
        "PGMAPPER_GATEWAY_JWT_PUBLIC_KEY": str(key_paths["gateway_public"]),
        "PGMAPPER_IDP_JWKS_URL": idp_jwks_url,
        "PGMAPPER_IDP_AUDIENCE": keys.audience,
        "PGMAPPER_QUERY_POLICY": policy,
        "PGMAPPER_CACHE_TTL": cache_ttl,
        "PGMAPPER_JWKS_TTL": jwks_ttl,
        "PGRST_JWT_SECRET": jwt_secret,
    }
    log_path = log_dir / "pgrmapper" / f"pgrmapper-{port}.log"
    process = Process(_command(), log_path=log_path, env=env, cwd=REPO_ROOT).start()
    url = f"http://127.0.0.1:{port}"
    try:
        ports.wait_for_http(f"{url}/health", timeout=10, expect=200)
    except Exception:
        process.stop()
        raise
    process.assert_running()
    return PgrmapperServer(port=port, url=url, process=process)
