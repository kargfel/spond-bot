# tests/test_proxy_check.py — scripts/proxy_check.py finds and tests TRUSTED_PROXIES before a deploy.
# The script is started for real (a subprocess with uvicorn) and asked over HTTP, with the same uvicorn
# settings the app runs with. Connections come from 127.0.0.1, which plays "the reverse proxy".
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "proxy_check.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Checker:
    """Runs `proxy_check.py <mode>` and queries it."""

    def __init__(self, mode: str, trusted: str | None):
        self.port = free_port()
        env = {k: v for k, v in os.environ.items() if k != "TRUSTED_PROXIES"}
        if trusted is not None:
            env["TRUSTED_PROXIES"] = trusted
        self.proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), mode, "--port", str(self.port), "--host", "127.0.0.1"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("proxy_check did not start: " + self.proc.stderr.read())

    def get(self, forwarded: str | None = None) -> str:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/", headers={"X-Forwarded-For": forwarded} if forwarded else {})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode()

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)


@pytest.fixture
def run():
    started = []

    def make(mode, trusted=None):
        c = Checker(mode, trusted)
        started.append(c)
        return c

    yield make
    for c in started:
        c.stop()


# ── finding the address ───────────────────────────────────────────────────


def test_peer_mode_shows_the_address_connections_come_from_and_the_line_to_copy(run):
    text = run("peer").get()
    assert "Connection comes from: 127.0.0.1" in text
    assert "TRUSTED_PROXIES=127.0.0.1" in text


def test_peer_mode_never_believes_a_forwarded_header(run):
    """It must show the real connection, whatever the sender claims."""
    text = run("peer", trusted="*").get(forwarded="203.0.113.9")
    assert "Connection comes from: 127.0.0.1" in text and "203.0.113.9" not in text.split("Connection comes from")[1].split("\n")[0]


# ── testing the value ─────────────────────────────────────────────────────


def test_a_trusted_proxy_has_its_header_honored(run):
    text = run("verify", trusted="127.0.0.1").get(forwarded="203.0.113.9")
    assert "SpondBot would record this visitor as: 203.0.113.9" in text
    assert "RESULT: header honored" in text


def test_an_untrusted_sender_cannot_fake_its_ip(run):
    text = run("verify", trusted="10.9.9.9").get(forwarded="203.0.113.9")
    assert "SpondBot would record this visitor as: 127.0.0.1" in text
    assert "RESULT: header ignored" in text and "203.0.113.9 " not in text.split("record this visitor as")[1].split("\n")[0]


def test_a_network_in_cidr_notation_works(run):
    text = run("verify", trusted="127.0.0.0/8").get(forwarded="203.0.113.9")
    assert "RESULT: header honored" in text


def test_the_default_trusts_only_the_host_itself(run):
    unset = run("verify", trusted=None)
    assert "TRUSTED_PROXIES: 127.0.0.1" in unset.get(forwarded="203.0.113.9")
    empty = run("verify", trusted="")  # `TRUSTED_PROXIES=` in .env
    assert "TRUSTED_PROXIES: 127.0.0.1" in empty.get(forwarded="203.0.113.9")


def test_a_chain_records_the_address_the_proxy_saw_not_what_the_client_claimed(run):
    """Traefik appends the address it saw to whatever the client sent; the client's part must not win."""
    text = run("verify", trusted="127.0.0.1").get(forwarded="1.2.3.4, 203.0.113.9")
    assert "SpondBot would record this visitor as: 203.0.113.9" in text


def test_the_wildcard_lets_the_clients_own_claim_win_which_is_why_it_is_not_the_default(run):
    text = run("verify", trusted="*").get(forwarded="1.2.3.4, 203.0.113.9")
    assert "SpondBot would record this visitor as: 1.2.3.4" in text   # spoofed
    assert "WARNING: * trusts every sender" in text


def test_without_a_header_it_says_how_to_send_one(run):
    text = run("verify", trusted="127.0.0.1").get()
    assert "X-Forwarded-For header received: (none)" in text and "curl -H 'X-Forwarded-For" in text


# ── the script itself ─────────────────────────────────────────────────────


def test_it_needs_a_mode():
    out = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert out.returncode == 2 and "peer,verify" in out.stderr


def test_it_does_not_touch_the_app_or_the_database():
    src = SCRIPT.read_text()
    for forbidden in ("from app", "import app", "DATABASE_URL", "sqlalchemy", "alembic"):
        assert forbidden not in src, forbidden
