"""tests/scripts/test_scrub_demo_source.py -- 2026-10-16 demo/production
database split: scripts/scrub-demo-source.py, the only bridge.

Synthetic rows in sqlite stand-ins (no live DB, no network): a "production"
file with demo_snapshots / brief_archive / demo_profiles, a "demo" file with
the sqlite twin of src/common/pg_schema_demo/0001, and the conftest-isolated
common.db sqlite file carrying faa_ladd_aircraft + faa_aircraft_registry so
the LADD gate runs through the REAL production filter
(common.db.faa_ladd_listed_batch / faa_lookup_by_hex), not a fake. Every
identifier is from the synthetic Q registry; the one tail-shaped string is
assembled at run time so no tracked text carries it.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from common import db  # noqa: E402

DEMO_TWIN = """
CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL, captured_at TEXT NOT NULL,
    payload BLOB NOT NULL, payload_hash TEXT, compressed BOOLEAN NOT NULL DEFAULT 1, synthetic BOOLEAN NOT NULL DEFAULT 0,
    UNIQUE (endpoint, captured_at, synthetic));
CREATE TABLE brief_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL, brief_type TEXT NOT NULL,
    content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'skill', UNIQUE (generated_at, brief_type));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE demo_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, password_hash TEXT NOT NULL,
    salt TEXT NOT NULL, window_days INTEGER NOT NULL, speed REAL NOT NULL DEFAULT 1.0, auto_scale BOOLEAN NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT 1);
CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE scrub_ledger (seq INTEGER PRIMARY KEY, run_at TEXT NOT NULL, mode TEXT NOT NULL, window_start TEXT, window_end TEXT,
    snapshots_promoted INTEGER NOT NULL, snapshots_dropped INTEGER NOT NULL, briefs_promoted INTEGER NOT NULL,
    briefs_dropped INTEGER NOT NULL, dropped_by_class TEXT NOT NULL, synthetic_filled INTEGER NOT NULL DEFAULT 0,
    pruned INTEGER NOT NULL DEFAULT 0, ladd_count INTEGER NOT NULL, prev_hash TEXT NOT NULL UNIQUE, entry_hash TEXT NOT NULL UNIQUE);
"""
PROD_TWIN = """
CREATE TABLE demo_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL, captured_at TEXT NOT NULL,
    payload BLOB NOT NULL, payload_hash TEXT, compressed BOOLEAN NOT NULL DEFAULT 1);
CREATE TABLE brief_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL, brief_type TEXT NOT NULL,
    content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'skill');
CREATE TABLE demo_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, password_hash TEXT NOT NULL,
    salt TEXT NOT NULL, window_days INTEGER NOT NULL, speed REAL NOT NULL DEFAULT 1.0, auto_scale BOOLEAN NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT 1);
"""
LADD = ["QSYN1", "QX-ABC", "QCALL7", "QHEX9"]
LISTED_HEX = "abc123"          # registry row QHEX9 <-> this hex
TAIL = "N" + "123AB"           # assembled: tracked text never carries a US registration shape


def load():
    spec = importlib.util.spec_from_file_location("scrub_demo_source", REPO / "scripts/scrub-demo-source.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m          # worker processes pickle functions by module name
    spec.loader.exec_module(m)
    return m


def ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S")


def blob(text: str) -> bytes:
    return zlib.compress(text.encode(), 6)


@contextmanager
def _sqlite(path: Path):
    c = sqlite3.connect(str(path)); c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """(module, source_factory, target_factory, prod_path, demo_path)."""
    monkeypatch.setenv("DEMO_PG_USER", "demo_owner")
    monkeypatch.delenv("DISPATCH_ROLE", raising=False)
    prod = tmp_path / "prod.db"; demo = tmp_path / "demo.db"
    with _sqlite(prod) as c:
        c.executescript(PROD_TWIN)
    with _sqlite(demo) as c:
        c.executescript(DEMO_TWIN)
    # the REAL filter's tables, in the conftest-isolated common.db sqlite file
    with db.conn() as c:
        c.executescript(
            "CREATE TABLE IF NOT EXISTS faa_ladd_aircraft (n_number TEXT PRIMARY KEY, updated_at REAL NOT NULL);"
            "CREATE TABLE IF NOT EXISTS faa_aircraft_registry (n_number TEXT PRIMARY KEY, mode_s_hex TEXT);"
        )
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex) VALUES (?, ?)", ("QHEX9", LISTED_HEX.upper()))
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex) VALUES (?, ?)", ("QFREE2", "0a1b2c"))
    db.faa_upsert_ladd(LADD)
    m = load()
    monkeypatch.setattr(m, "verify_self", lambda: None)
    monkeypatch.setattr(m, "Q_KEY_FILE", tmp_path / "secrets" / "demo-q-registry.key")   # never the real home
    return m, (lambda: _sqlite(prod)), (lambda: _sqlite(demo)), prod, demo


def seed(prod: Path, rows: list[tuple[str, str, str]], briefs: list[tuple[str, str, str]] = ()):
    with _sqlite(prod) as c:
        for ep, at, text in rows:
            c.execute("INSERT INTO demo_snapshots (endpoint, captured_at, payload, payload_hash, compressed) VALUES (?, ?, ?, ?, 1)",
                      (ep, at, blob(text), "h-" + str(abs(hash(text)))))
        for at, kind, text in briefs:
            c.execute("INSERT INTO brief_archive (generated_at, brief_type, content, source) VALUES (?, ?, ?, 'skill')", (at, kind, text))


def target_texts(demo: Path) -> dict[tuple[str, str], str]:
    with _sqlite(demo) as c:
        rows = c.execute("SELECT endpoint, captured_at, payload, synthetic FROM snapshots").fetchall()
    return {(r["endpoint"], r["captured_at"]): zlib.decompress(r["payload"]).decode() for r in rows if not r["synthetic"]}


def run(m, src, tgt, *args) -> int:
    return m.main(list(args), source_factory=src, target_factory=tgt, skip_self_verify=True)


def test_ladd_listed_rows_never_reach_the_target(world, capsys):
    m, src, tgt, prod, demo = world
    seed(prod, [
        ("tfr", ago(days=3), json.dumps({"tfrs": [{"notam": "clean text", "area": "sample"}]})),
        ("notams", ago(days=3), "advisory mentions QSYN1 in prose"),                       # listed registration
        ("alerts", ago(days=3), json.dumps({"items": [{"callsign": "QCALL7", "text": "x"}]})),  # listed callsign (keyed field)
        ("weather", ago(days=3), json.dumps({"aircraft": [{"hex": LISTED_HEX, "alt": 1}]})),     # listed hex via registry
        ("amtrak", ago(days=3), "foreign mark QX-ABC seen"),                                     # listed hyphenated mark
        ("cps", ago(days=3), json.dumps({"score": 3, "note": "all fine"})),
    ])
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    got = target_texts(demo)
    # 2026-10-17 (operator): listed aircraft are substituted, every record is kept
    assert {ep for ep, _ in got} == {"tfr", "notams", "alerts", "weather", "amtrak", "cps"}
    joined = " ".join(got.values())
    for ident in LADD + [LISTED_HEX]:
        assert ident not in joined
    q = m.QRegistry.load(tmp_q := m.Q_KEY_FILE)
    assert tmp_q.stat().st_mode & 0o777 == 0o600
    assert q.mark("QSYN1") in joined and q.mark("QCALL7") in joined and "[hex-redacted]" in joined
    assert re.fullmatch(r"Q[0-9A-HJ-NPR-Z]{5}", q.mark("QSYN1"))
    out = capsys.readouterr().out
    assert "ladd registration substituted" in out and "ladd callsign substituted" in out and "ladd hex redacted" in out
    assert "QSYN1" not in out and "QCALL7" not in out


def test_q_marks_stable_and_key_dependent(world):
    m = world[0]
    a, b = m.QRegistry(b"k" * 32), m.QRegistry(b"j" * 32)
    assert a.mark("qsyn1") == a.mark("QSYN1") and a.mark("QSYN1") != b.mark("QSYN1")


def test_failed_or_empty_ladd_load_is_fatal_exit_5(world, monkeypatch):
    m, src, tgt, prod, demo = world
    seed(prod, [("tfr", ago(days=3), "clean")])
    monkeypatch.setattr(m.db, "faa_ladd_count", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(SystemExit) as ei:
        run(m, src, tgt, "--backfill")
    assert ei.value.code == 5
    monkeypatch.setattr(m.db, "faa_ladd_count", lambda: 0)
    with pytest.raises(SystemExit) as ei:
        run(m, src, tgt, "--backfill")
    assert ei.value.code == 5
    assert target_texts(demo) == {}, "nothing promoted past a closed gate"


def test_lookup_error_mid_run_is_fatal(world, monkeypatch):
    m, src, tgt, prod, demo = world
    seed(prod, [("tfr", ago(days=3), "clean")])
    monkeypatch.setattr(m.db, "faa_ladd_listed_batch", lambda idents: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(SystemExit) as ei:
        run(m, src, tgt, "--backfill")
    assert ei.value.code == 5
    assert target_texts(demo) == {}


def test_excluded_endpoints_young_rows_and_brief_types_are_dropped(world):
    m, src, tgt, prod, demo = world
    seed(prod, [
        ("board", ago(days=3), "board text"), ("board_threads", ago(days=3), "threads"),
        ("osint_feed", ago(days=3), "osint"), ("knowledge_graph_meta", ago(days=3), "{}"),
        ("tfr", ago(hours=2), "too young"), ("tfr", ago(days=2), "old enough"),
    ], briefs=[(ago(days=2), "ops", "ops brief body"), (ago(days=2), "weekly", "weekly body"),
               (ago(days=2), "ep-advance", "advance body"), (ago(days=2), "daily", "daily body"),
               (ago(hours=1), "ops", "young ops")])
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    got = target_texts(demo)
    assert list(got.values()) == ["old enough"]
    with _sqlite(demo) as c:
        kinds = sorted(r["brief_type"] + ":" + r["content"] for r in c.execute("SELECT brief_type, content FROM brief_archive"))
    assert kinds == ["ep-advance:advance body", "ops:ops brief body", "weekly:weekly body"]


def test_shape_sweeps_are_the_second_line(world):
    m, src, tgt, prod, demo = world
    seed(prod, [("tfr", ago(days=3), f"tail {TAIL} hex 0a1b2c colour ff3131 word decade")])
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    text = next(iter(target_texts(demo).values()))
    assert TAIL not in text and "[tail-redacted]" in text
    assert "0a1b2c" not in text and "[hex-redacted]" in text
    assert "ff3131" in text and "decade" in text          # allowlisted colour; all-letter word untouched


def test_ledger_chain_valid_and_tamper_detected(world):
    m, src, tgt, prod, demo = world
    seed(prod, [("tfr", ago(days=3), "a"), ("cps", ago(days=2), "b")])
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    seed(prod, [("tfr", ago(days=1, hours=1), "c")])
    assert run(m, src, tgt, "--refresh") == 0
    with _sqlite(demo) as c:
        rows = c.execute("SELECT seq, prev_hash, entry_hash, mode, snapshots_promoted FROM scrub_ledger ORDER BY seq").fetchall()
    assert [r["mode"] for r in rows] == ["backfill", "refresh"]
    assert rows[0]["prev_hash"] == "0" * 64 and rows[1]["prev_hash"] == rows[0]["entry_hash"]
    assert [r["snapshots_promoted"] for r in rows] == [2, 1]
    assert run(m, src, tgt, "--verify-ledger") == 0
    with _sqlite(demo) as c:
        c.execute("UPDATE scrub_ledger SET snapshots_promoted = 9 WHERE seq = 1")
    assert run(m, src, tgt, "--verify-ledger") == 1
    # re-running the backfill is idempotent (ON CONFLICT DO NOTHING on the unique key)
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    assert len(target_texts(demo)) == 3


def test_dry_run_writes_nothing(world, capsys):
    m, src, tgt, prod, demo = world
    seed(prod, [("tfr", ago(days=3), "a")], briefs=[(ago(days=2), "ops", "body")])
    assert run(m, src, tgt, "--backfill", "--days", "7", "--dry-run") == 0
    with _sqlite(demo) as c:
        for t in ("snapshots", "brief_archive", "meta", "scrub_ledger"):
            assert c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] == 0, t
    assert "nothing written" in capsys.readouterr().out
    assert run(m, src, tgt, "--refresh") == 2, "no window_end after a dry run"


def test_profiles_copy_once(world):
    m, src, tgt, prod, demo = world
    with _sqlite(prod) as c:
        c.execute("INSERT INTO demo_profiles (label, password_hash, salt, window_days, speed, auto_scale, created_at, active) "
                  "VALUES ('P', 'hash', 'salt', 14, 1.0, 0, '2026-08-02T04:00:48+00:00', 1)")
    assert run(m, src, tgt, "--profiles", "--dry-run") == 0
    with _sqlite(demo) as c:
        assert c.execute("SELECT COUNT(*) AS n FROM demo_profiles").fetchone()["n"] == 0
    assert run(m, src, tgt, "--profiles") == 0
    assert run(m, src, tgt, "--profiles") == 0
    with _sqlite(demo) as c:
        assert c.execute("SELECT COUNT(*) AS n FROM demo_profiles").fetchone()["n"] == 1


def test_identity_refusals(world, monkeypatch):
    m, src, tgt, prod, demo = world
    monkeypatch.setenv("DISPATCH_ROLE", "demo")
    with pytest.raises(SystemExit) as ei:
        run(m, src, tgt, "--backfill")
    assert ei.value.code == 5
    monkeypatch.delenv("DISPATCH_ROLE")
    monkeypatch.setenv("DEMO_PG_USER", "demo")
    with pytest.raises(SystemExit) as ei:
        run(m, src, tgt, "--backfill")
    assert ei.value.code == 5


def test_policy_constants():
    m = load()
    assert m.ENDPOINT_ALLOWLIST == {"tfr", "weather", "alerts", "cps", "notams", "amtrak", "opsplan", "route", "brief"}
    assert not (m.ENDPOINT_ALLOWLIST & m.ENDPOINT_DENYLIST)
    assert m.BRIEF_TYPES == {"ops", "weekly", "ep-advance"}
    assert m.MIN_AGE_HOURS_DEFAULT == 24 and m.RETENTION_DAYS == 364


def test_worker_processes_give_the_same_result(world, monkeypatch):
    m, src, tgt, prod, demo = world
    monkeypatch.setattr(m, "SOURCE_PAGE", 2)
    seed(prod, [("tfr", ago(days=3, minutes=i), f"advisory {i} mentions QSYN1") for i in range(7)])
    assert run(m, src, tgt, "--backfill", "--days", "7", "--workers", "3") == 0
    got = target_texts(demo)
    assert len(got) == 7 and not any("QSYN1" in v for v in got.values())
