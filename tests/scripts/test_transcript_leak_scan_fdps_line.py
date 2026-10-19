"""2026-10-18 (leak hygiene): the transcript leak scan redacts the identifier in the old
FDPS per-message INFO line ("fdps: wrote flight_event for <id> (...)"), keeps the rest
of the line, and never flags the new count summary or the DEBUG line. Synthetic ids."""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load(monkeypatch, tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text("")
    monkeypatch.setenv("LEAK_GUARD_SECRETS_ENV", str(env))
    monkeypatch.setenv("LEAK_GUARD_EXTRA_FILES", str(tmp_path / "none"))
    spec = importlib.util.spec_from_file_location("tls_fdps", REPO / "scripts/leak-guard/transcript_leak_scan.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


OLD = "2026-10-17 12:00:00 ingest.parsers.fdps INFO fdps: wrote flight_event for N0TEST7 (KDCA -> KBOS, source=FH)"
NEW = ("fdps: wrote 412 flight_event(s) in the last 300s (FH=12, TH=400)\n"
       "fdps: wrote flight_event (KDCA -> KBOS, source=FH)")


def test_old_line_found_and_redacted_new_lines_clean(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    bad, good = root / "a.jsonl", root / "b.jsonl"
    bad.write_text(json.dumps({"out": OLD}) + "\n")
    good.write_text(json.dumps({"out": NEW}) + "\n")
    t = time.time() - 3600
    for p in (bad, good):
        os.utime(p, (t, t))
    assert m.find(bad.read_bytes(), {}) == {"fdps-log-registration": 1}
    assert m.find(good.read_bytes(), {}) == {}
    assert m.main(["scan", "--root", str(root)]) == 1
    assert "N0TEST7" not in capsys.readouterr().out          # names and counts only
    assert m.main(["redact", "--root", str(root)]) == 0
    text = bad.read_text()
    assert "N0TEST7" not in text
    assert "wrote flight_event for [REDACTED:fdps-log-registration] (KDCA -> KBOS, source=FH)" in text
    assert m.main(["scan", "--root", str(root)]) == 0
