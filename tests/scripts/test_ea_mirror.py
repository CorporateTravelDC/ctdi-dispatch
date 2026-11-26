"""tests/scripts/test_ea_mirror.py -- 2026-11-26: the EA read mirror (scripts/mirror/)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPLIT = REPO / "scripts/mirror/ea_mirror_split.py"
SCRIPT = (REPO / "scripts/mirror/ea-mirror.sh").read_text()


def run_split(tmp_path, entries, keep="3"):
    data = "".join(json.dumps(e) + "\n" for e in entries)
    return subprocess.run([sys.executable, str(SPLIT), str(tmp_path / "j"), keep], input=data, text=True,
                          capture_output=True, check=True)


def test_split_writes_one_rolling_file_per_unit(tmp_path):
    t = 1_795_500_000_000_000
    run_split(tmp_path, [
        {"_SYSTEMD_USER_UNIT": "corporatetraveldc-ingest-fdps.service", "MESSAGE": "a", "__REALTIME_TIMESTAMP": str(t)},
        {"_SYSTEMD_USER_UNIT": "corporatetraveldc-ingest-fdps.service", "MESSAGE": [104, 105], "__REALTIME_TIMESTAMP": str(t)},
        {"_SYSTEMD_USER_UNIT": "corporatetraveldc-backup.service", "MESSAGE": "b", "__REALTIME_TIMESTAMP": str(t)},
        "not json",
    ])
    f = tmp_path / "j/corporatetraveldc-ingest-fdps.service.log"
    assert f.read_text().splitlines() == ["2026-11-24T06:00:00Z a", "2026-11-24T06:00:00Z hi"]
    run_split(tmp_path, [{"_SYSTEMD_USER_UNIT": "corporatetraveldc-ingest-fdps.service", "MESSAGE": f"m{i}",
                          "__REALTIME_TIMESTAMP": str(t)} for i in range(5)])
    assert [l.split()[1] for l in f.read_text().splitlines()] == ["m2", "m3", "m4"]      # newest KEEP lines only


def test_split_names_are_safe(tmp_path):
    run_split(tmp_path, [{"_SYSTEMD_USER_UNIT": "../../evil unit", "MESSAGE": "x", "__REALTIME_TIMESTAMP": "1"}])
    assert [p.name for p in (tmp_path / "j").iterdir()] == [".._.._evil_unit.log"]
    assert not (tmp_path / "evil unit.log").exists()


def test_mirror_redacts_then_scans_then_publishes():
    r, s, p = SCRIPT.index(" redact --root"), SCRIPT.index(" scan --root"), SCRIPT.index("rsync -a --delete")
    assert r < s < p
    assert "--require-ladd --require-coords" in SCRIPT[r:s] and "--include-live" in SCRIPT[r:s]
    gate = SCRIPT[s:p]
    assert "NOT publishing" in gate and "exit 1" in gate


def test_mirror_never_lists_borg_near_the_nightly_or_without_the_backup_lock():
    assert '10#$hour >= 12 && 10#$hour < 20' in SCRIPT
    assert 'flock -n "$BSTATE/.lock" podman unshare' in SCRIPT


def test_mirror_reads_no_secret_files_and_only_selects():
    assert "dispatch-secrets.env" not in SCRIPT and "source " not in SCRIPT
    sql = [l for l in SCRIPT.splitlines() if l.strip().upper().startswith(("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"))]
    assert not sql
