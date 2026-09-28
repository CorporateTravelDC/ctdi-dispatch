"""
Tests for the 2026-08-30 EVENING pass (SWIM-audit backlog items 2 & 3 on
top of the morning + afternoon passes): per-watched-flight REROUTE scope
matching, the FADT EDCT watchlist hit's rebroadcast behavior, the
destination-change spelling-flap normalization fix, and the
alternate-saturation detector.

Same discipline as test_swim_audit_20260830.py / _pm.py: every XML
fixture is an UNMODIFIED real capture from this box
(tfms_debug_unknown_msgtype/), each test names its sample, and all DB
assertions run against an isolated temp DB -- never /var/lib. Every test
that touches a PushDedup points DISPATCH_STATE_DIR at tmp_path so dedup
state is per-test, mirroring how the live containers share
/var/lib/corporatetraveldc.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

FIXTURES = Path(__file__).parent / "fixtures" / "swim_audit"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _isolated_db():
    import common.db as db
    from common import db_swim
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    db._db_path = lambda: Path(tmp.name)
    db.init_db_all()
    db_swim.init_db_swim_v41()
    db_swim.init_db_swim_v42()
    return orig, tmp.name


def _restore_db(orig, tmp_name):
    import common.db as db
    db._db_path = orig
    Path(tmp_name).unlink(missing_ok=True)


def _quiet_tfms(monkeypatch, tmp_path):
    from ingest.parsers import tfms_parser
    monkeypatch.setattr(tfms_parser, "_DEBUG_SAMPLE_DIR", str(tmp_path / "tfms"),
                        raising=False)
    monkeypatch.setattr(tfms_parser, "_UNKNOWN_MSGTYPE_DIR",
                        str(tmp_path / "tfms_unk"), raising=False)


# ── REROUTE -> watchlist scope matching ──────────────────────────────────────

def test_reroute_watchlist_hit_fires_and_identical_rebroadcast_stays_quiet(
        monkeypatch, tmp_path):
    """REROUTE_1.xml: real SERBOS_1_PARTIAL advisory. Segment 2 scopes
    KBOS/KMHT/KBED -> KEWR onto 'BOSOX T303 HFD V3 CMK V623 SAX'. A
    watched KBOS->KEWR flight must get exactly one tfms_reroute hit, and
    the byte-identical rebroadcast must stay quiet (6 h dedup window,
    content-keyed on route/status/times)."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    _quiet_tfms(monkeypatch, tmp_path)
    from ingest.parsers.tfms_parser import parse_tfms_message
    entry = {"id": "wl-eve-1", "identifier": "JBU123",
             "origin": "KBOS", "destination": "KEWR"}
    hits = []
    with patch("common.db_swim.upsert_tfms_reroute"), \
         patch("shared.watchlist.get_active_entries", return_value=[entry]), \
         patch("shared.watchlist.watchlist_event_hit",
               side_effect=lambda *a, **k: hits.append((a, k))):
        assert parse_tfms_message(_load("REROUTE_1.xml")) == []
        assert parse_tfms_message(_load("REROUTE_1.xml")) == []  # rebroadcast
    assert len(hits) == 1
    (entry_id, summary, detail), kw = hits[0]
    assert entry_id == "wl-eve-1"
    assert detail["watchlist_trigger"] == "tfms_reroute"
    assert detail["reroute_id"] == "rr.dccops.lxstn29.20260830145218"
    assert detail["required_route"].startswith("BOSOX T303")
    assert "SERBOS_1_PARTIAL" in summary
    assert kw.get("priority") == 3


def test_reroute_watchlist_no_hit_outside_scope(monkeypatch, tmp_path):
    """REROUTE_1.xml again: a watched KDCA->KEWR flight is NOT in any
    segment's origin scope (origins are KACK/KBOS/KMHT/KBED/KMVY plus
    centers) and must not match."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    _quiet_tfms(monkeypatch, tmp_path)
    from ingest.parsers.tfms_parser import parse_tfms_message
    entry = {"id": "wl-eve-2", "identifier": "AAL42",
             "origin": "KDCA", "destination": "KEWR"}
    hits = []
    with patch("common.db_swim.upsert_tfms_reroute"), \
         patch("shared.watchlist.get_active_entries", return_value=[entry]), \
         patch("shared.watchlist.watchlist_event_hit",
               side_effect=lambda *a, **k: hits.append((a, k))):
        parse_tfms_message(_load("REROUTE_1.xml"))
    assert hits == []


def test_reroute_center_only_scope_is_a_deliberate_false_negative(
        monkeypatch, tmp_path):
    """REROUTE_13.xml (JFK_THRU_ZDC_RRTE_MONITOR): every segment's origin
    scope is centers only (UNKN/ZID/ZDC/ZNY/ZOB -- some inside <airport>
    tags). A KCVG->KJFK flight is genuinely inside the advisory's real
    scope (ZID departure), but with no airport->center table the matcher
    must SKIP it rather than guess -- documented false negative."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    _quiet_tfms(monkeypatch, tmp_path)
    from ingest.parsers.tfms_parser import parse_tfms_message
    entry = {"id": "wl-eve-3", "identifier": "DAL99",
             "origin": "KCVG", "destination": "KJFK"}
    hits = []
    with patch("common.db_swim.upsert_tfms_reroute"), \
         patch("shared.watchlist.get_active_entries", return_value=[entry]), \
         patch("shared.watchlist.watchlist_event_hit",
               side_effect=lambda *a, **k: hits.append((a, k))):
        parse_tfms_message(_load("REROUTE_13.xml"))
    assert hits == []


# ── FADT EDCT watchlist hit: rebroadcast behavior ────────────────────────────

def test_fadt_edct_watchlist_hit_once_per_unchanged_slot_list(
        monkeypatch, tmp_path):
    """FADT_0.xml: real EWR ground-stop slot list carrying UAL8115
    (IAD->EWR, EDCT 030516). A watched UAL8115 gets exactly one
    tfms_edct hit; the byte-identical rebroadcast stays quiet inside the
    dedup window. (The hit itself shipped in the morning pass -- this
    pins the new-or-revised-only contract the backlog item asks for.)"""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    _quiet_tfms(monkeypatch, tmp_path)
    from ingest.parsers.tfms_parser import parse_tfms_message
    entry = {"id": "wl-eve-4", "identifier": "UAL8115"}
    hits = []
    with patch("common.db_swim.upsert_tfms_edct_slot"), \
         patch("shared.watchlist.get_active_entries", return_value=[entry]), \
         patch("shared.watchlist.watchlist_event_hit",
               side_effect=lambda *a, **k: hits.append((a, k))):
        parse_tfms_message(_load("FADT_0.xml"))
        parse_tfms_message(_load("FADT_0.xml"))  # identical rebroadcast
    assert len(hits) == 1
    (entry_id, summary, detail), kw = hits[0]
    assert entry_id == "wl-eve-4"
    assert detail["watchlist_trigger"] == "tfms_edct"
    assert detail["edct"] == "2026-08-03T05:16:00Z"


# ── Destination-change spelling-flap normalization ───────────────────────────

def test_destination_spelling_flap_is_not_a_change_but_real_change_is(
        monkeypatch, tmp_path):
    """Reproduces the live 2026-08-30 finding: the detector's entire
    first-day output (7/7 rows) was FAA-vs-ICAO spelling flapping of one
    airport (KMFR->MFR->KMFR etc.), zero diversions. KMFR->MFR must now
    be silent; KMFR->KBOI must still log one change."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    import common.db as db
    from ingest.parsers import fdps_parser
    orig, tmp_name = _isolated_db()
    try:
        def parsed(dest, source):
            return {"gufi": "g-flap-1", "callsign": "N863WA",
                    "origin": "KSJC", "destination": dest, "source": source}
        assert fdps_parser.write_flight_event(parsed("KMFR", "FH"))
        assert fdps_parser.write_flight_event(parsed("MFR", "TH"))   # flap
        assert fdps_parser.write_flight_event(parsed("KMFR", "TH"))  # flap back
        with db.conn() as c:
            assert c.execute(
                "SELECT COUNT(*) AS n FROM fdps_destination_changes"
            ).fetchone()["n"] == 0
        assert fdps_parser.write_flight_event(parsed("KBOI", "FH"))  # real
        with db.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM fdps_destination_changes").fetchall()]
        assert len(rows) == 1
        assert rows[0]["old_destination"] == "KMFR"
        assert rows[0]["new_destination"] == "KBOI"
        assert rows[0]["flight_id"] == "g-flap-1"
    finally:
        _restore_db(orig, tmp_name)


# ── Alternate-saturation detector ────────────────────────────────────────────

def test_alternate_saturation_fires_at_threshold_across_spellings(
        monkeypatch, tmp_path):
    """Three distinct flights re-filing to the same alternate inside the
    window -- spelled BWI and KBWI interchangeably, as the live feed
    really does -- must fire exactly one aggregate alert; re-checking the
    unchanged flight set stays quiet; a FOURTH convergent flight re-fires
    (growth is the signal, content-keyed on the flight set)."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    from common import db_swim
    from ingest.parsers import fdps_parser
    orig, tmp_name = _isolated_db()
    try:
        for fid, org, new in (("f1", "KJFK", "KBWI"), ("f2", "KBOS", "BWI"),
                              ("f3", "KLGA", "KBWI")):
            db_swim.insert_fdps_destination_change(
                flight_id=fid, callsign=fid.upper(), origin=org,
                old_destination="KDCA", new_destination=new, source="FH")
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append((a, k))):
            fdps_parser._check_alternate_saturation("KBWI")
            fdps_parser._check_alternate_saturation("KBWI")  # unchanged set
        assert len(fired) == 1
        (family, feed_name, facility, title, detail, dispatch), kw = fired[0]
        assert family == "fdps" and feed_name == "fdps_alt_saturation"
        assert "3 flights" in title
        assert kw.get("escalating_only") is False and kw.get("isolate") is True

        db_swim.insert_fdps_destination_change(
            flight_id="f4", callsign="F4", origin="KPHL",
            old_destination="KDCA", new_destination="KBWI", source="FH")
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append((a, k))):
            fdps_parser._check_alternate_saturation("KBWI")
        assert len(fired) == 2
        assert "4 flights" in fired[1][0][3]
    finally:
        _restore_db(orig, tmp_name)


def test_alternate_saturation_excludes_return_to_origin_rows(
        monkeypatch, tmp_path):
    """Rows whose origin == new destination (normalized) are
    positioning/return-to-field shapes, not alternate convergence -- two
    real convergers plus one BWI->...->BWI return must NOT reach the
    3-flight threshold."""
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    from common import db_swim
    from ingest.parsers import fdps_parser
    orig, tmp_name = _isolated_db()
    try:
        for fid, org, new in (("g1", "KJFK", "KBWI"), ("g2", "KBOS", "KBWI"),
                              ("g3", "BWI", "KBWI")):   # g3: return-to-field
            db_swim.insert_fdps_destination_change(
                flight_id=fid, callsign=fid.upper(), origin=org,
                old_destination="KDCA", new_destination=new, source="FH")
        fired = []
        with patch("shared.sector_coalesce.fire_family_alert",
                   side_effect=lambda *a, **k: fired.append((a, k))):
            fdps_parser._check_alternate_saturation("KBWI")
        assert fired == []
    finally:
        _restore_db(orig, tmp_name)


def test_norm_airport_helpers_agree():
    """The two file-local normalizers (fdps_parser._norm_airport,
    tfms_parser._norm_apt_token) implement the same contract -- pin it so
    they can't drift apart silently."""
    from ingest.parsers.fdps_parser import _norm_airport
    from ingest.parsers.tfms_parser import _norm_apt_token
    for raw in ("KJFK", "JFK", "kbwi", "E25", "KE25", "EGLL", None, "", " KDCA "):
        assert _norm_airport(raw) == _norm_apt_token(raw)
    assert _norm_airport("KE25") == "E25" == _norm_airport("E25")
    assert _norm_airport("EGLL") == "EGLL"
