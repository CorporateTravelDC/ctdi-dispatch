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
    """
    def __enter__(self):
        import common.db as _db
        self._orig_path = _db._db_path

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._tmp_path = tmp.name

        _db._db_path = lambda: Path(self._tmp_path)
        _db.init_db()
        _db.init_db_v2()
        _db.init_db_v3()
        _db.init_db_v4()
        _db.init_db_v5()
        return self

    def __exit__(self, *_):
        import common.db as _db
        _db._db_path = self._orig_path
        Path(self._tmp_path).unlink(missing_ok=True)


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
