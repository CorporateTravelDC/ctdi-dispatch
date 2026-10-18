"""Watchlist read cache (2026-10-18, lean-out L1; operator D2: TTL 3 s).

get_watchlist_entries() ran once per FDPS FH/TH/CL message and once per SMES
surface event (~105 reads/s). Those two paths now read
db.get_watchlist_entries_cached() through shared.watchlist.get_active_entries(
cached=True): one DB read per entry type per process per TTL, dropped by any
watchlist write in the same process.
"""
import ast
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "src"))

from common import db  # noqa: E402


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    db.init_db_all()
    db.invalidate_watchlist_cache()
    c = _Clock()
    monkeypatch.setattr(db, "_watchlist_clock", c)
    yield c
    db.invalidate_watchlist_cache()


def _entry(i="wl-flight-test-1", ident="DAL456"):
    return {"id": i, "entry_type": "flight", "tier": "transient", "identifier": ident,
            "origin": "KDCA", "destination": "KATL", "added_by": "test"}


def _insert_raw(i, ident):
    """A write from ANOTHER process: straight SQL, no db.py writer, no invalidation."""
    with db.conn() as c:
        c.execute("INSERT INTO watchlist_entries (id, entry_type, tier, identifier, added_at, added_by) "
                  "VALUES (?, 'flight', 'transient', ?, '2026-10-18T00:00:00Z', 'test')", (i, ident))


def test_second_call_within_ttl_does_not_hit_the_db(clock):
    _insert_raw("a", "DAL1")
    with mock.patch.object(db, "get_watchlist_entries", wraps=db.get_watchlist_entries) as spy:
        first = db.get_watchlist_entries_cached("flight")
        clock.t += 2.9
        second = db.get_watchlist_entries_cached("flight")
    assert spy.call_count == 1
    assert [e["id"] for e in first] == [e["id"] for e in second] == ["a"]


def test_call_after_ttl_reads_the_db_again(clock):
    _insert_raw("a", "DAL1")
    with mock.patch.object(db, "get_watchlist_entries", wraps=db.get_watchlist_entries) as spy:
        db.get_watchlist_entries_cached("flight")
        _insert_raw("b", "DAL2")                         # another process adds a watch
        clock.t += 1.0
        assert {e["id"] for e in db.get_watchlist_entries_cached("flight")} == {"a"}
        clock.t += db.WATCHLIST_CACHE_TTL_S
        assert {e["id"] for e in db.get_watchlist_entries_cached("flight")} == {"a", "b"}
    assert spy.call_count == 2


def test_a_write_in_this_process_drops_the_cache(clock):
    db.upsert_watchlist_entry(_entry())
    assert [e["identifier"] for e in db.get_watchlist_entries_cached("flight")] == ["DAL456"]
    db.upsert_watchlist_entry({**_entry(), "identifier": "DAL789"})
    assert [e["identifier"] for e in db.get_watchlist_entries_cached("flight")] == ["DAL789"]
    db.delete_watchlist_entry("wl-flight-test-1")
    assert db.get_watchlist_entries_cached("flight") == []


def test_entry_types_are_cached_separately_and_copies_are_returned(clock):
    _insert_raw("a", "DAL1")
    got = db.get_watchlist_entries_cached("flight")
    got[0]["identifier"] = "MUTATED"
    assert db.get_watchlist_entries_cached("flight")[0]["identifier"] == "DAL1"
    assert db.get_watchlist_entries_cached("vessel") == []


def test_ttl_default_is_three_seconds():
    assert db.WATCHLIST_CACHE_TTL_S == 3.0


def test_shared_get_active_entries_cached_flag(clock):
    from shared import watchlist as wl
    with mock.patch.object(db, "get_watchlist_entries_cached", return_value=["c"]) as cached, \
         mock.patch.object(db, "get_watchlist_entries", return_value=["u"]) as uncached:
        assert wl.get_active_entries("flight", cached=True) == ["c"]
        assert wl.get_active_entries("flight") == ["u"]
    cached.assert_called_once_with(entry_type="flight")
    uncached.assert_called_once_with(entry_type="flight")


def test_hot_ingest_paths_read_through_the_cache():
    for rel in ("src/ingest/parsers/fdps_parser.py", "src/ingest/parsers/smes_parser.py"):
        src = (REPO / rel).read_text()
        assert 'get_active_entries(entry_type="flight", cached=True)' in src, rel


def test_every_watchlist_writer_drops_the_cache():
    """Bug once, lint forever: a new db.py function that writes
    watchlist_entries must be in _WATCHLIST_WRITERS (wrapped), or a process
    would read its own stale entries for up to the TTL."""
    src = (REPO / "src/common/db.py").read_text()
    tree = ast.parse(src)
    writers = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            seg = ast.get_source_segment(src, node) or ""
            if re.search(r"(UPDATE|INSERT INTO|DELETE FROM|REPLACE INTO)\s+watchlist_entries\b", seg):
                writers.add(node.name)
    assert writers, "no writers found -- the scan is broken"
    missing = writers - set(db._WATCHLIST_WRITERS)
    assert not missing, f"watchlist writers not invalidating the read cache: {sorted(missing)}"
    for name in db._WATCHLIST_WRITERS:
        assert getattr(getattr(db, name), "_invalidates_watchlist_cache", False), name
