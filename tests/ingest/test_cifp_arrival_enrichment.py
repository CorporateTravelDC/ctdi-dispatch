"""
Regression tests for the 2026-09-05 CIFP-derived arrival_time enrichment:
common.cifp_lookup.estimate_runway_eta(), common.db.get_tbfm_sequence_for_flight(),
and their two wire-up sites: ingest.parsers.fdps_parser.write_flight_event()
(fixes flight_events.arrival_time, previously hardcoded None on every
write -- see flight_resolver.py's own module comment) and
ingest.parsers.tbfm_parser._check_tbfm_watchlist_hits() (appends a
runway-arrival estimate to the watchlist notification text).

Uses the same real fixture as tests/poller/test_faa_cifp_parse.py
(KIAD's GIBBZ6 STAR + RW01L threshold, pulled byte-for-byte from the
real CIFP cycle 2609 file) plus IAD's real, confirmed SWANN meter fix
coordinates from tbfm_parser.DC_METER_FIXES.

IMPORTANT: db._db_path is a bare module attribute, not something
monkeypatch.setattr auto-tracks unless you go through monkeypatch itself
-- every test here uses the `cifp_db` fixture below (built on
monkeypatch.setattr(db, "_db_path", ...)) rather than reassigning
db._db_path directly, specifically so it's restored even on failure. An
earlier version of this file assigned it directly with a manual
try/finally that only cleaned up the temp file, not the monkeypatch --
that left db._db_path pointed at a deleted temp file for every
subsequent test in the same pytest session, causing real cross-test
failures when run alongside other files (confirmed live: passed in
isolation, failed when run with `-k tbfm` alongside other test files).
"""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import common.db as db
from common import cifp_lookup as L
from ingest.parsers.fdps_parser import write_flight_event
from ingest.parsers.tbfm_parser import DC_METER_FIXES, _check_tbfm_watchlist_hits
from poller.skills.faa_cifp_parse import _parse_lines

FIXTURE = Path(__file__).parent.parent / "poller" / "fixtures" / "cifp_kiad_gibbz6_sample.txt"


@pytest.fixture
def cifp_db(monkeypatch, tmp_path):
    """Isolated DB with the real CIFP fixture loaded. monkeypatch restores
    db._db_path automatically even if the test raises."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "_db_path", lambda: db_path)
    db.init_db_all()
    lines = FIXTURE.read_text().splitlines()
    fixes, legs, holds = _parse_lines(lines, "260903")
    db.cifp_replace_all("260903", fixes, legs, holds)
    yield db_path


def test_estimate_runway_eta_real_geometry(cifp_db):
    swann_lat, swann_lon = DC_METER_FIXES["SWANN"]
    eta_iso = "2026-09-05T18:00:00Z"
    est = L.estimate_runway_eta(swann_lat, swann_lon, "KIAD", eta_iso)
    assert est is not None
    assert est["distance_nm"] > 0
    # Sanity bound: SWANN is a real DC-area IAD arrival fix, not on
    # the other side of the country -- should be well under 200nm
    # from KIAD's own runway threshold.
    assert est["distance_nm"] < 200
    runway_dt = datetime.fromisoformat(est["runway_eta"].replace("Z", "+00:00"))
    eta_dt = datetime.fromisoformat(eta_iso.replace("Z", "+00:00"))
    assert runway_dt > eta_dt  # transit time only ever adds, never subtracts
    assert (runway_dt - eta_dt) < timedelta(hours=1)  # sane upper bound


def test_estimate_runway_eta_unknown_airport_returns_none(cifp_db):
    assert L.estimate_runway_eta(39.0, -76.0, "KZZZ", "2026-09-05T18:00:00Z") is None


def test_estimate_runway_eta_bad_timestamp_returns_none(cifp_db):
    assert L.estimate_runway_eta(39.15, -76.23, "KIAD", "not-a-timestamp") is None


def test_get_tbfm_sequence_for_flight_freshness_gate(cifp_db):
    db.upsert_tbfm_sequence("SWANN", "ZDC", "UAL123", "2026-09-05T18:00:00Z", 3, 250)
    fresh = db.get_tbfm_sequence_for_flight("UAL123")
    assert fresh is not None
    assert fresh["meter_fix"] == "SWANN"

    # Simulate staleness: last_seen far in the past.
    with db.conn() as c:
        c.execute("UPDATE tbfm_sequences SET last_seen=? WHERE flight_id=?",
                 ("2020-01-01T00:00:00Z", "UAL123"))
    stale = db.get_tbfm_sequence_for_flight("UAL123")
    assert stale is None

    assert db.get_tbfm_sequence_for_flight("NOTREAL999") is None


def _base_parsed(**overrides) -> dict:
    parsed = {
        "callsign": "UAL123",
        "gufi": "GUFI-TEST-001",
        "origin": "KORD",
        "destination": "KIAD",
        "aircraft_type": "B738",
        "latitude": 39.0,
        "longitude": -77.0,
        "altitude_ft": 12000,
        "ground_speed": 280,
        "raw_xml": "<test/>",
        "flight_status": "enroute",
    }
    parsed.update(overrides)
    return parsed


def test_write_flight_event_fills_arrival_time_with_active_tbfm_sequence(cifp_db):
    db.upsert_tbfm_sequence("SWANN", "ZDC", "UAL123", "2026-09-05T18:00:00Z", 3, 250,
                            eta_kind="mfx")
    ok = write_flight_event(_base_parsed())
    assert ok
    with db.conn() as c:
        r = c.execute("SELECT arrival_time FROM flight_events WHERE flight_id=?",
                     ("GUFI-TEST-001",)).fetchone()
    assert r is not None
    assert r["arrival_time"] is not None
    # arrival_time is REAL unix epoch per the schema, NOT an ISO string --
    # asserting the ISO shape here is what let a TEXT value ship to a column
    # whose only consumer does a numeric BETWEEN (see fdps_parser.py).
    assert isinstance(r["arrival_time"], (int, float)), (
        f"arrival_time must be a numeric unix epoch, got {type(r['arrival_time'])}"
    )
    arrival_dt = datetime.fromtimestamp(r["arrival_time"], tz=timezone.utc)
    assert arrival_dt > datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc)


def test_write_flight_event_fills_arrival_time_for_cifp_only_meter_fix(cifp_db):
    """REGRESSION (2026-09-05): the enrichment must resolve the meter fix
    against cifp_fixes, NOT only tbfm_parser.DC_METER_FIXES.

    The first version looked the fix up solely in that hardcoded 10-name
    dict, and every other test here used SWANN -- a DC_METER_FIXES key --
    so the suite passed green while production wrote 0 populated rows out
    of the 300 most recent flight_events. The dict matches NOTHING the live
    feed publishes: real tbfm_sequences meter_fix values are HVQ/KILMR/
    MULRR/CAPKO/BILIT/IGGGY/CAVLR/... , none of which are DC_METER_FIXES
    keys.

    HVQ is deliberately chosen: it is present in the real CIFP fixture, is
    a genuine live meter fix (observed in production traffic), and is NOT
    a DC_METER_FIXES key -- so this fails against the old code path and
    passes only when CIFP resolution is actually wired in.
    """
    assert "HVQ" not in DC_METER_FIXES, "test premise: HVQ must not be a DC_METER_FIXES key"
    assert L.resolve_fix("HVQ") is not None, "test premise: HVQ must resolve in the CIFP fixture"

    db.upsert_tbfm_sequence("HVQ", "ZDC", "RPA4752", "2026-09-05T18:00:00Z", 5, 250,
                            eta_kind="mfx")
    ok = write_flight_event(_base_parsed(callsign="RPA4752", gufi="GUFI-TEST-CIFP-1"))
    assert ok
    with db.conn() as c:
        r = c.execute("SELECT arrival_time FROM flight_events WHERE flight_id=?",
                      ("GUFI-TEST-CIFP-1",)).fetchone()
    assert r is not None
    assert r["arrival_time"] is not None, (
        "arrival_time not populated for a CIFP-resolvable meter fix -- the "
        "enrichment has regressed to the DC_METER_FIXES-only lookup"
    )
    # arrival_time is REAL unix epoch per the schema, NOT an ISO string --
    # asserting the ISO shape here is what let a TEXT value ship to a column
    # whose only consumer does a numeric BETWEEN (see fdps_parser.py).
    assert isinstance(r["arrival_time"], (int, float)), (
        f"arrival_time must be a numeric unix epoch, got {type(r['arrival_time'])}"
    )
    arrival_dt = datetime.fromtimestamp(r["arrival_time"], tz=timezone.utc)
    assert arrival_dt > datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc)


def test_write_flight_event_skips_pseudo_label_and_uses_resolvable_sequence(cifp_db):
    """REGRESSION (2026-09-05, second half of the same production bug): a
    flight carries MANY concurrent TBFM sequence rows, and the FRESHEST is
    usually a pseudo-label.

    db.get_tbfm_sequence_for_flight() returns only the newest row. In
    production UAL1635 had 42 fresh sequences and the newest was 'ZDC' --
    a facility pseudo-label with no coordinates -- so even after CIFP
    resolution was wired in, the enrichment STILL produced 0 rows. The
    parser must walk all fresh sequences newest-first and take the first
    one that actually resolves.

    Here HVQ (resolvable, in the fixture) is written FIRST and the
    pseudo-label DC_MET second, so DC_MET is the freshest -- the exact
    production shape. Fails against a take-the-newest-only implementation.
    """
    db.upsert_tbfm_sequence("HVQ", "ZDC", "UAL1635", "2026-09-05T18:00:00Z", 5, 250,
                            eta_kind="mfx")
    db.upsert_tbfm_sequence("DC_MET", "ZDC", "UAL1635", "2026-09-05T18:05:00Z", 6, 250,
                            eta_kind="mfx")

    # upsert_tbfm_sequence stamps last_seen with utcnow(), so both rows land
    # in the same second and the ordering would be arbitrary. Force the
    # production shape explicitly: pseudo-label strictly newer than the real
    # fix, both still inside the 1800s freshness window.
    now = datetime.now(timezone.utc)
    with db.conn() as c:
        c.execute("UPDATE tbfm_sequences SET last_seen=? WHERE flight_id=? AND meter_fix=?",
                  ((now - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "UAL1635", "HVQ"))
        c.execute("UPDATE tbfm_sequences SET last_seen=? WHERE flight_id=? AND meter_fix=?",
                  ((now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "UAL1635", "DC_MET"))

    seqs = db.get_tbfm_sequences_for_flight("UAL1635")
    assert len(seqs) == 2, "test premise: both sequences must be fresh"
    assert seqs[0]["meter_fix"] == "DC_MET", (
        "test premise: the unresolvable pseudo-label must be the freshest row"
    )

    ok = write_flight_event(_base_parsed(callsign="UAL1635", gufi="GUFI-TEST-CIFP-3"))
    assert ok
    with db.conn() as c:
        r = c.execute("SELECT arrival_time FROM flight_events WHERE flight_id=?",
                      ("GUFI-TEST-CIFP-3",)).fetchone()
    assert r["arrival_time"] is not None, (
        "arrival_time not populated -- the enrichment stopped at the freshest "
        "(pseudo-label) sequence instead of walking on to the resolvable one"
    )


def test_write_flight_event_arrival_time_none_for_pseudo_label_meter_fix(cifp_db):
    """TBFM also publishes facility/meter-point pseudo-labels (ZDC, DC_MET,
    IAD_MP, HVQ_BKW, J48_J75, ZID-E, ...) that are not fixes and have no
    coordinates. These must resolve to None, never a fabricated position."""
    db.upsert_tbfm_sequence("DC_MET", "ZDC", "AAL9001", "2026-09-05T18:00:00Z", 2, 250,
                            eta_kind="mfx")
    ok = write_flight_event(_base_parsed(callsign="AAL9001", gufi="GUFI-TEST-CIFP-2"))
    assert ok
    with db.conn() as c:
        r = c.execute("SELECT arrival_time FROM flight_events WHERE flight_id=?",
                      ("GUFI-TEST-CIFP-2",)).fetchone()
    assert r["arrival_time"] is None


def test_write_flight_event_arrival_time_none_without_tbfm_sequence(cifp_db):
    # No upsert_tbfm_sequence call -- no active sequence for this flight.
    ok = write_flight_event(_base_parsed(callsign="DAL456", gufi="GUFI-TEST-002"))
    assert ok
    with db.conn() as c:
        r = c.execute("SELECT arrival_time FROM flight_events WHERE flight_id=?",
                     ("GUFI-TEST-002",)).fetchone()
    assert r is not None
    assert r["arrival_time"] is None


def test_write_flight_event_arrival_time_none_for_unresolved_meter_fix(cifp_db):
    """LUCIT is a real DC_METER_FIXES key with a confirmed-unresolvable
    coordinate (None) -- must never fabricate a position for it."""
    db.upsert_tbfm_sequence("LUCIT", "ZDC", "SWA789", "2026-09-05T18:00:00Z", 1, 240,
                            eta_kind="mfx")
    ok = write_flight_event(_base_parsed(callsign="SWA789", gufi="GUFI-TEST-003"))
    assert ok
    with db.conn() as c:
        r = c.execute("SELECT arrival_time FROM flight_events WHERE flight_id=?",
                     ("GUFI-TEST-003",)).fetchone()
    assert r["arrival_time"] is None


def test_write_flight_event_arrival_time_none_for_non_dc_destination(cifp_db):
    db.upsert_tbfm_sequence("SWANN", "ZDC", "JBU321", "2026-09-05T18:00:00Z", 2, 250)
    ok = write_flight_event(_base_parsed(
        callsign="JBU321", gufi="GUFI-TEST-004", destination="KJFK",
        latitude=40.6, longitude=-73.7,
    ))
    assert ok
    with db.conn() as c:
        r = c.execute("SELECT arrival_time FROM flight_events WHERE flight_id=?",
                     ("GUFI-TEST-004",)).fetchone()
    assert r["arrival_time"] is None


def test_tbfm_watchlist_status_includes_runway_eta_estimate(cifp_db, monkeypatch):
    captured = {}

    def fake_match(flight_id):
        return {"id": "watch-entry-1"}

    def fake_update_status(entry_id, status, now_iso):
        captured["status"] = status

    def fake_watchlist_event_hit(entry_id, summary, detail, priority=3):
        captured["summary"] = summary

    monkeypatch.setattr("ingest.parsers.tbfm_parser._match_watchlist_flight", fake_match)
    monkeypatch.setattr(db, "update_watchlist_tbfm_status", fake_update_status)
    import shared.watchlist as wl
    monkeypatch.setattr(wl, "watchlist_event_hit", fake_watchlist_event_hit)

    sequences = [{
        "flight_id": "UAL123", "meter_fix": "SWANN", "eta": "2026-09-05T18:00:00Z",
        "apt": "IAD", "facility": "ZDC", "sequence_num": 3, "assigned_speed": 250,
    }]
    _check_tbfm_watchlist_hits(sequences)

    assert "status" in captured
    assert "est. runway arrival" in captured["status"]


def test_tbfm_watchlist_status_omits_estimate_for_unresolved_fix(cifp_db, monkeypatch):
    captured = {}
    monkeypatch.setattr("ingest.parsers.tbfm_parser._match_watchlist_flight",
                        lambda fid: {"id": "watch-entry-2"})
    monkeypatch.setattr(db, "update_watchlist_tbfm_status",
                        lambda *a, **k: captured.update(status=a[1]))
    import shared.watchlist as wl
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda *a, **k: None)

    sequences = [{
        "flight_id": "SWA789", "meter_fix": "LUCIT", "eta": "2026-09-05T18:00:00Z",
        "apt": "IAD", "facility": "ZDC", "sequence_num": 1, "assigned_speed": 240,
    }]
    _check_tbfm_watchlist_hits(sequences)

    assert "status" in captured
    assert "est. runway arrival" not in captured["status"]
    assert "ETA" in captured["status"]  # the meter-fix ETA itself is still present


# ── eta_kind arc handling (v46, 2026-09-05) ──────────────────────────────────
# TBFM's eta is whichever arc the message carried. These are ETAs to
# genuinely different points, and the previous code treated them all as
# meter-fix ETAs -- double-counting the fix->runway leg for "rwy" rows and
# measuring from the wrong point entirely for "dfx"/"sfx".

def test_rwy_arc_used_verbatim_no_transit_added(cifp_db):
    """A "rwy" row is already TBFM's own runway-threshold ETA. It must be
    used AS-IS -- adding CIFP fix->runway transit would count that leg
    twice and push the estimate late."""
    eta_iso = "2026-09-05T18:00:00Z"
    db.upsert_tbfm_sequence("HVQ", "ZDC", "AAL2200", eta_iso, 1, 250,
                            eta_kind="rwy")
    epoch = L.runway_eta_epoch("AAL2200", "KIAD")
    assert epoch is not None
    expected = datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc).timestamp()
    assert epoch == expected, "rwy ETA must pass through unmodified"


def test_mfx_arc_adds_transit_and_is_later_than_rwy(cifp_db):
    """Same fix, same timestamp, different arc: "mfx" must come out strictly
    later than "rwy", because the fix->runway leg still has to be flown."""
    eta_iso = "2026-09-05T18:00:00Z"
    db.upsert_tbfm_sequence("HVQ", "ZDC", "AAL2201", eta_iso, 1, 250,
                            eta_kind="rwy")
    db.upsert_tbfm_sequence("HVQ", "ZDC", "AAL2202", eta_iso, 1, 250,
                            eta_kind="mfx")
    rwy = L.runway_eta_epoch("AAL2201", "KIAD")
    mfx = L.runway_eta_epoch("AAL2202", "KIAD")
    assert rwy is not None and mfx is not None
    assert mfx > rwy, "mfx must add fix->runway transit; rwy must not"


@pytest.mark.parametrize("kind", ["dfx", "sfx"])
def test_descent_and_speed_fix_arcs_are_skipped(cifp_db, kind):
    """dfx/sfx are ETAs to a descent/speed fix -- a different point than
    meter_fix -- so transit measured from the meter fix would be wrong.
    Skip rather than emit a confidently wrong number."""
    db.upsert_tbfm_sequence("HVQ", "ZDC", f"AAL23{kind}", "2026-09-05T18:00:00Z",
                            1, 250, eta_kind=kind)
    assert L.runway_eta_epoch(f"AAL23{kind}", "KIAD") is None


def test_unknown_arc_is_skipped_never_assumed_mfx(cifp_db):
    """Pre-v46 rows have eta_kind NULL. Unknown must mean SKIP, never a
    silent assumption of "mfx" -- that assumption is the original bug."""
    db.upsert_tbfm_sequence("HVQ", "ZDC", "AAL2204", "2026-09-05T18:00:00Z", 1, 250)
    assert L.runway_eta_epoch("AAL2204", "KIAD") is None


def test_runway_eta_epoch_returns_epoch_not_iso(cifp_db):
    """The column is REAL unix epoch; returning an ISO string here is what
    made the consumer's numeric BETWEEN silently match nothing."""
    db.upsert_tbfm_sequence("HVQ", "ZDC", "AAL2205", "2026-09-05T18:00:00Z", 1, 250,
                            eta_kind="mfx")
    epoch = L.runway_eta_epoch("AAL2205", "KIAD")
    assert isinstance(epoch, float), f"expected float epoch, got {type(epoch)}"
