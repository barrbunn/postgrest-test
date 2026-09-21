"""Unit tests for port allocation and readiness polling."""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

from tests.harness.ports import free_port, wait_for_http, wait_for_tcp


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture()
def http_server():
    port = free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


def test_free_port_is_bindable():
    port = free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_wait_for_tcp_detects_listener(http_server):
    wait_for_tcp("127.0.0.1", http_server, timeout=2)


def test_wait_for_tcp_times_out_when_nothing_listens():
    port = free_port()
    with pytest.raises(TimeoutError):
        wait_for_tcp("127.0.0.1", port, timeout=0.3, interval=0.05)


def test_wait_for_http_returns_matching_response(http_server):
    response = wait_for_http(f"http://127.0.0.1:{http_server}/health",
                             timeout=2, expect=200)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_wait_for_http_times_out_on_closed_port():
    port = free_port()
    with pytest.raises(TimeoutError):
        wait_for_http(f"http://127.0.0.1:{port}/", timeout=0.3, interval=0.05)
