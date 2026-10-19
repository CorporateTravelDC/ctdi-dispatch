"""tests/scripts/test_demo_sqlite_retire_check.py -- 2026-10-19 relay 4
preparation: scripts/demo-sqlite-retire-check.py verifies N hours on
Postgres, lists the old demo SQLite files, records G26 hashes, and prints a
shred command ONLY when verified -- it never runs one. Fake healthz, fake
podman facts, files and /proc under tmp_path.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("retire", REPO / "scripts/demo-sqlite-retire-check.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def good_healthz(**over):
    hz = {"source": "postgres", "ok": True, "profiles_store": "postgres", "started_at": "2026-10-20T00:00:00Z",
          "uptime_s": 5 * 86400, "requests": {"sqlite": 0, "postgres": 1234}, "loop_days": 14}
    hz.update(over)
    return hz


def good_facts(started=NOW - timedelta(days=5), **over):
    f = {"running": True, "started_at": started.strftime("%Y-%m-%d %H:%M:%S.123456789 +0000 UTC"),
         "DEMO_SOURCE": "postgres", "DISPATCH_DB_BACKEND": "postgres", "DISPATCH_ROLE": "demo"}
    f.update(over)
    return f


@pytest.fixture
def world(tmp_path, monkeypatch):
    m = load()
    monkeypatch.setattr(m, "utcnow", lambda: NOW)
    a = tmp_path / "demo-source.db"; a.write_bytes(b"sovereign" * 100)
    b = tmp_path / "demo_access.db"; b.write_bytes(b"profiles")
    (tmp_path / "demo-source.db-wal").write_bytes(b"w")
    proc = tmp_path / "proc"; (proc / "123" / "fd").mkdir(parents=True)
    calls = []
    real_run = subprocess.run

    def guarded_run(argv, *a, **k):
        calls.append(list(argv))
        assert "shred" not in " ".join(map(str, argv)), "the script must never run shred"
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(m.subprocess, "run", guarded_run)
    cands = (str(a), str(b), str(tmp_path / "absent.db"))
    return m, tmp_path, cands, proc, calls


def run(m, cands, proc, tmp_path, hz, facts, *extra):
    out = tmp_path / "g26.json"
    rc = m.main(["--hours", "48", "--out", str(out), *extra], healthz_fn=lambda: hz,
                facts_fn=lambda n: facts, candidates=cands, proc=proc, wait_load=False)
    return rc, out


def test_verified_prints_the_command_and_records_hashes(world, capsys):
    m, tmp, cands, proc, calls = world
    rc, out = run(m, cands, proc, tmp, good_healthz(), good_facts())
    text = capsys.readouterr().out
    assert rc == 0 and "(a) served from postgres for >= 48 h: YES" in text
    assert f"shred -n 1 -z -u -- {cands[0]} {cands[0]}-wal {cands[1]}" in text
    assert "absent.db" not in text
    rec = json.loads(out.read_text())
    assert rec["guardrail"] == "G26" and rec["verified"] is True
    by = {e["path"]: e for e in rec["files"]}
    assert by[cands[0]]["sha256"] == hashlib.sha256(b"sovereign" * 100).hexdigest()
    assert by[cands[1]]["size"] == len(b"profiles")
    assert Path(cands[0]).exists(), "nothing is deleted"


@pytest.mark.parametrize("hz,facts,why", [
    (dict(source="sqlite"), {}, "not postgres"),
    (dict(requests={"sqlite": 3, "postgres": 9}), {}, "requests answered from sqlite"),
    (dict(requests={"sqlite": 0, "postgres": 0}), {}, "no request answered from postgres"),
    (dict(uptime_s=3600), {}, "uptime 1 h < 48 h"),
    (dict(profiles_store="sqlite"), {}, "profile store"),
    (dict(ok=False), {}, "ok is not true"),
    ({}, dict(DISPATCH_DB_BACKEND="sqlite"), "DISPATCH_DB_BACKEND='sqlite'"),
    ({}, dict(started_at="2026-10-25 06:00:00.0 +0000 UTC"), "started less than 48 h ago"),
    ({}, dict(running=False), "not running"),
])
def test_not_verified_prints_no_command(world, capsys, hz, facts, why):
    m, tmp, cands, proc, calls = world
    rc, out = run(m, cands, proc, tmp, good_healthz(**hz), good_facts(**facts))
    text = capsys.readouterr().out
    assert rc == 1 and why in text and "shred -n" not in text
    assert "(c) no shred command: not verified" in text
    assert json.loads(out.read_text())["verified"] is False


def test_a_file_held_open_blocks_the_command(world, capsys):
    m, tmp, cands, proc, calls = world
    os.symlink(str(Path(cands[1]).resolve()), proc / "123" / "fd" / "7")
    rc, _ = run(m, cands, proc, tmp, good_healthz(), good_facts())
    text = capsys.readouterr().out
    assert rc == 1 and "IN USE by 1 process(es)" in text and "shred -n" not in text


def test_unreachable_healthz_is_not_verified(world, capsys):
    m, tmp, cands, proc, calls = world

    def boom():
        raise OSError("refused")
    rc = m.main(["--no-hash"], healthz_fn=boom, facts_fn=lambda n: good_facts(), candidates=cands, proc=proc,
                wait_load=False)
    assert rc == 1 and "healthz unreachable" in capsys.readouterr().out


def test_script_text_never_executes_shred():
    text = (REPO / "scripts/demo-sqlite-retire-check.py").read_text()
    for line in text.splitlines():
        if "shred" in line and ("subprocess" in line or "os.system" in line or "run(" in line):
            pytest.fail(line)
    assert '"shred -n 1 -z -u -- "' in text
