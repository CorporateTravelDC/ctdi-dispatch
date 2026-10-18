"""2026-10-18 (relay L1, leanout F3/F5): the semantic compile run ledger
(semantic_compile_runs, pg_schema/0077), the `semantic-compiled` sentinel and
the daily runner's exit status. Runs compile_layer() end to end on a throwaway
sqlite index (the same test double tests/second_brain/test_semantic.py uses);
never touches Postgres or /var/lib/corporatetraveldc."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from second_brain.semantic import compile as sc  # noqa: E402

REPO = Path(__file__).parent.parent.parent


def _zero(conn):
    return 0


@pytest.fixture
def index_db(tmp_path, monkeypatch):
    # The geometric/causal/cluster stages read SWIM/rail tables the throwaway
    # index does not have; they are not what these tests are about.
    for stage in ("assign_geometry", "assign_causal_associations", "assign_clusters"):
        monkeypatch.setattr(sc, stage, _zero)
    db = tmp_path / "index.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE vault_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL, category TEXT, size_bytes INTEGER,
            mtime TEXT, etag TEXT, indexed_at TEXT NOT NULL, tags TEXT,
            ingest_method TEXT, compile_status TEXT DEFAULT 'raw');
        CREATE VIRTUAL TABLE vault_notes_fulltext USING fts5(
            path UNINDEXED, title, content, tags, tokenize='porter');
    """)
    for path, tags, ingest, title in [
        ("corporatetraveldc/04-Syntheses/daily/aam-2026-08-01.md",
         "daily,aam,vertiport,auto", "aam-daily-watch", "AAM Daily Watch"),
        ("corporatetraveldc/01-Sources/manual/20260818T000000Z.md",
         "manual,infra", "manual", "Thermal finding"),
    ]:
        conn.execute(
            "INSERT INTO vault_documents(path, filename, category, size_bytes, mtime, etag,"
            " indexed_at, tags, ingest_method) VALUES (?,?,?,?,?,?,?,?,?)",
            (path, path.rsplit("/", 1)[-1], "test", 10, "2026-08-18T00:00:00+00:00", "e",
             "2026-08-18T00:00:00+00:00", tags, ingest))
        conn.execute("INSERT INTO vault_notes_fulltext(path, title, content, tags) VALUES (?,?,?,?)",
                     (path, title, "body", tags))
    conn.commit()
    conn.close()
    return str(db)


def _runs(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM semantic_compile_runs ORDER BY seq")]
    conn.close()
    return rows


def _fail(conn):
    raise RuntimeError("canceling statement due to lock timeout")


def test_successful_compile_records_ok_row_and_seq_n_plus_1(index_db, tmp_path):
    trig = tmp_path / "triggers"
    r1 = sc.compile_layer(db_path=index_db, sentinel_dir=str(trig))
    r2 = sc.compile_layer(db_path=index_db, sentinel_dir=str(trig))
    assert (r1["compile_seq"], r2["compile_seq"]) == (1, 2)
    rows = _runs(index_db)
    assert [r["ok"] for r in rows] == [1, 1]
    assert [r["compile_seq"] for r in rows] == [1, 2]
    assert rows[-1]["error"] is None and rows[-1]["duration_s"] >= 0
    assert rows[-1]["derivations"] == r2["derivations"]
    assert rows[-1]["concepts"] == r2["assignments"]
    # the run ledger survives the compile's own drop/truncate of _TABLES
    assert "semantic_compile_runs" not in sc._TABLES
    conn = sqlite3.connect(index_db)
    assert conn.execute("SELECT value FROM semantic_meta WHERE key='compile_seq'").fetchone()[0] == "2"
    conn.close()
    payload = json.loads((trig / sc.COMPILED_SENTINEL_NAME).read_text())
    assert payload["compile_seq"] == 2 and payload["seq"] == r2["run_seq"]
    assert payload["compiled_at"].endswith("+00:00")


def test_failed_compile_records_ok_false_and_keeps_counter(index_db, tmp_path, monkeypatch):
    trig = tmp_path / "triggers"
    sc.compile_layer(db_path=index_db, sentinel_dir=str(trig))
    before = (trig / sc.COMPILED_SENTINEL_NAME).read_text()
    monkeypatch.setattr(sc, "assign_clusters", _fail)
    with pytest.raises(RuntimeError):
        sc.compile_layer(db_path=index_db, sentinel_dir=str(trig))
    rows = _runs(index_db)
    assert [bool(r["ok"]) for r in rows] == [True, False]
    assert rows[-1]["compile_seq"] is None
    assert "lock timeout" in rows[-1]["error"]
    # the sentinel is written only on success: unchanged by the failed run
    assert (trig / sc.COMPILED_SENTINEL_NAME).read_text() == before
    monkeypatch.setattr(sc, "assign_clusters", _zero)
    assert sc.compile_layer(db_path=index_db, sentinel_dir=str(trig))["compile_seq"] == 2


def test_no_sentinel_when_the_first_compile_fails(index_db, tmp_path, monkeypatch):
    trig = tmp_path / "triggers"
    monkeypatch.setattr(sc, "assign_geometry", _fail)
    with pytest.raises(RuntimeError):
        sc.compile_layer(db_path=index_db, sentinel_dir=str(trig))
    assert not (trig / sc.COMPILED_SENTINEL_NAME).exists()


def test_test_db_path_writes_no_sentinel_by_default(index_db, monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "SECOND_BRAIN_TRIGGER_DIR", str(tmp_path / "never"))
    sc.compile_layer(db_path=index_db)
    assert not (tmp_path / "never").exists()


def test_daily_runner_exits_nonzero_on_error(monkeypatch, capsys):
    from poller.skills import semantic_compile_daily as d
    monkeypatch.setattr(d, "log_usage", lambda *a, **k: None)
    monkeypatch.setattr(sc, "compile_layer", lambda: _fail(None))
    assert d.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error" and "lock timeout" in out["reason"]
    monkeypatch.setattr(sc, "compile_layer", lambda: {"version": "x", "compile_seq": 7})
    assert d.main() == 0


def test_migration_0077_matches_the_contract():
    import re
    sql = re.sub(r"\s+", " ", (REPO / "src/common/pg_schema/0077_semantic_compile_runs.sql").read_text())
    for col in ("seq BIGSERIAL PRIMARY KEY", "started_at TIMESTAMPTZ NOT NULL",
                "finished_at TIMESTAMPTZ NOT NULL", "ok BOOLEAN NOT NULL", "error TEXT",
                "derivations INTEGER", "concepts INTEGER", "duration_s REAL",
                "compile_seq BIGINT UNIQUE"):
        assert col in sql, col


def test_compile_unit_routes_failures():
    unit = (REPO / ".config/containers/systemd/corporatetraveldc-semantic-compile-daily.container").read_text()
    assert "OnFailure=corporatetraveldc-unit-failure-notify@%n.service" in unit
