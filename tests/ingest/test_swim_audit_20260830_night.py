"""
Tests for the 2026-08-30 NIGHT pass: the diversion-continuation detector
(fdps_parser._check_diversion_continuation + db_swim v44), the new
`drone` watchlist entry type with its collapsed launched/landed UAS phase
columns (db.py v43), and the utm_watcher's defensive OpenDroneID field
extraction.

Same discipline as the morning/afternoon/evening files: all DB assertions
run against an isolated temp DB (never /var/lib), and anything that could
fire a push is patched. The continuation tests use synthetic rows rather
than captured XML because fdps_destination_changes has ZERO real
diversions on record (its entire first live day was the spelling-flap
noise the evening pass normalized away), so no real capture can exercise
this path yet.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


def _isolated_db():
    import common.db as db
    from common import db_swim
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    db._db_path = lambda: Path(tmp.name)
    db.init_db_all()  # introspection picks up the new init_db_v43 too
    db_swim.init_db_swim_v41()
    db_swim.init_db_swim_v42()
    db_swim.init_db_swim_v44()
    db_swim.init_db_swim_v45()  # late pass: operator_class column
    return orig, tmp.name


def _restore_db(orig, tmp_name):
    import common.db as db
    db._db_path = orig
    Path(tmp_name).unlink(missing_ok=True)


def _seed_diverted_flight(flight_id: str, callsign: str, origin: str,
                          old_dest: str, new_dest: str,
                          registration: str | None = None):
    """One diverted flight: a flight_events row plus its B->C
    destination-change observation, exactly the state the live parser
    leaves behind after a real diversion filing."""
    import common.db as db
    from common import db_swim
    db.upsert_flight_event(
        flight_id=flight_id, airline=callsign[:3], flight_num=callsign[3:],
        origin=origin, destination=new_dest, aircraft_type=None,
        departure_time=None, arrival_time=None, status=None,
        position_lat=None, position_lon=None, altitude_ft=None,
        ground_speed_kt=None, raw_json="")
    if registration:
        db_swim.update_flight_event_extras(
            flight_id=flight_id, squawk=None, registration=registration,
            controlling_facility=None)
    db_swim.insert_fdps_destination_change(
        flight_id=flight_id, callsign=callsign, origin=origin,
        old_destination=old_dest, new_destination=new_dest, source="FH")


def _rows(sql: str):
    from common.db import conn
    with conn() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


# ── Diversion-continuation detector ──────────────────────────────────────────

def test_continuation_pair_same_callsign_fires_once():
    """UAL123 KIAD->KBOS diverts to KBDL; UAL123 later files KBDL->KBOS
    under a new GUFI. Exactly one pair row, exactly one alert, and the
    identical re-check (an FDPS rebroadcast of the filing) stays quiet
    via the UNIQUE-insert gate."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-DIV1", "UAL123", "KIAD", "KBOS", "KBDL")
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append((a, k))):
            parsed = {"origin": "KBDL", "destination": "KBOS"}
            fdps_parser._check_diversion_continuation(parsed, "GUFI-CONT1", "UAL123")
            fdps_parser._check_diversion_continuation(parsed, "GUFI-CONT1", "UAL123")
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        r = rows[0]
        assert r["diverted_flight_id"] == "GUFI-DIV1"
        assert r["continuation_flight_id"] == "GUFI-CONT1"
        assert r["match_basis"] == "callsign"
        assert r["confidence"] == "fdps"        # no ACARS row -> FDPS alone is valid
        assert r["acars_msg_id"] is None
        assert r["original_destination"] == "BOS"   # normalized spellings stored
        assert r["diversion_airport"] == "BDL"
        assert len(fired) == 1
        args, kwargs = fired[0]
        assert args[1] == "fdps_diversion_continuation"
        assert kwargs.get("isolate") is True
    finally:
        _restore_db(orig, tmp)


def test_continuation_pair_matched_by_registration():
    """The recovery leg files under a DIFFERENT callsign -- matched via
    flight_events' COALESCE-kept registration instead (no continuation-
    naming convention exists in this repo to match on)."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-DIV2", "AAL861", "KMIA", "KDCA", "KRIC",
                              registration="N123AA")
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append(a)):
            parsed = {"origin": "KRIC", "destination": "KDCA",
                      "registration": "N-123AA"}   # dash-insensitive
            fdps_parser._check_diversion_continuation(parsed, "GUFI-CONT2", "AAL9861")
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        assert rows[0]["match_basis"] == "registration"
        assert rows[0]["continuation_callsign"] == "AAL9861"
        assert len(fired) == 1
    finally:
        _restore_db(orig, tmp)


def test_continuation_requires_route_and_relationship_match():
    """No pair for: (a) an unrelated flight on the same route, (b) a
    related flight on a different route, (c) the diverted GUFI itself
    rebroadcasting."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-DIV3", "JBU456", "KBOS", "KEWR", "KPHL")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            # (a) right route shape, unrelated callsign, no registration
            fdps_parser._check_diversion_continuation(
                {"origin": "KPHL", "destination": "KEWR"}, "GUFI-X1", "DAL999")
            # (b) same callsign, wrong route
            fdps_parser._check_diversion_continuation(
                {"origin": "KPHL", "destination": "KJFK"}, "GUFI-X2", "JBU456")
            # (c) same GUFI as the diverted leg
            fdps_parser._check_diversion_continuation(
                {"origin": "KPHL", "destination": "KEWR"}, "GUFI-DIV3", "JBU456")
        assert _rows("SELECT * FROM fdps_diversion_continuations") == []
        assert not fire.called
    finally:
        _restore_db(orig, tmp)


def test_continuation_spelling_flap_rows_never_pair():
    """A pre-normalization-era flap row (old=MFR new=KMFR, same airport)
    can never produce a pair -- the normalized old==new guard skips it."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers import fdps_parser
        _seed_diverted_flight("GUFI-DIV4", "N863WA", "KSJC", "MFR", "KMFR")
        with patch("shared.sector_coalesce.fire_family_alert") as fire:
            fdps_parser._check_diversion_continuation(
                {"origin": "KMFR", "destination": "KMFR"}, "GUFI-X4", "N863WA")
        assert _rows("SELECT * FROM fdps_diversion_continuations") == []
        assert not fire.called
    finally:
        _restore_db(orig, tmp)


def test_continuation_acars_corroboration_is_bonus_not_gate():
    """With a route-consistent acars_messages row from the same tail in
    the window, the pair records confidence='fdps+acars' and the message
    id -- but the same pair without it (previous tests) already fired at
    'fdps', proving ACARS is never required."""
    orig, tmp = _isolated_db()
    try:
        import common.db as db
        from ingest.parsers import fdps_parser
        from datetime import datetime, timezone
        _seed_diverted_flight("GUFI-DIV5", "UAL777", "KIAD", "KORD", "KMKE",
                              registration="N26902")
        db.insert_acars_message(
            received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            freq_mhz=131.550, icao_hex=None, tail="N26902", flight="UA777",
            msg_type=None, label="H1", block_id=None, ack=None, mode=None,
            msg_text="DIVERT KMKE FUEL OK WILL CONTINUE ORD WHEN RELEASED",
            raw=None)
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append(a)):
            fdps_parser._check_diversion_continuation(
                {"origin": "KMKE", "destination": "KORD",
                 "registration": "N26902"}, "GUFI-CONT5", "UAL777")
        rows = _rows("SELECT * FROM fdps_diversion_continuations")
        assert len(rows) == 1
        assert rows[0]["confidence"] == "fdps+acars"
        assert rows[0]["acars_msg_id"] is not None
        assert len(fired) == 1
        assert "+ACARS corroborated" in fired[0][4]  # detail text
    finally:
        _restore_db(orig, tmp)


# ── drone entry type + collapsed UAS phase ───────────────────────────────────

def test_drone_file_map_and_entry_type():
    from shared.watchlist import WatchlistFileWatcher, EntryType
    import typing
    assert WatchlistFileWatcher._FILE_MAP["permanent_drones.json"] == "drone"
    assert "drone" in typing.get_args(EntryType)
    # the permanent file itself exists in the repo watchlists dir
    repo_file = Path(__file__).parent.parent.parent / "watchlists" / "permanent_drones.json"
    assert repo_file.exists()


def _seed_entry(entry_id: str, entry_type: str, identifier: str):
    from common.db import conn
    with conn() as c:
        c.execute(
            "INSERT INTO watchlist_entries (id, entry_type, tier, identifier,"
            " added_at, added_by) VALUES (?, ?, 'permanent', ?, '2026-08-30T00:00:00Z', 'test')",
            (entry_id, entry_type, identifier))


def test_uas_phase_two_phase_cycle_and_guards():
    """The collapsed drone phase machine: launched/landed alternate freely
    (relaunch is a new sortie, not a regression), same-phase re-reports
    are rejected, unknown phases are rejected, and non-drone entries can
    never be written through this path (their 5-phase OOOI machine is
    untouched)."""
    orig, tmp = _isolated_db()
    try:
        import common.db as db
        _seed_entry("wl-drone-1", "drone", "1596F0000000000000AA")
        _seed_entry("wl-flight-1", "flight", "UAL123")
        ts = "2026-08-30T22:00:00Z"
        assert db.update_watchlist_uas_phase("wl-drone-1", "launched", "local_rid", ts)
        assert not db.update_watchlist_uas_phase("wl-drone-1", "launched", "local_rid", ts)
        assert db.update_watchlist_uas_phase("wl-drone-1", "landed", "local_rid", ts)
        assert db.update_watchlist_uas_phase("wl-drone-1", "launched", "local_rid", ts)  # relaunch OK
        assert not db.update_watchlist_uas_phase("wl-drone-1", "out", "local_rid", ts)   # OOOI vocab rejected
        assert not db.update_watchlist_uas_phase("wl-flight-1", "launched", "local_rid", ts)  # drone-only
        rows = _rows("SELECT uas_phase, uas_phase_source FROM watchlist_entries WHERE id='wl-drone-1'")
        assert rows[0]["uas_phase"] == "launched"
        assert rows[0]["uas_phase_source"] == "local_rid"
        # flight row untouched
        rows = _rows("SELECT uas_phase, oooi_phase FROM watchlist_entries WHERE id='wl-flight-1'")
        assert rows[0]["uas_phase"] is None
    finally:
        _restore_db(orig, tmp)


# ── utm_watcher defensive parsing ────────────────────────────────────────────

def test_utm_watcher_extracts_known_likely_shapes():
    from utm_watcher import utm_watcher as uw
    # flat serial-number shape
    assert uw.extract_uas_id({"serial_number": "1596F0000000000000AA"}) == "1596F0000000000000AA"
    # nested basic_id container shape
    assert uw.extract_uas_id({"basic_id": {"uas_id": "SESS-42"}}) == "SESS-42"
    # generic id key
    assert uw.extract_uas_id({"id": "ABC123", "lat": 38.88}) == "ABC123"
    # normalization is dash/case-insensitive
    assert uw.normalize_uas_id("sess-42") == uw.normalize_uas_id("SESS42")


def test_utm_watcher_unrecognized_shapes_do_not_crash():
    from utm_watcher import utm_watcher as uw
    for weird in ({}, {"foo": "bar"}, {"id": {"nested": "dict"}},
                  {"basic_id": "not-a-dict-but-a-string"},
                  {"lat": "not-a-float"}):
        got = uw.extract_uas_id(weird)
        assert isinstance(got, str)
    # basic_id as a plain string is id-bearing via the flat key pass
    assert uw.extract_uas_id({"basic_id": "RAWSTRING"}) == "RAWSTRING"
    # payload builder never raises on junk values
    p = uw.build_ntfy_payload("X1", {"lat": "junk", "operator_id": {"a": 1}}, "LOCAL")
    assert p["title"].startswith("UAS: X1")
