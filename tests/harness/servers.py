"""Run and manage mock servers.

Entry point for the subprocesses::

    python -m tests.harness.servers <kind> <config.json>

and `start_mock()` for tests, which allocates a port, writes the config,
starts the process and waits until it answers.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from tests.harness import ports
from tests.harness.mocks import gateway as gateway_mock
from tests.harness.mocks import idp as idp_mock
from tests.harness.mocks import postgrest as postgrest_mock
from tests.harness.proc import Process

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_app(kind: str, config: dict):
    if kind == "idp":
        return idp_mock.create_app(**config)
    if kind == "gateway":
        return gateway_mock.create_app(**config)
    if kind == "postgrest":
        return postgrest_mock.create_app(**config)
    raise SystemExit(f"unknown mock server kind: {kind!r}")


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: python -m tests.harness.servers <kind> <config.json>")
    kind = argv[1]
    config = json.loads(Path(argv[2]).read_text())
    host = config.pop("host", "127.0.0.1")
    port = int(config.pop("port"))
    uvicorn.run(load_app(kind, config), host=host, port=port, log_level="warning")


@dataclass
class MockServer:
    kind: str
    port: int
    url: str
    process: Process

    def stop(self) -> None:
        self.process.stop()


def start_mock(kind: str, config: dict, *, log_dir: Path,
               ready_path: str = "/") -> MockServer:
    port = ports.free_port()
    log_dir.mkdir(parents=True, exist_ok=True)
    config_path = log_dir / f"{kind}-{port}.json"
    config_path.write_text(json.dumps({**config, "host": "127.0.0.1", "port": port}))
    log_path = log_dir / f"{kind}-{port}.log"
    process = Process(
        [sys.executable, "-m", "tests.harness.servers", kind, str(config_path)],
        log_path=log_path,
        cwd=REPO_ROOT,
    ).start()
    url = f"http://127.0.0.1:{port}"
    try:
        ports.wait_for_http(url + ready_path, timeout=10)
    except Exception:
        process.stop()
        raise
    process.assert_running()
    return MockServer(kind=kind, port=port, url=url, process=process)


if __name__ == "__main__":
    main(sys.argv)
