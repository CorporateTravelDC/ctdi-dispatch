"""
tests/ingest/test_local_airspace.py

Unit tests for ingest.local_airspace:
  - UltraFeeder aircraft.json parsing, distance computation, watchlist matching
  - Marine One detection logic
  - Emergency squawk detection
  - ACARS OOOI label parsing and watchlist callsign matching
  - Clean skip when ULTRAFEEDER_URL is unset
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Fixtures ──────────────────────────────────────────────────────────────────

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ultrafeeder_aircraft.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _make_db() -> sqlite3.Connection:
    """In-memory DB with full schema for testing."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS local_aircraft (
            icao_hex TEXT PRIMARY KEY, callsign TEXT, registration TEXT,
            aircraft_type TEXT, operator TEXT, latitude REAL, longitude REAL,
            altitude_ft INTEGER, ground_speed INTEGER, track_deg REAL,
            squawk TEXT, on_ground INTEGER DEFAULT 0, rssi REAL,
            distance_nm REAL, last_seen TEXT NOT NULL, first_seen TEXT NOT NULL,
            source TEXT DEFAULT 'ultrafeeder'
        );
        CREATE TABLE IF NOT EXISTS acars_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT NOT NULL,
            freq_mhz REAL, icao_hex TEXT, tail TEXT, flight TEXT,
            msg_type TEXT, label TEXT, block_id TEXT, ack TEXT, mode TEXT,
            msg_text TEXT, raw TEXT, watchlist_hit INTEGER DEFAULT 0,
            watchlist_entry_id TEXT
        );
        CREATE TABLE IF NOT EXISTS local_airspace_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fired_at TEXT NOT NULL,
            alert_type TEXT NOT NULL, icao_hex TEXT, callsign TEXT,
            registration TEXT, distance_nm REAL, altitude_ft INTEGER,
            squawk TEXT, watchlist_entry_id TEXT, payload TEXT,
            ntfy_fired INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS watchlist_entries (
            id TEXT PRIMARY KEY, entry_type TEXT, tier TEXT, identifier TEXT,
            origin TEXT, destination TEXT, route_name TEXT,
            scheduled_departure TEXT, scheduled_arrival TEXT,
            auto_remove_at TEXT, added_at TEXT, added_by TEXT, notes TEXT,
            last_event_at TEXT, last_event_summary TEXT
        );
        CREATE TABLE IF NOT EXISTS watchlist_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT, entry_type TEXT,
            identifier TEXT, event_type TEXT, event_summary TEXT,
            event_detail TEXT, fired_at TEXT
        );
        CREATE TABLE IF NOT EXISTS hot_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, computed_at REAL,
            route_narrative TEXT, active_tfrs TEXT, vip_flags TEXT
        );
    """)
    c.commit()
    return c


# ── Test: haversine distance ──────────────────────────────────────────────────

class TestHaversine(unittest.TestCase):
    def test_zero_distance(self):
        from ingest.local_airspace import _haversine_nm
        self.assertAlmostEqual(_haversine_nm(38.88, -77.09, 38.88, -77.09), 0, places=3)

    def test_known_distance(self):
        from ingest.local_airspace import _haversine_nm
        # DCA (38.8521, -77.0377) to IAD (38.9531, -77.4565) ~23nm
        dist = _haversine_nm(38.8521, -77.0377, 38.9531, -77.4565)
        self.assertGreater(dist, 20)
        self.assertLess(dist, 30)


# ── Test: UltraFeeder JSON parsing ────────────────────────────────────────────

class TestUltrafeederJsonParse(unittest.TestCase):
    """test_ultrafeeder_json_parse — parse fixture, verify upsert and distance."""

    def setUp(self):
        self._orig_db_path = None
        self._tmpdir = tempfile.mkdtemp()

    @patch("ingest.local_airspace.ULTRAFEEDER_AIRCRAFT_URL",
           "http://mock/data/aircraft.json")
    @patch("ingest.local_airspace.RECEIVER_LAT", 38.8816)
    @patch("ingest.local_airspace.RECEIVER_LON", -77.0910)
    @patch("ingest.local_airspace.ALERT_RADIUS_NM", 100.0)
    @patch("ingest.local_airspace._local_dedup_check", return_value=True)
    @patch("ingest.local_airspace.get_active_entries", return_value=[])
    @patch("ingest.local_airspace._fire_ntfy")
    def test_upsert_and_distance(self, mock_ntfy, mock_wl, mock_dedup):
        fixture = _load_fixture()

        inserted_rows: list[dict] = []

        def capture_upsert(**kwargs):
            inserted_rows.append(kwargs)

        with patch("ingest.local_airspace.requests.get") as mock_get, \
             patch("ingest.local_airspace.db.upsert_local_aircraft",
                   side_effect=capture_upsert):
            mock_resp = MagicMock()
            mock_resp.json.return_value = fixture
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            from ingest.local_airspace import _poll_ultrafeeder
            result = _poll_ultrafeeder()

        self.assertTrue(result)
        # UAL220 should be in the upserted rows
        ual220 = next((r for r in inserted_rows
                       if r.get("icao_hex") == "a4b2c1"), None)
        self.assertIsNotNone(ual220)
        self.assertEqual(ual220["callsign"], "UAL220")
        self.assertEqual(ual220["altitude_ft"], 3500)
        self.assertIsNotNone(ual220["distance_nm"])
        self.assertGreater(ual220["distance_nm"], 0)
        # Aircraft at receiver lat/lon should have near-zero distance
        jsxrow = next((r for r in inserted_rows
                       if r.get("icao_hex") == "a66666"), None)
        self.assertIsNotNone(jsxrow)
        self.assertLess(jsxrow["distance_nm"], 1.0)

    @patch("ingest.local_airspace.ULTRAFEEDER_AIRCRAFT_URL", "")
    def test_skips_when_url_empty(self):
        from ingest.local_airspace import _poll_ultrafeeder
        result = _poll_ultrafeeder()
        self.assertFalse(result)


# ── Test: watchlist proximity detection ──────────────────────────────────────

class TestWatchlistProximity(unittest.TestCase):
    """Verify watchlist callsign hit fires watchlist_event_hit within alert radius."""

    @patch("ingest.local_airspace.ULTRAFEEDER_AIRCRAFT_URL",
           "http://mock/data/aircraft.json")
    @patch("ingest.local_airspace.RECEIVER_LAT", 38.8816)
    @patch("ingest.local_airspace.RECEIVER_LON", -77.0910)
    @patch("ingest.local_airspace.ALERT_RADIUS_NM", 50.0)
    @patch("ingest.local_airspace._local_dedup_check", return_value=False)
    @patch("ingest.local_airspace.watchlist_event_hit")
    @patch("ingest.local_airspace.db.insert_local_airspace_alert")
    @patch("ingest.local_airspace.db.upsert_local_aircraft")
    def test_watchlist_match_fires_alert(self, mock_upsert, mock_alert_insert,
                                         mock_event_hit, mock_dedup):
        fixture = _load_fixture()
        # UAL220 is in the fixture at ~3nm from receiver
        wl_entry = {
            "id": "wl-flight-ual220-test",
            "entry_type": "flight",
            "identifier": "UAL220",
            "origin": None, "destination": None,
        }

        with patch("ingest.local_airspace.requests.get") as mock_get, \
             patch("ingest.local_airspace.get_active_entries",
                   return_value=[wl_entry]):
            mock_resp = MagicMock()
            mock_resp.json.return_value = fixture
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            from ingest.local_airspace import _poll_ultrafeeder
            _poll_ultrafeeder()

        # watchlist_event_hit should have been called for UAL220
        calls = [str(c) for c in mock_event_hit.call_args_list]
        self.assertTrue(any("UAL220" in c or "wl-flight-ual220-test" in c
                            for c in calls),
                        f"Expected UAL220 watchlist hit. Calls: {calls}")


# ── Test: Marine One detection ────────────────────────────────────────────────

class TestMarineOneDetectionLocal(unittest.TestCase):
    """test_marine_one_detection_local — callsign + proximity → priority-5 dispatch."""

    @patch("ingest.local_airspace.ULTRAFEEDER_AIRCRAFT_URL",
           "http://mock/data/aircraft.json")
    @patch("ingest.local_airspace.RECEIVER_LAT", 38.8816)
    @patch("ingest.local_airspace.RECEIVER_LON", -77.0910)
    @patch("ingest.local_airspace.MARINE_ONE_ALERT_RADIUS_NM", 100.0)
    @patch("ingest.local_airspace._local_dedup_check", return_value=False)
    @patch("ingest.local_airspace._fire_ntfy")
    @patch("ingest.local_airspace.db.upsert_local_aircraft")
    @patch("ingest.local_airspace.db.insert_local_airspace_alert")
    @patch("ingest.local_airspace.db.insert_route_narrative")
    @patch("ingest.local_airspace.get_active_entries", return_value=[])
    def test_marine_one_fires_dispatch_only(self, mock_wl, mock_narrative,
                                            mock_alert, mock_upsert,
                                            mock_ntfy, mock_dedup):
        fixture = _load_fixture()
        # MARINE1 is in the fixture (hex d00000, squawk 5000)

        with patch("ingest.local_airspace.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = fixture
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            from ingest.local_airspace import _poll_ultrafeeder
            _poll_ultrafeeder()

        # _fire_ntfy signature: (topic, title, body, priority)
        # priority may be positional or keyword depending on call site
        def _get_priority(call):
            if len(call.args) >= 4:
                return call.args[3]
            return call.kwargs.get("priority", 0)

        ntfy_calls = [(c.args[0], _get_priority(c)) for c in mock_ntfy.call_args_list
                      if c.args]
        dispatch_calls = [c for c in ntfy_calls if c[0] == "dispatch"]
        flight_alert_calls = [c for c in ntfy_calls
                               if c[0] == "flight-alerts"]

        self.assertTrue(
            any(p == 5 for _, p in dispatch_calls),
            f"Expected priority-5 dispatch push. Got: {ntfy_calls}"
        )
        self.assertEqual(flight_alert_calls, [],
                         "Marine One must NOT fire to flight-alerts")


# ── Test: Emergency squawk 7700 ───────────────────────────────────────────────

class TestEmergencySquawk7700(unittest.TestCase):
    """test_emergency_squawk_7700 — squawk 7700 within scan radius → dispatch alert."""

    @patch("ingest.local_airspace.ULTRAFEEDER_AIRCRAFT_URL",
           "http://mock/data/aircraft.json")
    @patch("ingest.local_airspace.RECEIVER_LAT", 38.8816)
    @patch("ingest.local_airspace.RECEIVER_LON", -77.0910)
    @patch("ingest.local_airspace.SCAN_RADIUS_NM", 150.0)
    @patch("ingest.local_airspace._local_dedup_check", return_value=False)
    @patch("ingest.local_airspace._fire_ntfy")
    @patch("ingest.local_airspace.db.upsert_local_aircraft")
    @patch("ingest.local_airspace.db.insert_local_airspace_alert")
    @patch("ingest.local_airspace.get_active_entries", return_value=[])
    def test_squawk_7700_fires_dispatch(self, mock_wl, mock_alert, mock_upsert,
                                        mock_ntfy, mock_dedup):
        fixture = _load_fixture()
        # DAL500 squawks 7700 (hex e12345) in the fixture

        with patch("ingest.local_airspace.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = fixture
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            from ingest.local_airspace import _poll_ultrafeeder
            _poll_ultrafeeder()

        ntfy_calls = [(c.args[0], c.args[1] if len(c.args) > 1 else "")
                      for c in mock_ntfy.call_args_list if c.args]
        squawk_calls = [c for c in ntfy_calls
                        if "7700" in c[1] or "EMERGENCY" in c[1]]
        self.assertTrue(squawk_calls,
                        f"Expected squawk 7700 dispatch alert. Got: {ntfy_calls}")
        # Must be dispatch, not flight-alerts
        for topic, title in squawk_calls:
            self.assertEqual(topic, "dispatch")


# ── Test: ACARS OOOI parsing ──────────────────────────────────────────────────

class TestAcarsOooiParse(unittest.TestCase):
    """test_acars_oooi_parse — OOOI label classification and watchlist callsign match."""

    def _make_msg(self, label: str, flight: str, tail: str = "N12345",
                  msg_text: str = "") -> dict:
        return {
            "label": label, "flight": flight, "tail": tail,
            "freq": 130.025, "icao": "a4b2c1",
            "text": msg_text, "block_id": "A", "ack": "!", "mode": "2",
        }

    @patch("ingest.local_airspace.db.insert_acars_message")
    @patch("ingest.local_airspace.watchlist_event_hit")
    @patch("ingest.local_airspace.get_active_entries")
    def test_qo_label_classified_as_on(self, mock_entries, mock_hit, mock_insert):
        """Label QO → msg_type ON → fires watchlist_event_hit."""
        mock_entries.return_value = [{
            "id": "wl-flight-ual220-test",
            "entry_type": "flight",
            "identifier": "UAL220",
        }]
        msg = self._make_msg("QO", "UAL220", msg_text="ON EVENT")

        from ingest.local_airspace import _process_acars_message
        _process_acars_message(msg)

        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args.kwargs
        self.assertEqual(call_kwargs["msg_type"], "ON")
        self.assertEqual(call_kwargs["watchlist_hit"], 1)
        mock_hit.assert_called_once()

    @patch("ingest.local_airspace.db.insert_acars_message")
    @patch("ingest.local_airspace.watchlist_event_hit")
    @patch("ingest.local_airspace.get_active_entries")
    def test_h1_classified_as_pos_no_oooi_event(self, mock_entries, mock_hit, mock_insert):
        """Label H1 → msg_type POS, position report — watchlist match but lower priority."""
        mock_entries.return_value = [{
            "id": "wl-flight-ual220-test",
            "entry_type": "flight",
            "identifier": "UAL220",
        }]
        msg = self._make_msg("H1", "UAL220", msg_text="POSITION")

        from ingest.local_airspace import _process_acars_message
        _process_acars_message(msg)

        call_kwargs = mock_insert.call_args.kwargs
        self.assertEqual(call_kwargs["msg_type"], "POS")
        self.assertEqual(call_kwargs["watchlist_hit"], 1)

    @patch("ingest.local_airspace.db.insert_acars_message")
    @patch("ingest.local_airspace.watchlist_event_hit")
    @patch("ingest.local_airspace.get_active_entries")
    def test_unmatched_callsign_no_hit(self, mock_entries, mock_hit, mock_insert):
        """Callsign not in watchlist → watchlist_hit=0, no event fired."""
        mock_entries.return_value = [{
            "id": "wl-flight-ual220-test",
            "entry_type": "flight",
            "identifier": "UAL220",
        }]
        msg = self._make_msg("QO", "DAL999")

        from ingest.local_airspace import _process_acars_message
        _process_acars_message(msg)

        call_kwargs = mock_insert.call_args.kwargs
        self.assertEqual(call_kwargs["watchlist_hit"], 0)
        mock_hit.assert_not_called()


# ── Test: clean skip when ULTRAFEEDER_URL empty ───────────────────────────────

class TestUltrafeederUrlEmpty(unittest.TestCase):
    """test_ultrafeeder_url_empty — module skips cleanly when URL not configured."""

    def test_skips_cleanly(self):
        with patch("ingest.local_airspace.ULTRAFEEDER_AIRCRAFT_URL", ""):
            from ingest.local_airspace import _poll_ultrafeeder
            result = _poll_ultrafeeder()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
