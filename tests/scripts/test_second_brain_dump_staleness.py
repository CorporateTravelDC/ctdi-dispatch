"""2026-10-22 (operator): the weekly second-brain dump was silently skipped for a month; the
staleness check fails (and would notify ops-health) when its newest log is older than 9 days."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/second-brain-dump-staleness.sh"


def _run(d: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "DUMP_LOG_DIR": str(d)}
    return subprocess.run(["bash", str(SCRIPT), "--no-notify"], env=env, capture_output=True, text=True, timeout=30)


def test_fresh_stale_and_missing(tmp_path):
    assert _run(tmp_path / "absent").returncode == 1                 # no log dir at all
    assert _run(tmp_path).returncode == 1                            # dir but no logs
    old = tmp_path / "2026-09-14.log"; old.write_text("x")
    t = time.time() - 10 * 86400; os.utime(old, (t, t))
    r = _run(tmp_path)
    assert r.returncode == 1 and "10 days old" in r.stdout
    new = tmp_path / "2026-10-20.log"; new.write_text("x")
    t = time.time() - 2 * 86400; os.utime(new, (t, t))
    r = _run(tmp_path)
    assert r.returncode == 0 and "2026-10-20.log" in r.stdout
