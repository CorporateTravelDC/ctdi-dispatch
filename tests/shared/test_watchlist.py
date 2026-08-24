"""Unit tests for the shared watchlist module."""
import json
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso(offset_minutes: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_transient_flight(identifier: str = "AAL123",
                           expire_offset_min: int = 60) -> dict:
    return {
        "id": f"wl-flight-{identifier.lower()}-test",
        "entry_type": "flight",
        "tier": "transient",
        "identifier": identifier,
        "origin": "KORD",
        "destination": "KDCA",
        "route_name": None,
        "scheduled_departure": None,
        "scheduled_arrival": None,
        "auto_remove_at": _now_iso(expire_offset_min),
        "added_at": _now_iso(),
        "added_by": "test",
        "notes": None,
        "last_event_at": None,
        "last_event_summary": None,
    }


def _make_permanent_train(identifier: str = "2171") -> dict:
    return {
        "id": f"perm-train-{identifier}",
        "entry_type": "train",
        "tier": "permanent",
        "identifier": identifier,
        "origin": "BOS",
        "destination": "WAS",
        "route_name": "Acela",
        "scheduled_departure": None,
        "scheduled_arrival": None,
        "auto_remove_at": None,
        "added_at": _now_iso(),
        "added_by": "operator",
        "notes": None,
        "last_event_at": None,
        "last_event_summary": None,
    }


class _IsolatedDB:
    """
    Context manager that redirects common.db to a temporary in-memory SQLite DB.
    Ensures tests don't touch the real /var/lib/corporatetraveldc database.

    2026-08-20: also redirects common.config.state_dir() to an isolated
    temp directory. Found while chasing a *second*, previously-masked bug:
    watchlist_event_hit()'s dedup (shared.watchlist._watchlist_dedup, a
    module-level PushDedup singleton -- see common/push_dedup.py) persists
    its state to a REAL FILE under state_dir()
    (pusher-watchlist-event-dedup.json), deliberately, for cross-process
    correctness in production (multiple ingest containers + poller share
    it). Nothing isolated state_dir() before this, so every test run of
    watchlist_event_hit() was reading/writing the ACTUAL production dedup
    state file -- confirmed live: a real 14KB
    /var/lib/corporatetraveldc/pusher-watchlist-event-dedup.json with a
    same-day mtime. That let content from one test run suppress the exact
    same test's push in a LATER run (the whole point of dedup, just aimed
    at the wrong file), which is why test_watchlist_event_hit_writes_
    history/_deduplication/_different_types_not_deduped failed
    intermittently depending on what had run before them in the same
    process -- invisible until the schema-staleness fix above let these
    tests get far enough to reach the dedup check at all.
    """
    def __enter__(self):
        import common.db as _db
        import common.config as _config
        self._orig_path = _db._db_path
        self._orig_state_dir = _config.state_dir

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._tmp_path = tmp.name
        self._tmp_state_dir = tempfile.mkdtemp(prefix="ctdi-test-state-")

        _db._db_path = lambda: Path(self._tmp_path)
        _config.state_dir = lambda: self._tmp_state_dir
        # 2026-08-20: was a hand-listed init_db()..init_db_v5() chain that
        # silently fell 29 schema versions behind (hex_id/registration,
        # added v18, were among the casualties) -- init_db_all() introspects
        # every init_db_vN() in common.db and runs the full current chain,
        # so this can't go stale again the same way. See db.init_db_all()'s
        # own docstring for the full rationale.
        _db.init_db_all()
        return self

    def __exit__(self, *_):
        import common.db as _db
        import common.config as _config
        import shutil
        _db._db_path = self._orig_path
        _config.state_dir = self._orig_state_dir
        Path(self._tmp_path).unlink(missing_ok=True)
        shutil.rmtree(self._tmp_state_dir, ignore_errors=True)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_sweep_removes_only_expired_transient():
    with _IsolatedDB():
        from common import db
        from shared.watchlist import sweep_expired_transient

        # One expired transient, one active transient, one permanent.
        expired = _make_transient_flight("TST001", expire_offset_min=-5)
        active = _make_transient_flight("TST002", expire_offset_min=60)
        permanent = _make_permanent_train("9999")

        db.upsert_watchlist_entry(expired)
        db.upsert_watchlist_entry(active)
        db.upsert_watchlist_entry(permanent)

        removed = sweep_expired_transient()
        assert removed == 1

        remaining = db.get_watchlist_entries()
        ids = {e["id"] for e in remaining}
        assert active["id"] in ids
        assert permanent["id"] in ids
        assert expired["id"] not in ids


def test_sweep_writes_history_record():
    with _IsolatedDB():
        from common import db
        from shared.watchlist import sweep_expired_transient

        expired = _make_transient_flight("TST003", expire_offset_min=-10)
        db.upsert_watchlist_entry(expired)
        sweep_expired_transient()

        history = db.get_watchlist_history(entry_id=expired["id"])
        assert len(history) == 1
        assert history[0]["event_type"] == "auto_expired"


def test_watchlist_event_hit_writes_history(monkeypatch=None):
    with _IsolatedDB():
        from common import db
        from shared import watchlist as wl

        entry = _make_transient_flight("AAL999")
        db.upsert_watchlist_entry(entry)

        fired = []

        def _fake_ntfy(domain_topic, title, detail_body, dispatch_body, priority):
            fired.append((domain_topic, dispatch_body))

        with patch("shared.watchlist._fire_ntfy_dual", side_effect=_fake_ntfy):
            wl.watchlist_event_hit(
                entry["id"],
                "AAL999 filed KORD→KDCA",
                {"watchlist_trigger": "fdps_fh"},
                priority=3,
            )

        history = db.get_watchlist_history(entry_id=entry["id"])
        assert len(history) == 1
        assert history[0]["event_summary"] == "AAL999 filed KORD→KDCA"
        assert len(fired) == 1
        assert fired[0][0] == "flight-alerts"


def test_watchlist_event_hit_deduplication():
    with _IsolatedDB():
        from common import db
        from shared import watchlist as wl

        entry = _make_transient_flight("DAL777")
        db.upsert_watchlist_entry(entry)

        fired = []

        def _fake_ntfy(*args, **kwargs):
            fired.append(args)

        with patch("shared.watchlist._fire_ntfy_dual", side_effect=_fake_ntfy):
            detail = {"watchlist_trigger": "fdps_fh"}
            wl.watchlist_event_hit(entry["id"], "first event", detail)
            wl.watchlist_event_hit(entry["id"], "second event same type", detail)

        # Only one ntfy push should have fired (second was deduplicated).
        assert len(fired) == 1


def test_watchlist_event_hit_different_types_not_deduped():
    with _IsolatedDB():
        from common import db
        from shared import watchlist as wl

        entry = _make_transient_flight("UAL555")
        db.upsert_watchlist_entry(entry)

        fired = []

        def _fake_ntfy(*args, **kwargs):
            fired.append(args)

        with patch("shared.watchlist._fire_ntfy_dual", side_effect=_fake_ntfy):
            wl.watchlist_event_hit(entry["id"], "filed", {"watchlist_trigger": "fdps_fh"})
            wl.watchlist_event_hit(entry["id"], "cancelled", {"watchlist_trigger": "fdps_cl"})

        assert len(fired) == 2


def test_watchlist_file_watcher_upserts_new_entry(tmp_path):
    with _IsolatedDB():
        from common import db
        from shared.watchlist import WatchlistFileWatcher

        flights_file = tmp_path / "permanent_flights.json"
        flights_file.write_text(json.dumps({"watchlist": [
            {"id": "perm-flight-test1", "identifier": "TST001",
             "origin": "KDCA", "destination": "KORD",
             "added": "2026-05-27", "added_by": "test"}
        ]}))
        trains_file = tmp_path / "permanent_trains.json"
        trains_file.write_text(json.dumps({"watchlist": []}))

        with patch("shared.watchlist.PERMANENT_WATCHLIST_DIR", tmp_path):
            watcher = WatchlistFileWatcher()
            watcher._load_all()

        entries = db.get_watchlist_entries(entry_type="flight")
        assert any(e["identifier"] == "TST001" for e in entries)


def test_watchlist_file_watcher_removes_deleted_entry(tmp_path):
    with _IsolatedDB():
        from common import db
        from shared.watchlist import WatchlistFileWatcher

        flights_file = tmp_path / "permanent_flights.json"
        trains_file = tmp_path / "permanent_trains.json"
        trains_file.write_text(json.dumps({"watchlist": []}))

        # First load: two entries.
        flights_file.write_text(json.dumps({"watchlist": [
            {"id": "perm-flight-a", "identifier": "AAA001",
             "added": "2026-05-27", "added_by": "test"},
            {"id": "perm-flight-b", "identifier": "BBB002",
             "added": "2026-05-27", "added_by": "test"},
        ]}))

        with patch("shared.watchlist.PERMANENT_WATCHLIST_DIR", tmp_path):
            watcher = WatchlistFileWatcher()
            watcher._load_all()

        assert len(db.get_watchlist_entries(entry_type="flight")) == 2

        # Second load: only one entry — BBB002 removed from file.
        flights_file.write_text(json.dumps({"watchlist": [
            {"id": "perm-flight-a", "identifier": "AAA001",
             "added": "2026-05-27", "added_by": "test"},
        ]}))

        with patch("shared.watchlist.PERMANENT_WATCHLIST_DIR", tmp_path):
            watcher._load_file("permanent_flights.json", flights_file)

        remaining = db.get_watchlist_entries(entry_type="flight")
        assert len(remaining) == 1
        assert remaining[0]["identifier"] == "AAA001"

        history = db.get_watchlist_history(entry_id="perm-flight-b")
        assert any(h["event_type"] == "permanent_removed" for h in history)


def test_watchlist_file_watcher_invalid_json_does_not_remove(tmp_path):
    with _IsolatedDB():
        from common import db
        from shared.watchlist import WatchlistFileWatcher

        flights_file = tmp_path / "permanent_flights.json"
        trains_file = tmp_path / "permanent_trains.json"
        trains_file.write_text(json.dumps({"watchlist": []}))

        flights_file.write_text(json.dumps({"watchlist": [
            {"id": "perm-flight-stable", "identifier": "STA001",
             "added": "2026-05-27", "added_by": "test"},
        ]}))

        with patch("shared.watchlist.PERMANENT_WATCHLIST_DIR", tmp_path):
            watcher = WatchlistFileWatcher()
            watcher._load_all()

        assert len(db.get_watchlist_entries(entry_type="flight")) == 1

        # Write invalid JSON — watcher should skip and keep DB intact.
        flights_file.write_text("{invalid json}")

        with patch("shared.watchlist.PERMANENT_WATCHLIST_DIR", tmp_path):
            watcher._load_file("permanent_flights.json", flights_file)

        assert len(db.get_watchlist_entries(entry_type="flight")) == 1


# ── extend_auto_remove_for_delay (2026-08-23) ──────────────────────────────────
# NOTE: anchored on _now_iso()/relative timedeltas, deliberately NOT hardcoded
# absolute dates -- an earlier revision hardcoded "2026-08-23T..."/"2026-08-24T..."
# strings, which worked at the time but silently became a time bomb: once real
# wall-clock time crossed into 2026-08-24, get_watchlist_entries()'s own
# `auto_remove_at > now` filter started excluding those fixture rows as
# already-expired, and test_extend_auto_remove_for_delay_ontime_departure_no_extension
# (whose value never got extended) was the first to actually fail. Found live,
# not theoretically -- re-running the full suite after the chronology work
# turned up a real regression here.

def test_extend_auto_remove_for_delay_extends_by_real_delay():
    """Scheduled dep now, scheduled arr +4.5h (auto_remove_at = arr+6h).
    Actual OFF +2h late -> auto_remove_at extends by 2h -- 8h past the
    *originally* scheduled arrival, matching the operator's own worked
    example (14:00 dep / 18:30 arr / 16:00 actual OFF)."""
    with _IsolatedDB():
        from common import db
        from shared.watchlist import extend_auto_remove_for_delay

        dep = datetime.now(timezone.utc)
        arr = dep + timedelta(hours=4, minutes=30)
        base_expiry = arr + timedelta(hours=6)
        sched_dep = dep.strftime("%Y-%m-%dT%H:%M:%SZ")
        sched_arr = arr.strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = _make_transient_flight("DAL2")
        entry["scheduled_departure"] = sched_dep
        entry["scheduled_arrival"] = sched_arr
        entry["auto_remove_at"] = base_expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
        db.upsert_watchlist_entry(entry)

        actual_off = (dep + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 2h late
        extend_auto_remove_for_delay(entry, actual_off, sched_dep, sched_arr)

        row = db.get_watchlist_entries(entry_type="flight")[0]
        expected = (base_expiry + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert row["auto_remove_at"] == expected
        assert row["departure_delay_min"] == 120


def test_extend_auto_remove_for_delay_ontime_departure_no_extension():
    """An on-time (or early) departure must not extend the window, but
    still gets marked processed (delay_min=0, not left NULL) so a resent
    OOOI message is recognized as already-handled."""
    with _IsolatedDB():
        from common import db
        from shared.watchlist import extend_auto_remove_for_delay

        dep = datetime.now(timezone.utc)
        arr = dep + timedelta(hours=4, minutes=30)
        base_expiry = arr + timedelta(hours=6)
        sched_dep = dep.strftime("%Y-%m-%dT%H:%M:%SZ")
        sched_arr = arr.strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = _make_transient_flight("DAL3")
        entry["scheduled_departure"] = sched_dep
        entry["scheduled_arrival"] = sched_arr
        entry["auto_remove_at"] = base_expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
        db.upsert_watchlist_entry(entry)

        actual_off = (dep - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 5min early
        extend_auto_remove_for_delay(entry, actual_off, sched_dep, sched_arr)

        row = db.get_watchlist_entries(entry_type="flight")[0]
        assert row["auto_remove_at"] == base_expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert row["departure_delay_min"] == 0


def test_extend_auto_remove_for_delay_is_idempotent():
    """A resent airlineOffTime (same or different value) on a later TFMS
    message must not extend the window a second time."""
    with _IsolatedDB():
        from common import db
        from shared.watchlist import extend_auto_remove_for_delay

        dep = datetime.now(timezone.utc)
        arr = dep + timedelta(hours=4, minutes=30)
        base_expiry = arr + timedelta(hours=6)
        sched_dep = dep.strftime("%Y-%m-%dT%H:%M:%SZ")
        sched_arr = arr.strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = _make_transient_flight("DAL4")
        entry["scheduled_departure"] = sched_dep
        entry["scheduled_arrival"] = sched_arr
        entry["auto_remove_at"] = base_expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
        db.upsert_watchlist_entry(entry)

        expected_extended = (base_expiry + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        off_2h_late = (dep + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        extend_auto_remove_for_delay(entry, off_2h_late, sched_dep, sched_arr)
        first = db.get_watchlist_entries(entry_type="flight")[0]
        assert first["auto_remove_at"] == expected_extended

        # Re-fetch as the real caller would (fresh dict, departure_delay_min
        # now 120 not None) and call again with a much larger claimed delay.
        off_6h_late = (dep + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        extend_auto_remove_for_delay(first, off_6h_late, sched_dep, sched_arr)
        second = db.get_watchlist_entries(entry_type="flight")[0]
        assert second["auto_remove_at"] == expected_extended
        assert second["departure_delay_min"] == 120


def test_extend_auto_remove_for_delay_corrects_fallback_base():
    """An entry added before scheduled_arrival was known (added_at+24h
    fallback, see _default_auto_remove_at()) must have its base corrected
    onto scheduled_arrival+6h once TFMS supplies originalArrival, THEN
    have any real delay added on top -- not extend off the arbitrary
    added-time fallback."""
    with _IsolatedDB():
        from common import db
        from shared.watchlist import extend_auto_remove_for_delay

        entry = _make_transient_flight("ASA2", expire_offset_min=24 * 60)
        entry["scheduled_departure"] = None
        entry["scheduled_arrival"] = None
        db.upsert_watchlist_entry(entry)

        dep = datetime.now(timezone.utc)
        arr = dep + timedelta(hours=4, minutes=30)
        sched_dep = dep.strftime("%Y-%m-%dT%H:%M:%SZ")
        sched_arr = arr.strftime("%Y-%m-%dT%H:%M:%SZ")
        actual_off = (dep + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 1h late
        extend_auto_remove_for_delay(entry, actual_off, sched_dep, sched_arr)

        row = db.get_watchlist_entries(entry_type="flight")[0]
        # base corrected to arr+6h, then +1h delay
        expected = (arr + timedelta(hours=6) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert row["auto_remove_at"] == expected
        assert row["departure_delay_min"] == 60
        assert row["scheduled_departure"] == sched_dep
        assert row["scheduled_arrival"] == sched_arr


def test_sweep_does_not_remove_permanent_expired_by_time():
    """Permanent entries have auto_remove_at=NULL and must never be swept."""
    with _IsolatedDB():
        from common import db
        from shared.watchlist import sweep_expired_transient

        perm = _make_permanent_train("8888")
        perm["auto_remove_at"] = None
        db.upsert_watchlist_entry(perm)

        removed = sweep_expired_transient()
        assert removed == 0
        assert len(db.get_watchlist_entries()) == 1


if __name__ == "__main__":
    # Quick smoke-run without pytest.
    import traceback
    tests = [
        test_sweep_removes_only_expired_transient,
        test_sweep_writes_history_record,
        test_watchlist_event_hit_writes_history,
        test_watchlist_event_hit_deduplication,
        test_watchlist_event_hit_different_types_not_deduped,
        test_sweep_does_not_remove_permanent_expired_by_time,
    ]
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
