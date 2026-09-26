# tests/test_backup_script.py — scripts/backup.sh runs in the compose `backup` service
# (postgres:16-alpine, BusyBox sh). A stub pg_dump stands in for the real one.
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"
DAY = 86400

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def _stub_pg_dump(bin_dir: Path, *, fail: bool = False) -> Path:
    """pg_dump stub: writes its arguments to the -f target, or fails half-way."""
    log = bin_dir / "pg_dump.args"
    stub = bin_dir / "pg_dump"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{log}"\n'
        'out=""; while [ $# -gt 0 ]; do [ "$1" = "-f" ] && out="$2"; shift; done\n'
        'echo partial-data > "$out"\n'
        + ("exit 3\n" if fail else "echo dump-data >> \"$out\"\n")
    )
    stub.chmod(0o755)
    return log


def _run(tmp_path: Path, *args: str, fail: bool = False, **env: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stub_pg_dump(bin_dir, fail=fail)
    backups = tmp_path / "backups"
    backups.mkdir(exist_ok=True)
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BACKUP_DIR": str(backups),
            "BACKUP_KEEP_DAYS": "14",
            "BACKUP_INTERVAL_HOURS": "24",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def _dumps(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "backups").glob("spond_bot-*.dump"))


def _age(path: Path, days: float) -> None:
    t = time.time() - days * DAY
    os.utime(path, (t, t))


def test_once_writes_a_complete_custom_format_dump(tmp_path):
    result = _run(tmp_path, "once")
    assert result.returncode == 0, result.stderr
    dumps = _dumps(tmp_path)
    assert len(dumps) == 1
    assert dumps[0].read_text() == "partial-data\ndump-data\n"
    assert "--format=custom" in (tmp_path / "bin" / "pg_dump.args").read_text()
    assert not list((tmp_path / "backups").glob(".*partial*"))


def test_failed_dump_leaves_no_file_and_exits_non_zero(tmp_path):
    result = _run(tmp_path, "once", fail=True)
    assert result.returncode != 0
    assert _dumps(tmp_path) == []
    assert list((tmp_path / "backups").iterdir()) == []
    assert "backup failed" in result.stderr


def test_prunes_old_dumps_only_after_a_successful_backup(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    old = backups / "spond_bot-20260101T000000Z.dump"
    recent = backups / "spond_bot-20260920T000000Z.dump"
    unrelated = backups / "notes.txt"
    for f in (old, recent, unrelated):
        f.write_text("x")
    _age(old, 20)
    _age(recent, 5)
    _age(unrelated, 20)

    assert _run(tmp_path, "once", fail=True).returncode != 0
    assert old.exists(), "a failed run must not delete anything"

    assert _run(tmp_path, "once").returncode == 0
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()
    assert len(_dumps(tmp_path)) == 2


@pytest.mark.parametrize("age_days,expected", [(None, 1), (0.5, 0), (3, 1)])
def test_check_reports_whether_a_recent_backup_exists(tmp_path, age_days, expected):
    backups = tmp_path / "backups"
    backups.mkdir()
    if age_days is not None:
        f = backups / "spond_bot-20260926T000000Z.dump"
        f.write_text("x")
        _age(f, age_days)
    # "Recent" means within two backup intervals (2 x 24h here).
    assert _run(tmp_path, "check").returncode == expected


def test_rejects_unknown_command(tmp_path):
    result = _run(tmp_path, "bogus")
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
