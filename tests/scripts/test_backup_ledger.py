"""
2026-10-09: backup private ledger -- hash chain, tamper detection and the CUI "#" stamp.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_LEDGER", str(tmp_path / "ledger.jsonl"))
    spec = importlib.util.spec_from_file_location("bl", REPO / "scripts/backup/backup_ledger.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_chain_links_and_verifies(monkeypatch, tmp_path):
    m = load(monkeypatch, tmp_path)
    a = m.append("backup", {"archive": {"name": "ctdi-1"}})
    b = m.append("sync", {"target": "proton:ctdi", "ok": True})
    assert b["prev_hash"] == a["entry_hash"] and b["seq"] == 2
    assert m.verify() == (True, b["entry_hash"])
    assert oct(m.LEDGER.stat().st_mode & 0o777) == "0o600"


def test_any_edit_or_deleted_line_breaks_the_chain(monkeypatch, tmp_path):
    m = load(monkeypatch, tmp_path)
    for i in range(3):
        m.append("backup", {"n": i})
    lines = m.LEDGER.read_text().splitlines()
    edited = json.loads(lines[1]); edited["data"]["n"] = 99
    m.LEDGER.write_text("\n".join([lines[0], json.dumps(edited), lines[2]]) + "\n")
    assert m.verify()[0] is False
    m.LEDGER.write_text("\n".join([lines[0], lines[2]]) + "\n")       # a removed middle entry
    assert m.verify()[0] is False


def test_cui_stamp_records_hash_and_counts_only(monkeypatch, tmp_path, capsys):
    m = load(monkeypatch, tmp_path)
    dump = tmp_path / "ladd.pgdump"
    dump.write_bytes(b"synthetic")
    m.main(["file", str(dump)])
    meta = json.loads(capsys.readouterr().out)
    assert meta[0]["sha256"] == m.sha256_file(dump) and meta[0]["bytes"] == 9
    e = m.append("cui-stamp", {"stamp": "#", "rows": {"faa_ladd_aircraft": 3}, "off_box": False, "local_dump": meta})
    assert e["data"]["stamp"] == "#" and e["data"]["off_box"] is False
    assert m.main(["verify"]) == 0


def test_unknown_kind_is_refused(monkeypatch, tmp_path):
    m = load(monkeypatch, tmp_path)
    try:
        m.append("anything", {})
    except ValueError:
        return
    raise AssertionError("unknown kind accepted")
