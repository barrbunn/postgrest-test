"""Port allocation and readiness polling for harness servers."""

from __future__ import annotations

import socket
import time

import httpx


def free_port() -> int:
    """An ephemeral port that is free at call time (bind then close)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_tcp(host: str, port: int, timeout: float = 10.0,
                 interval: float = 0.1) -> None:
    """Poll until something accepts TCP connections on host:port."""
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=interval):
                return
        except OSError as ex:
            last_error = ex
            time.sleep(interval)
    raise TimeoutError(
        f"nothing listening on {host}:{port} after {timeout}s: {last_error}"
    )


def wait_for_http(url: str, timeout: float = 10.0, interval: float = 0.1,
                  expect: int | None = None) -> httpx.Response:
    """Poll url until it answers (optionally with a specific status)."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=interval)
        except httpx.HTTPError as ex:
            last_error = ex
        else:
            if expect is None or response.status_code == expect:
                return response
            last_error = RuntimeError(f"unexpected status {response.status_code}")
        time.sleep(interval)
    raise TimeoutError(f"{url} not ready after {timeout}s: {last_error}")
