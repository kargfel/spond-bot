# tests/test_proxy_warning.py — a public site that trusts X-Forwarded-For from everyone says so at startup.
import logging

import pytest

from app.main import warn_if_proxies_untrusted


@pytest.mark.parametrize("proxies,domain,warns", [
    ("*", "spond.example.com", True),
    ("", "spond.example.com", True),
    (" * ", "spond.example.com", True),
    ("172.18.0.5", "spond.example.com", False),
    ("172.18.0.0/16,10.0.0.5", "spond.example.com", False),
    ("*", "localhost", False),  # local development: nothing to protect
])
def test_warns_only_when_a_public_site_trusts_everyone(proxies, domain, warns, caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        assert warn_if_proxies_untrusted(proxies, domain) is warns
    assert ("TRUSTED_PROXIES" in caplog.text) is warns


def test_the_warning_tells_what_to_do(caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        warn_if_proxies_untrusted("*", "spond.example.com")
    assert "fake their IP" in caplog.text and "TRUSTED_PROXIES=" in caplog.text and "docs/setup.md" in caplog.text


def test_compose_drops_privileges_the_app_does_not_need():
    from pathlib import Path

    import yaml

    app = yaml.safe_load((Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text())["services"]["app"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["cap_drop"] == ["ALL"]
