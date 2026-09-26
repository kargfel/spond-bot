# tests/test_healthcheck_script.py — scripts/healthcheck.py is Docker's HEALTHCHECK probe.
# Exit code 0 means healthy, anything else unhealthy.
import http.server
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def _serve(status_code: int):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status_code if self.path == "/api/v1/health" else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(port: int) -> int:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={"HEALTHCHECK_URL": f"http://127.0.0.1:{port}/api/v1/health"},
        timeout=15,
    ).returncode


@pytest.mark.parametrize("code,expected", [(200, 0), (503, 1), (500, 1)])
def test_exit_code_follows_http_status(code, expected):
    server = _serve(code)
    try:
        assert _run(server.server_address[1]) == expected
    finally:
        server.shutdown()


def test_unreachable_app_is_unhealthy():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    assert _run(free_port) == 1
