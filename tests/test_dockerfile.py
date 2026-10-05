# tests/test_dockerfile.py — the container must not blindly trust X-Forwarded-For when told otherwise.
import json
from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()


def _cmd() -> str:
    line = next(l for l in DOCKERFILE.splitlines() if l.startswith("CMD"))
    return json.loads(line[len("CMD"):])[-1]


def test_trusted_proxies_is_configurable_and_never_defaults_to_everyone():
    cmd = _cmd()
    assert '--forwarded-allow-ips "${TRUSTED_PROXIES:-127.0.0.1}"' in cmd
    assert "--proxy-headers" in cmd
    assert "forwarded-allow-ips='*'" not in DOCKERFILE and ":-*" not in DOCKERFILE, "a wildcard default lets anyone fake their IP"


def test_the_startup_command_still_migrates_before_serving():
    cmd = _cmd()
    assert cmd.index("alembic upgrade head") < cmd.index("uvicorn app.main:app")


def test_the_shell_expands_trusted_proxies_from_the_environment():
    """Runs the flag exactly as written in the Dockerfile's CMD through sh with different environments."""
    import os
    import re
    import subprocess

    flag = re.search(r'--forwarded-allow-ips "\$\{[^}]+\}"', _cmd()).group(0)

    def expand(**env):
        base = {k: v for k, v in os.environ.items() if k != "TRUSTED_PROXIES"}
        # `set --` splits the flag into words exactly as the CMD's shell does
        out = subprocess.run(["sh", "-c", f'set -- {flag}; printf "%s=%s" "$1" "$2"'],
                             capture_output=True, text=True, env={**base, **env})
        return out.stdout

    assert expand() == "--forwarded-allow-ips=127.0.0.1"                                      # not set
    assert expand(TRUSTED_PROXIES="") == "--forwarded-allow-ips=127.0.0.1"                    # `TRUSTED_PROXIES=` in .env
    assert expand(TRUSTED_PROXIES="10.0.0.5") == "--forwarded-allow-ips=10.0.0.5"
    assert expand(TRUSTED_PROXIES="172.18.0.0/16,10.0.0.5") == "--forwarded-allow-ips=172.18.0.0/16,10.0.0.5"


def test_the_proxy_check_script_uses_the_same_default_as_the_dockerfile():
    import re
    from pathlib import Path

    script = (Path(__file__).resolve().parent.parent / "scripts" / "proxy_check.py").read_text()
    assert re.search(r'DEFAULT_TRUSTED = "127\.0\.0\.1"', script)
    assert "${TRUSTED_PROXIES:-127.0.0.1}" in DOCKERFILE
