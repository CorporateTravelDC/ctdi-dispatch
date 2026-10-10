"""
2026-10-09: reference-data import ledger (LADD + FAA registry) -- chain, tamper
detection, fork refusal, and no identifiers in the record. Synthetic data only.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from common import db, import_ledger

REPO = Path(__file__).resolve().parents[2]


def test_stamps_chain_and_verify():
    a = import_ledger.stamp("faa-registry", [import_ledger.bytes_meta("ReleasableAircraft.zip", b"zip")], {"registry_upserted": 1})
    b = import_ledger.stamp("ladd-import", [], {"faa_ladd_aircraft_after": 0})
    assert (a["seq"], b["seq"]) == (1, 2) and b["prev_hash"] == a["entry_hash"]
    assert import_ledger.verify() == (True, b["entry_hash"])
    assert import_ledger.head() == {"seq": 2, "entry_hash": b["entry_hash"]}


def test_an_edited_body_breaks_verify():
    import_ledger.stamp("ladd-import", [], {"faa_ladd_aircraft_after": 10})
    import_ledger.stamp("ladd-import", [], {"faa_ladd_aircraft_after": 11})
    with db.conn() as c:
        c.execute("UPDATE reference_import_ledger SET body = ? WHERE seq = 1", ('{"counts":{"faa_ladd_aircraft_after":99}}',))
    ok, why = import_ledger.verify()
    assert not ok and "seq 1" in why


def test_a_second_writer_on_the_same_head_fails_instead_of_forking():
    e = import_ledger.stamp("ladd-import", [], {})
    with pytest.raises(sqlite3.IntegrityError):
        with db.conn() as c:
            c.execute("INSERT INTO reference_import_ledger (seq, at, kind, body, prev_hash, entry_hash) "
                      "VALUES (?, ?, ?, ?, ?, ?)", (2, "t", "ladd-import", "{}", "", "x"))   # prev "" reused
    assert import_ledger.verify() == (True, e["entry_hash"])


def test_unknown_kind_refused():
    with pytest.raises(ValueError):
        import_ledger.stamp("anything", [], {})


def test_ladd_import_stamps_hashes_basenames_and_counts_never_identifiers(tmp_path):
    filt = tmp_path / "industry_SYNTH.txt"
    filt.write_text("QSYN1\nQSYN2\nQSYN3\n")
    rem = tmp_path / "remove_SYNTH.txt"
    rem.write_text("QSYN3\n")
    db.init_db_v11()
    with db.conn() as c:                       # 0063 is Postgres-only; the sqlite test DB needs it here
        c.execute("CREATE TABLE IF NOT EXISTS faa_ladd_removals (n_number TEXT PRIMARY KEY, "
                  "removed_at REAL NOT NULL, source_file TEXT, note TEXT)")
    spec = importlib.util.spec_from_file_location("import_ladd_filter", REPO / "scripts/import-ladd-filter.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.argv = ["import-ladd-filter.py", str(filt), "--remove", str(rem)]
    m.main()
    e = import_ledger.entries()[-1]
    body = json.loads(e["body"])
    assert e["kind"] == "ladd-import"
    assert {s["role"] for s in body["sources"]} == {"filter", "remove"}
    assert all(s["name"] in (filt.name, rem.name) and len(s["sha256"]) == 64 for s in body["sources"])
    assert body["counts"]["faa_ladd_aircraft_after"] == 2 and body["counts"]["faa_ladd_removals"] == 1
    assert "QSYN" not in e["body"] and str(tmp_path) not in e["body"]       # no identifiers, no paths
    assert import_ledger.verify()[0]


def test_registry_fetch_stamps_the_exact_download(monkeypatch):
    import hashlib
    import io
    import zipfile
    from poller.fetchers import faa_registry as fr
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MASTER.txt", "synthetic")
    raw = buf.getvalue()
    monkeypatch.setattr(fr, "_download_zip", lambda url, timeout=300: zipfile.ZipFile(io.BytesIO(raw)))
    monkeypatch.setattr(fr, "_parse_master", lambda zf: iter([[{"n": 1}], [{"n": 2}]]))
    monkeypatch.setattr(fr, "_parse_acftref", lambda zf: iter([]))
    monkeypatch.setattr(db, "faa_upsert_aircraft", lambda batch: None)
    monkeypatch.setattr(db, "faa_registry_sweep_removed", lambda cutoff: 3)
    assert fr.fetch_faa_registry()["ok"] is True
    e = import_ledger.entries()[-1]
    body = json.loads(e["body"])
    assert e["kind"] == "faa-registry"
    assert body["sources"] == [{"name": "ReleasableAircraft.zip", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}]
    assert body["counts"] == {"registry_upserted": 2, "acftref": 0, "removed": 3}


def _run_import(*argv):
    spec = importlib.util.spec_from_file_location("import_ladd_filter2", REPO / "scripts/import-ladd-filter.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.argv = ["import-ladd-filter.py", *map(str, argv)]
    m.main()


def test_stamp_only_proves_identity_and_changes_nothing(tmp_path, capsys):
    db.init_db_v11()
    with db.conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS faa_ladd_removals (n_number TEXT PRIMARY KEY, "
                  "removed_at REAL NOT NULL, source_file TEXT, note TEXT)")
    week = tmp_path / "industry_SYNTH_1006.txt"
    week.write_text("QSYN1\nQSYN2\nQSYN3\n")
    rem = tmp_path / "remove_SYNTH_1006.txt"
    rem.write_text("QSYN3\n")
    _run_import(week, "--remove", rem)
    with db.conn() as c:
        before = sorted(r["n_number"] for r in c.execute("SELECT n_number FROM faa_ladd_aircraft").fetchall())
    capsys.readouterr()
    _run_import("--stamp-only", week, "--remove", rem)                 # the same files, re-supplied
    out = capsys.readouterr().out
    assert "identical to loaded: yes" in out and "STAMP ONLY" in out and "QSYN" not in out
    with db.conn() as c:
        assert sorted(r["n_number"] for r in c.execute("SELECT n_number FROM faa_ladd_aircraft").fetchall()) == before
    body = json.loads(import_ledger.entries()[-1]["body"])
    assert body["counts"]["identical_to_loaded"] is True and body["note"].startswith("stamp-only")
    other = tmp_path / "industry_SYNTH_other.txt"
    other.write_text("QSYN1\nQSYN9\n")
    _run_import("--stamp-only", other, "--remove", rem)                # a different week's file
    out = capsys.readouterr().out
    assert "+1 / -1" in out and "identical to loaded: no" in out
