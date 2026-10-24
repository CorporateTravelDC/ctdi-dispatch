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
CREATE TABLE flight_events (flight_id TEXT PRIMARY KEY, airline TEXT, flight_num TEXT, registration TEXT,
    departure_time REAL, arrival_time REAL);
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
    monkeypatch.setattr(m, "MAX_LOAD", 1e9)    # tests never wait on this host's load
    monkeypatch.setattr(m, "Q_KEY_FILE", tmp_path / "secrets" / "demo-q-registry.key")   # never the real home
    return m, (lambda: _sqlite(prod)), (lambda: _sqlite(demo)), prod, demo


def seed(prod: Path, rows: list[tuple[str, str, str]], briefs: list[tuple[str, str, str]] = ()):
    with _sqlite(prod) as c:
        for ep, at, text in rows:
            c.execute("INSERT INTO demo_snapshots (endpoint, captured_at, payload, payload_hash, compressed) VALUES (?, ?, ?, ?, 1)",
                      (ep, at, blob(text), "h-" + str(abs(hash(text)))))
        for at, kind, text in briefs:
            c.execute("INSERT INTO brief_archive (generated_at, brief_type, content, source) VALUES (?, ?, ?, 'skill')", (at, kind, text))


def seed_legs(prod: Path, idents: list[str], around: str | float, extra: tuple = ()):
    """SWIM legs (flight_events) for LADD idents: one leg an hour before
    `around` on that operational day and the days either side, plus `extra`
    departure epochs. The demo LADD timing orders legs from these; without
    them a LADD aircraft-day is dropped (fail closed)."""
    base = around if isinstance(around, (int, float)) else \
        datetime.fromisoformat(around).replace(tzinfo=timezone.utc).timestamp()
    deps = [base - 3600 + j * 86400 for j in (-1, 0, 1)] + list(extra)
    with _sqlite(prod) as c:
        for ident in idents:
            for i, dep in enumerate(deps):
                c.execute("INSERT INTO flight_events (flight_id, registration, departure_time) VALUES (?, ?, ?)",
                          (f"{ident}-{i}", ident, dep))


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
    seed_legs(prod, ["QSYN1", "QCALL7", "QX-ABC"], ago(days=3))
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    got = target_texts(demo)
    # 2026-10-17 (operator): listed aircraft are substituted, every record is kept
    assert {ep for ep, _ in got} == {"tfr", "notams", "alerts", "weather", "amtrak", "cps"}
    joined = " ".join(got.values())
    for ident in LADD + [LISTED_HEX]:
        assert ident not in joined
    q = m.QRegistry.load(tmp_q := m.Q_KEY_FILE)
    assert tmp_q.stat().st_mode & 0o777 == 0o600
    assert q.mark("QSYN1") in joined and q.mark("QCALL7") in joined
    # 2026-10-20: a record known only by its (redacted) hex has no identifier to
    # order its legs from SWIM -- the LADD timing drops it (fail closed)
    assert json.loads(got[("weather", next(a for e, a in got if e == "weather"))]) == {"aircraft": []}
    assert re.fullmatch(r"Q[0-9A-HJ-NPR-Z]{5}", q.mark("QSYN1"))
    out = capsys.readouterr().out
    assert "ladd registration substituted" in out and "ladd callsign substituted" in out and "ladd hex redacted" in out
    assert "ladd aircraft-day dropped (no SWIM leg order)" in out
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


def test_waits_for_load_headroom(world, monkeypatch):
    m = world[0]
    loads = iter([41.0, 30.0, 23.9])
    slept = []
    monkeypatch.setattr(m, "MAX_LOAD", 24.0)
    waited = m._wait_for_headroom(sleep=slept.append, loadavg=lambda: (next(loads),))
    assert len(slept) == 2 and waited == 2 * m.LOAD_POLL_S


# -- 2026-10-19 relay 3: the nightly timer reads production, never writes it ----

def test_production_source_is_wrapped_read_only(world):
    """Every borrowed source connection opens with SET TRANSACTION READ ONLY
    (the server then refuses any write), and main() wraps the production
    accessor -- tests' injected sqlite twins are the only unwrapped factory."""
    m = world[0]
    seen: list[str] = []

    class Rec:
        def execute(self, sql, params=()):
            seen.append(sql)
            return self

    @contextmanager
    def fake():
        yield Rec()

    with m.readonly_source(fake)() as s:
        s.execute("SELECT 1")
    assert seen == ["SET TRANSACTION READ ONLY", "SELECT 1"]
    text = (REPO / "scripts/scrub-demo-source.py").read_text()
    assert "src = source_factory or readonly_source(db_backend.pg_conn)" in text


def test_source_side_sql_is_select_only():
    """Static: inside every `with source_factory() as s:` / `with
    src() as s:` block the script executes SELECTs only; nothing on the
    source path names INSERT/UPDATE/DELETE/TRUNCATE/ALTER/CREATE/DROP."""
    text = (REPO / "scripts/scrub-demo-source.py").read_text()
    calls = re.findall(r"\bs\.execute\(\s*((?:\"[^\"]*\"\s*)+)", text)
    assert calls, "no source-side execute calls found"
    for lit in calls:
        sql = "".join(re.findall(r"\"([^\"]*)\"", lit)).strip().upper()
        assert sql.startswith("SELECT ") or sql == "SET TRANSACTION READ ONLY", sql
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|CREATE|DROP|GRANT)\b", sql), sql


def test_refresh_waits_before_the_ladd_gate(world, monkeypatch):
    m, src, tgt, prod, demo = world
    seed(prod, [("tfr", ago(days=3), json.dumps({"tfrs": [{"notam": "clean"}]}))])
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    order: list[str] = []
    monkeypatch.setattr(m, "_wait_for_headroom", lambda *a, **k: order.append("wait") or 0.0)
    real_open = m.LaddGate.open
    monkeypatch.setattr(m.LaddGate, "open", lambda self: (order.append("gate"), real_open(self))[1])
    assert run(m, src, tgt, "--refresh") == 0
    assert order[:2] == ["wait", "gate"]


def test_refresh_units_are_paced_and_notify_on_failure():
    units = REPO / ".config/systemd/user"
    svc = [l.strip() for l in (units / "corporatetraveldc-demo-source-refresh.service").read_text().splitlines()
           if l.strip() and not l.startswith("#")]
    assert "OnFailure=corporatetraveldc-unit-failure-notify@%n.service" in svc
    assert "Nice=19" in svc and "Type=oneshot" in svc
    assert any(l.startswith("ExecStartPre=") and l.endswith("scripts/relay-wait-load.sh") for l in svc)
    assert any(l.startswith("ExecStart=") and l.endswith("scripts/scrub-demo-source.py --refresh --workers 1") for l in svc)
    assert "Environment=DEMO_SCRUB_MAX_LOAD=24" in svc and "Environment=RELAY_MAX_LOAD=24" in svc
    assert any(l.startswith("TimeoutStartSec=") for l in svc)
    assert not any("--backfill" in l or "--profiles" in l for l in svc)
    timer = [l.strip() for l in (units / "corporatetraveldc-demo-source-refresh.timer").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert "OnCalendar=*-*-* 03:17:00 UTC" in timer and "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
    assert not any(l.startswith(("Requires=", "Wants=")) for l in timer)


def test_ladd_legs_bucketed_and_dark_through_the_full_scrub(world, capsys):
    """2026-10-20 (operator): LADD legs reach the demo with times on the
    15-minute grid (hidden per-leg offset) and nothing arrival-side; counts
    only in the ledger and the report, never a key or an offset."""
    m, src, tgt, prod, demo = world
    at = ago(days=3)
    eta = (datetime.now(timezone.utc) - timedelta(days=3, minutes=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seed(prod, [("route", at, json.dumps({"vip_flags": [
        {"callsign": "QCALL7", "eta": eta, "gate": "B12", "status": "landed", "destination": "KIAD"},
        {"callsign": "CLEAN1", "eta": eta, "gate": "C3"}]}))],
        briefs=[(at, "ops", f"QSYN1 ETA 14:07Z.\nQSYN1 landed 14:21Z at gate 7.\nCalm winds.\n")])
    seed_legs(prod, ["QCALL7", "QSYN1"], at)
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    got = json.loads(target_texts(demo)[("route", at)])["vip_flags"]
    q = m.QRegistry.load(m.Q_KEY_FILE)
    ladd = next(f for f in got if f["callsign"] == q.mark("QCALL7"))
    assert "gate" not in ladd and "status" not in ladd
    t = datetime.fromisoformat(ladd["eta"].replace("Z", "+00:00"))
    assert t.minute % 15 == 0 and t.second == 0
    assert next(f for f in got if f["callsign"] == "CLEAN1") == {"callsign": "CLEAN1", "eta": eta, "gate": "C3"}
    with _sqlite(demo) as c:
        brief = c.execute("SELECT content FROM brief_archive").fetchone()["content"]
        ledger = json.loads(c.execute("SELECT dropped_by_class FROM scrub_ledger").fetchone()["dropped_by_class"])
    assert "landed" not in brief and "gate 7" not in brief and "Calm winds." in brief
    assert int(re.search(r"ETA \d\d:(\d\d)Z", brief).group(1)) % 15 == 0
    assert any(k.startswith("ladd timing: ") for k in ledger)
    assert all(not re.search(r"\d", k) for k in ledger)
    out = capsys.readouterr().out
    assert "ladd timing: ladd leg timed" in out and "QCALL7" not in out and "QSYN1" not in out


def test_purge_promoted_then_rebackfill_rescrubs_old_rows(world):
    """2026-10-20: rows promoted before the LADD timing rules keep their real
    times under ON CONFLICT DO NOTHING; --purge-promoted clears them (ledger
    row, counts only) so the re-backfill re-scrubs everything."""
    m, src, tgt, prod, demo = world
    at = ago(days=3)
    seed(prod, [("route", at, json.dumps({"vip_flags": [{"callsign": "QCALL7", "eta": "2026-10-18T14:07:12Z"}]}))])
    seed_legs(prod, ["QCALL7"], at, extra=(datetime(2026, 10, 18, 13, 0, tzinfo=timezone.utc).timestamp(),))
    with _sqlite(demo) as c:   # an old-rule row already promoted at the same key
        c.execute("INSERT INTO snapshots (endpoint, captured_at, payload, payload_hash, compressed, synthetic) "
                  "VALUES ('route', ?, ?, 'old', 1, 0)", (at, blob('{"vip_flags":[{"eta":"2026-10-18T14:07:12Z"}]}')))
        c.execute("INSERT INTO meta (key, value) VALUES ('window_end', ?)", (at,))
    assert run(m, src, tgt, "--purge-promoted", "--dry-run") == 0
    assert len(target_texts(demo)) == 1                                  # dry run deletes nothing
    assert run(m, src, tgt, "--purge-promoted") == 0
    assert target_texts(demo) == {}
    with _sqlite(demo) as c:
        assert c.execute("SELECT value FROM meta WHERE key='window_end'").fetchone() is None
        row = c.execute("SELECT mode, dropped_by_class FROM scrub_ledger").fetchone()
    assert row["mode"] == "purge" and json.loads(row["dropped_by_class"]) == {"purged before re-backfill": 1}
    assert run(m, src, tgt, "--backfill", "--days", "7") == 0
    eta = json.loads(target_texts(demo)[("route", at)])["vip_flags"][0]["eta"]
    assert datetime.fromisoformat(eta.replace("Z", "+00:00")).minute % 15 == 0


def test_purge_promoted_deletes_in_short_batches(world, monkeypatch):
    """2026-10-22 (operator): the single DELETE hit the demo instance's 30 s statement_timeout on
    2026-10-20. The purge clears window_end first, then deletes by primary key in batches of
    PURGE_BATCH, each batch in its own transaction (its own factory() block), until none are left."""
    m, src, tgt, prod, demo = world
    with _sqlite(demo) as c:
        for i in range(7):
            c.execute("INSERT INTO snapshots (endpoint, captured_at, payload, payload_hash, compressed, synthetic) "
                      "VALUES ('route', ?, ?, 'h', 1, 0)", (f"2026-10-1{i}T00:00:00Z", blob("{}")))
        for i in range(3):
            c.execute("INSERT INTO brief_archive (generated_at, brief_type, content, source) VALUES (?, 'ops', 'x', 'skill')",
                      (f"2026-10-1{i}T00:00:00Z",))
        c.execute("INSERT INTO meta (key, value) VALUES ('window_end', '2026-10-17T00:00:00Z')")
    deletes = []

    class Counting:
        def __init__(self):
            self.cm = _sqlite(demo)

        def __enter__(self):
            c = self.cm.__enter__()

            class Wrap:
                def execute(self, sql, params=()):
                    if sql.startswith("DELETE FROM snapshots") or sql.startswith("DELETE FROM brief_archive"):
                        assert "LIMIT ?" in sql and params == (3,)
                        deletes.append(sql.split()[2])
                    return c.execute(sql, params)
            return Wrap()

        def __exit__(self, *a):
            return self.cm.__exit__(*a)

    monkeypatch.setattr(m.Target, "PURGE_BATCH", 3)
    t = m.Target(Counting, dry_run=True)
    assert t.purge_promoted() == (7, 3) and deletes == []              # dry run: counts only
    t = m.Target(Counting, dry_run=False)
    assert t.purge_promoted() == (7, 3)
    # 7 snapshots: 3 + 3 + 1 + an empty batch; 3 briefs: 3 + an empty batch -- one transaction each
    assert deletes == ["snapshots"] * 4 + ["brief_archive"] * 2
    with _sqlite(demo) as c:
        assert c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM brief_archive").fetchone()[0] == 0
        assert c.execute("SELECT value FROM meta WHERE key='window_end'").fetchone() is None
