# tests/test_dockerfile.py — the container must not blindly trust X-Forwarded-For when told otherwise.
import json
from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()


def _cmd() -> str:
    line = next(l for l in DOCKERFILE.splitlines() if l.startswith("CMD"))
    return json.loads(line[len("CMD"):])[-1]


def test_trusted_proxies_is_configurable_and_defaults_to_the_old_behaviour():
    cmd = _cmd()
    assert '--forwarded-allow-ips "${TRUSTED_PROXIES:-*}"' in cmd
    assert "--proxy-headers" in cmd


def test_the_startup_command_still_migrates_before_serving():
    cmd = _cmd()
    assert cmd.index("alembic upgrade head") < cmd.index("uvicorn app.main:app")


def test_the_shell_expands_trusted_proxies_without_globbing_the_star():
    import os
    import subprocess

    def expand(env):
        out = subprocess.run(["sh", "-c", 'printf "%s" "--forwarded-allow-ips \\"${TRUSTED_PROXIES:-*}\\""'],
                             capture_output=True, text=True, env={**os.environ, **env})
        return out.stdout

    assert expand({"TRUSTED_PROXIES": ""}) == '--forwarded-allow-ips "*"'
    assert expand({"TRUSTED_PROXIES": "172.18.0.0/16,10.0.0.5"}) == '--forwarded-allow-ips "172.18.0.0/16,10.0.0.5"'
