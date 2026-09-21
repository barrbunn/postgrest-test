"""Helper subprocesses (mock servers, pgrmapper) with captured output."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


class Process:
    """A subprocess whose stdout/stderr are captured to a log file.

    Runs in its own process group so stop() can terminate the whole tree
    (uvicorn spawns no children today, but this keeps cleanup reliable).
    """

    def __init__(self, argv: list[str], log_path: Path,
                 env: dict[str, str] | None = None,
                 cwd: Path | None = None) -> None:
        self.argv = argv
        self.log_path = log_path
        self.env = env or {}
        self.cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._log = None

    def start(self) -> "Process":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("w")
        full_env = {**os.environ, **self.env}
        self._proc = subprocess.Popen(
            self.argv,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            env=full_env,
            start_new_session=True,
        )
        return self

    def stop(self, timeout: float = 5.0) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                try:
                    os.killpg(self._proc.pid, signal.SIGTERM)
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                    self._proc.wait(timeout=timeout)
            self._proc = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def assert_running(self) -> None:
        if not self.is_running():
            code = None if self._proc is None else self._proc.returncode
            raise RuntimeError(
                f"process exited (code {code}): {' '.join(self.argv)}\n"
                f"--- log tail ---\n{self.log_tail()}"
            )

    def log_tail(self, lines: int = 40) -> str:
        if not self.log_path.exists():
            return "(no log)"
        text = self.log_path.read_text(errors="replace").splitlines()
        return "\n".join(text[-lines:])

    def __enter__(self) -> "Process":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
