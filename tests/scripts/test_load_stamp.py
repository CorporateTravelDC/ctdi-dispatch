"""tests/scripts/test_load_stamp.py -- scripts/lib/load_stamp.py (2026-10-12):
a failure blamed on load carries a load value, in a private ledger."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "load_stamp_under_test", Path(__file__).resolve().parents[2] / "scripts/lib/load_stamp.py")
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)


def test_stamp_records_load_and_is_private(tmp_path):
    f = tmp_path / "s.jsonl"
    rec = ls.stamp("test-timeout", path=f, test="tests/x.py::t", duration_s=161.0)
    assert rec and rec["event"] == "test-timeout" and rec["ncpu"] >= 1
    assert {"load1", "load5", "load15", "load_per_cpu", "psi", "top"} <= set(rec)
    assert rec["ts"].endswith("Z")                                       # UTC
    assert (f.stat().st_mode & 0o777) == 0o600
    assert json.loads(f.read_text().splitlines()[0])["test"] == "tests/x.py::t"


def test_top_carries_command_names_only():
    for t in ls._top(5):
        assert set(t) == {"comm", "pcpu"} and " -" not in t["comm"]     # no argv (tokens live there)


def test_disabled_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CTDC_LOAD_STAMP", "0")
    assert ls.stamp("manual", path=tmp_path / "a.jsonl") is None
    monkeypatch.delenv("CTDC_LOAD_STAMP")
    assert ls.stamp("manual", path=Path("/proc/nope/x.jsonl")) is None   # unwritable: swallowed


def test_summary_groups_by_test_against_baseline(tmp_path, monkeypatch):
    f = tmp_path / "s.jsonl"
    monkeypatch.setenv("CTDC_LOAD_STAMP_FILE", str(f))
    rows = [{"ts": "2026-10-12T01:00:00Z", "event": "session-start", "load_per_cpu": 1.0, "top": []},
            {"ts": "2026-10-12T01:05:00Z", "event": "test-timeout", "test": "t::pg", "load_per_cpu": 4.5,
             "top": [{"comm": "podman", "pcpu": 300.0}]},
            {"ts": "2026-10-12T02:05:00Z", "event": "test-timeout", "test": "t::pg", "load_per_cpu": 3.9,
             "top": [{"comm": "podman", "pcpu": 250.0}]}]
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = ls.summary(ls.read())
    assert "baseline load/cpu at session start: median 1.00" in out
    assert "t::pg: timeout x2; load/cpu median 4.50 (max 4.50)" in out
    assert "podman x2" in out


def test_cli_stamp(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CTDC_LOAD_STAMP_FILE", str(tmp_path / "c.jsonl"))
    assert ls.main(["stamp", "--event", "relay-tests", "--note", "pre-commit"]) == 0
    assert "load stamp: relay-tests" in capsys.readouterr().out
    assert json.loads((tmp_path / "c.jsonl").read_text())["note"] == "pre-commit"
