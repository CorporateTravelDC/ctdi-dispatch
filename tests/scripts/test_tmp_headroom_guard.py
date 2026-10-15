"""2026-10-14: the /tmp headroom guard reclaims only this user's stale, known-shaped entries, in order,
and never anything recent. The floor is forced with TMP_MIN_FREE_PCT=101 (always under) against a
private directory standing in for /tmp."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/tmp-headroom-guard.sh"


def run(d: Path, state: Path, *args, floor="101"):
    env = dict(os.environ, TMP_GUARD_DIR=str(d), TMP_GUARD_STATE_DIR=str(state), TMP_MIN_FREE_PCT=floor,
               TMP_GUARD_NTFY_BASE="http://127.0.0.1:9", PATH="/usr/bin:/bin")   # never the real ntfy
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def age(p: Path, minutes: float):
    t = time.time() - minutes * 60
    os.utime(p, (t, t))


def test_reclaims_only_stale_known_shapes(tmp_path):
    d, state = tmp_path / "t", tmp_path / "state"
    d.mkdir()
    old_db, new_db = d / "tmpabcd1234.db", d / "tmpzzzz9999.db"
    old_mk, new_mk = d / "tmp.AbCdEfGhIj", d / "tmp.ZyXwVuTsRq"
    keep = d / "notes.txt"
    for p in (old_db, new_db, keep):
        p.write_text("x")
    old_mk.mkdir(); new_mk.mkdir()
    age(old_db, 60); age(old_mk, 25 * 60)
    r = run(d, state)
    assert not old_db.exists() and not old_mk.exists()
    assert new_db.exists() and new_mk.exists() and keep.exists()      # recent or unknown shape: untouched
    assert r.returncode == 1 and "STILL UNDER FLOOR" in r.stdout       # floor forced unreachable
    assert "ntfy send failed" in r.stdout and not (state / "last-alert-key").exists()   # nothing really sent


def test_dry_run_changes_nothing(tmp_path):
    d, state = tmp_path / "t", tmp_path / "state"
    d.mkdir()
    old = d / "tmpabcd1234.db"; old.write_text("x"); age(old, 60)
    r = run(d, state, "--dry-run")
    assert old.exists() and "would reclaim 1 leaked test database" in r.stdout


def test_healthy_is_silent(tmp_path):
    d, state = tmp_path / "t", tmp_path / "state"
    d.mkdir()
    old = d / "tmpabcd1234.db"; old.write_text("x"); age(old, 60)
    r = run(d, state, floor="0")
    assert r.returncode == 0 and old.exists() and r.stdout == ""
