# tests/test_proxy_warning.py — a public site that trusts X-Forwarded-For from everyone says so at startup.
import logging

import pytest

from app.main import warn_if_proxies_untrusted


@pytest.mark.parametrize("proxies,domain,warns", [
    ("*", "spond.example.com", True),          # trusts everyone: spoofable
    (" * ", "spond.example.com", True),
    ("", "spond.example.com", True),           # unset: every visitor looks like the proxy
    ("   ", "spond.example.com", True),
    ("172.18.0.5", "spond.example.com", False),
    ("172.18.0.0/16,10.0.0.5", "spond.example.com", False),
    ("*", "localhost", False),                 # local development: nothing to protect
    ("", "localhost", False),
])
def test_warns_when_a_public_site_trusts_everyone_or_nobody(proxies, domain, warns, caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        assert warn_if_proxies_untrusted(proxies, domain) is warns
    assert ("TRUSTED_PROXIES" in caplog.text) is warns


def test_the_wildcard_warning_says_visitors_can_fake_their_ip(caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        warn_if_proxies_untrusted("*", "spond.example.com")
    assert "fake their IP" in caplog.text and "TRUSTED_PROXIES in .env" in caplog.text


def test_the_unset_warning_says_every_visitor_looks_like_the_proxy_and_how_to_fix_it(caplog):
    with caplog.at_level(logging.WARNING, logger="app.main"):
        warn_if_proxies_untrusted("", "spond.example.com")
    assert "only 127.0.0.1 is trusted" in caplog.text
    assert "every visitor looks like the proxy" in caplog.text and "shared login rate limit" in caplog.text
    assert "TRUSTED_PROXIES=" in caplog.text and "docs/setup.md" in caplog.text


def test_the_setting_is_unset_by_default():
    from app.config import Settings

    assert Settings.model_fields["trusted_proxies"].default == ""


def test_compose_drops_privileges_the_app_does_not_need():
    from pathlib import Path

    import yaml

    app = yaml.safe_load((Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text())["services"]["app"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["cap_drop"] == ["ALL"]
