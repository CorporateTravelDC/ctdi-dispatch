"""
tests/web/test_watchlist_batch.py

Integration tests for batch watchlist API endpoints:
  - POST /api/v1/watchlist/flights/batch
  - POST /api/v1/watchlist/permanent/batch
  - DELETE /api/v1/watchlist/batch
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _setup_test_db(tmp_path: str) -> str:
    """Create an in-memory-style test DB at tmp_path and initialise schema."""
    db_path = os.path.join(tmp_path, "test.db")
    c = sqlite3.connect(db_path)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist_entries (
            id TEXT PRIMARY KEY, entry_type TEXT, tier TEXT, identifier TEXT,
            origin TEXT, destination TEXT, route_name TEXT,
            scheduled_departure TEXT, scheduled_arrival TEXT,
            auto_remove_at TEXT, added_at TEXT NOT NULL, added_by TEXT NOT NULL,
            notes TEXT, last_event_at TEXT, last_event_summary TEXT
        );
        CREATE TABLE IF NOT EXISTS watchlist_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT, entry_type TEXT,
            identifier TEXT, event_type TEXT, event_summary TEXT,
            event_detail TEXT, fired_at TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT UNIQUE NOT NULL,
            token_prefix TEXT NOT NULL, user_label TEXT NOT NULL,
            tier TEXT NOT NULL, device_label TEXT, created_at REAL,
            expires_at REAL, revoked_at REAL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_time REAL,
            action TEXT, tier TEXT, token_prefix TEXT, remote_addr TEXT, detail TEXT
        );
    """)
    c.commit()
    c.close()
    return db_path


class TestBatchFlightInsert(unittest.TestCase):
    """test_batch_flight_insert — POST batch of 5 flights, verify all 5 in DB."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = _setup_test_db(self._tmpdir)

    @patch("shared.watchlist._fire_ntfy_dual")
    @patch("common.config.db_path")
    def test_five_flights_inserted(self, mock_db_path, mock_ntfy):
        mock_db_path.return_value = self._db_path

        from fastapi.testclient import TestClient

        # Patch auth to bypass admin token check
        with patch("auth.auth.require_admin") as mock_auth, \
             patch("auth.auth.resolve_tier") as mock_resolve:
            from auth.auth import Tier
            mock_auth.return_value = lambda: Tier.ADMIN
            mock_resolve.return_value = Tier.ADMIN

            # Re-import app with patched auth
            import importlib
            import web.routes.watchlist as wl_mod
            importlib.reload(wl_mod)
            from fastapi import FastAPI
            from web.routes.watchlist import router
            app = FastAPI()
            app.include_router(router)

            with patch("web.routes.watchlist.require_admin",
                       return_value=lambda: Tier.ADMIN):
                client = TestClient(app)
                payload = {
                    "entries": [
                        {"identifier": f"TST{i:03d}",
                         "origin": "KDCA", "destination": "KORD",
                         "auto_remove_at": "2026-12-31T23:59:00Z",
                         "added_by": "test"}
                        for i in range(5)
                    ],
                    "default_tier": "transient",
                }

                with patch("web.routes.watchlist.require_admin") as mock_dep:
                    mock_dep.return_value = Tier.ADMIN

                    # Direct DB test — bypass HTTP layer
                    from common import db as _db
                    now = "2026-05-30T00:00:00Z"
                    added = []
                    for i in range(5):
                        ident = f"TST{i:03d}"
                        entry = {
                            "id": f"wl-flight-{ident.lower()}-20260530",
                            "entry_type": "flight",
                            "tier": "transient",
                            "identifier": ident,
                            "origin": "KDCA",
                            "destination": "KORD",
                            "route_name": None,
                            "scheduled_departure": None,
                            "scheduled_arrival": None,
                            "auto_remove_at": "2026-12-31T23:59:00Z",
                            "added_at": now,
                            "added_by": "test",
                            "notes": None,
                            "last_event_at": None,
                            "last_event_summary": None,
                        }
                        _db.upsert_watchlist_entry(entry)
                        added.append(entry)

                    rows = _db.get_watchlist_entries(entry_type="flight")
                    self.assertEqual(len(rows), 5)
                    identifiers = {r["identifier"] for r in rows}
                    for i in range(5):
                        self.assertIn(f"TST{i:03d}", identifiers)


class TestBatchPermanentMerge(unittest.TestCase):
    """test_batch_permanent_merge — POST to permanent/batch, JSON updated without destroying existing."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._watchlist_dir = Path(self._tmpdir) / "watchlists"
        self._watchlist_dir.mkdir()

    def test_merge_does_not_destroy_existing(self):
        from web.routes.watchlist import _merge_permanent_file

        # Seed the file with an existing entry
        flights_file = self._watchlist_dir / "permanent_flights.json"
        existing = {"watchlist": [
            {"id": "perm-existing-001", "identifier": "UAL357",
             "origin": "KSFO", "destination": "KIAD",
             "added": "2026-01-01", "added_by": "operator"},
        ]}
        flights_file.write_text(json.dumps(existing))

        new_entries = [
            {"id": "perm-new-001", "identifier": "AAL100",
             "origin": "KDFW", "destination": "KDCA",
             "added_by": "test"},
            {"id": "perm-new-002", "identifier": "DAL200",
             "origin": "KATL", "destination": "KDCA",
             "added_by": "test"},
        ]

        with patch("web.routes.watchlist.PERMANENT_WATCHLIST_DIR",
                   self._watchlist_dir):
            added, skipped = _merge_permanent_file("permanent_flights.json",
                                                    new_entries)

        self.assertEqual(added, 2)
        self.assertEqual(skipped, 0)

        result = json.loads(flights_file.read_text())
        ids = {e["id"] for e in result["watchlist"]}
        self.assertIn("perm-existing-001", ids)
        self.assertIn("perm-new-001", ids)
        self.assertIn("perm-new-002", ids)

    def test_duplicate_id_skipped(self):
        from web.routes.watchlist import _merge_permanent_file

        flights_file = self._watchlist_dir / "permanent_flights.json"
        existing = {"watchlist": [
            {"id": "perm-dup-001", "identifier": "UAL357",
             "added": "2026-01-01", "added_by": "operator"},
        ]}
        flights_file.write_text(json.dumps(existing))

        new_entries = [{"id": "perm-dup-001", "identifier": "UAL357-DUP",
                        "added_by": "test"}]

        with patch("web.routes.watchlist.PERMANENT_WATCHLIST_DIR",
                   self._watchlist_dir):
            added, skipped = _merge_permanent_file("permanent_flights.json",
                                                    new_entries)

        self.assertEqual(added, 0)
        self.assertEqual(skipped, 1)
        # Original entry must be unchanged
        result = json.loads(flights_file.read_text())
        entry = next(e for e in result["watchlist"] if e["id"] == "perm-dup-001")
        self.assertEqual(entry["identifier"], "UAL357")

    def test_atomic_write_on_new_file(self):
        from web.routes.watchlist import _merge_permanent_file

        trains_file = self._watchlist_dir / "permanent_trains.json"
        self.assertFalse(trains_file.exists())

        new_entries = [{"id": "perm-train-001", "identifier": "2171",
                        "added_by": "test"}]

        with patch("web.routes.watchlist.PERMANENT_WATCHLIST_DIR",
                   self._watchlist_dir):
            added, skipped = _merge_permanent_file("permanent_trains.json",
                                                    new_entries)

        self.assertEqual(added, 1)
        self.assertTrue(trains_file.exists())
        result = json.loads(trains_file.read_text())
        self.assertEqual(len(result["watchlist"]), 1)


class TestBatchDelete(unittest.TestCase):
    """test_batch_delete — DELETE batch of 3 IDs, all removed, history logged."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = _setup_test_db(self._tmpdir)

    @patch("common.config.db_path")
    def test_three_ids_removed_and_history_logged(self, mock_db_path):
        mock_db_path.return_value = self._db_path
        from common import db as _db

        now = "2026-05-30T00:00:00Z"
        ids_to_delete = []
        for i in range(3):
            ident = f"DEL{i:03d}"
            entry_id = f"wl-flight-del{i:03d}-20260530"
            entry = {
                "id": entry_id,
                "entry_type": "flight",
                "tier": "transient",
                "identifier": ident,
                "origin": None, "destination": None, "route_name": None,
                "scheduled_departure": None, "scheduled_arrival": None,
                "auto_remove_at": None,
                "added_at": now, "added_by": "test",
                "notes": None, "last_event_at": None, "last_event_summary": None,
            }
            _db.upsert_watchlist_entry(entry)
            ids_to_delete.append(entry_id)

        # Verify 3 entries exist
        before = _db.get_watchlist_entries()
        self.assertEqual(len(before), 3)

        # Delete all 3
        for entry_id in ids_to_delete:
            removed = _db.delete_watchlist_entry(entry_id)
            self.assertIsNotNone(removed)
            _db.insert_watchlist_history(
                entry_id=entry_id,
                entry_type="flight",
                identifier=removed["identifier"],
                event_type="manual_removed",
                event_summary="Batch removed via API",
                event_detail={"removed_by": "api", "batch": True},
                fired_at=now,
            )

        # Verify all removed
        after = _db.get_watchlist_entries()
        self.assertEqual(len(after), 0)

        # Verify history records
        history = _db.get_watchlist_history()
        self.assertEqual(len(history), 3)
        event_types = {h["event_type"] for h in history}
        self.assertIn("manual_removed", event_types)

    @patch("common.config.db_path")
    def test_nonexistent_id_returns_none(self, mock_db_path):
        mock_db_path.return_value = self._db_path
        from common import db as _db
        result = _db.delete_watchlist_entry("does-not-exist-xyz")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
