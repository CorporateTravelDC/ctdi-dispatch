"""
Tests for the 2026-08-30 LATE-NIGHT pass: Detector C (plan-removal /
cancellation classification via fdTrigger, activity evidence, settle-
window confirmation) and Detector D groundwork (FDPS nasRouteText
extraction incl. the previously-dropped HU source, the incremental
distinct-route-version table, and the genuine-reroute-vs-noise
classifier).

Same discipline as the earlier 2026-08-30 files: isolated temp DB,
real captured messages wherever they exist -- the 12 real plan removals
(byte-exact snapshots of tfms_debug_unknown_msgtype/
flightPlanCancellation_*.xml under fixtures/swim_audit/
tfms_plan_removals/: FD_FLIGHT_CANCEL_MSG x8, HCS_CANCELLATION_MSG x4),
the fully-real fixtures/fdps_fixm30_real/AH_sample_9.xml route capture,
and the JIA5230 filed->activated re-expression pair (fixtures/
swim_audit/fdps_{hu_route_filed,ah_route_activated}.xml -- real captured
field values; the original envelope files were lost to the test-process
capture-writer bug conftest.py now fixes, see those fixtures' own
headers). Synthetic XML is used ONLY for shapes with no real capture
(UPDATE_CANCEL_TIMEOUT removals, post-removal activity messages),
clearly labeled below.
"""
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

_FIXTURES = Path(__file__).parent / "fixtures"
_CAPTURE_DIR = _FIXTURES / "swim_audit" / "tfms_plan_removals"


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
    db_swim.init_db_swim_v44()
    db_swim.init_db_swim_v45()
    db_swim.init_db_swim_v46()
    return orig, tmp.name


def _restore_db(orig, tmp_name):
    import common.db as db
    db._db_path = orig
    Path(tmp_name).unlink(missing_ok=True)


def _quiet_tfms_captures():
    """Stop the test process from writing its (real, but re-parsed)
    messages back into the live debug-capture directories."""
    from ingest.parsers import tfms_parser
    tfms_parser._debug_sample_count = 10**9
    tfms_parser._unknown_msgtype_count = 10**9


def _reset_removal_watch(suppress_db_refresh: bool = False):
    """Per-test reset of Detector C's module-level in-memory state."""
    from ingest.parsers import tfms_parser
    tfms_parser._REMOVAL_WATCH = {}
    tfms_parser._REMOVAL_WATCH_REFRESHED = (
        time.monotonic() if suppress_db_refresh else 0.0)
    tfms_parser._REMOVAL_SWEEP_TS = time.monotonic()  # keep parse() sweeps quiet


def _rows(sql: str, *params):
    from common.db import conn
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _removal_xml(acid: str, dep: str, arr: str, trigger: str, igtd: str,
                 stamp: str, airline: str | None = None) -> bytes:
    """SYNTHETIC plan-removal document, field-for-field shaped on the
    real flightPlanCancellation_0.xml capture (attributes + nested
    qualifiedAircraftId/igtd), for triggers no real capture carries."""
    airline = airline or acid[:3]
    return f"""<tfmDataService><fltdOutput>
<fltdMessage acid="{acid}" airline="{airline}" arrArpt="{arr}" depArpt="{dep}"
 fdTrigger="{trigger}" flightRef="900001" major="{airline}"
 msgType="flightPlanCancellation" sourceFacility="{airline}"
 sourceTimeStamp="{stamp}">
<flightPlanCancellation><qualifiedAircraftId>
<aircraftId>{acid}</aircraftId><igtd>{igtd}</igtd>
<departurePoint><airport>{dep}</airport></departurePoint>
<arrivalPoint><airport>{arr}</airport></arrivalPoint>
</qualifiedAircraftId></flightPlanCancellation>
</fltdMessage></fltdOutput></tfmDataService>""".encode()


def _activity_xml(acid: str, dep: str, arr: str, msg_type: str, igtd: str,
                  stamp: str) -> bytes:
    """SYNTHETIC post-removal activity message (departureInformation /
    FlightCreate / ...), same attribute frame as the real fltd captures."""
    return f"""<tfmDataService><fltdOutput>
<fltdMessage acid="{acid}" airline="{acid[:3]}" arrArpt="{arr}" depArpt="{dep}"
 msgType="{msg_type}" sourceFacility="{acid[:3]}" sourceTimeStamp="{stamp}">
<{msg_type}><qualifiedAircraftId>
<aircraftId>{acid}</aircraftId><igtd>{igtd}</igtd>
<departurePoint><airport>{dep}</airport></departurePoint>
<arrivalPoint><airport>{arr}</airport></arrivalPoint>
</qualifiedAircraftId></{msg_type}>
</fltdMessage></fltdOutput></tfmDataService>""".encode()


# ── Detector C: classification vocabulary ────────────────────────────────────

def test_removal_trigger_vocabulary():
    from ingest.parsers.tfms_parser import _classify_removal_trigger
    assert _classify_removal_trigger("FD_FLIGHT_CANCEL_MSG") == "cancellation"
    assert _classify_removal_trigger("UPDATE_CANCEL_TIMEOUT") == "cancellation"
    assert _classify_removal_trigger("HCS_CANCELLATION_MSG") == "superseded"
    assert _classify_removal_trigger("CANCEL_CMD") == "superseded"
    assert _classify_removal_trigger("TMI_UPDATE") == "superseded"
    # The trap the external document is loudest about: the international
    # timeout looks like the cleanest signal and is the opposite.
    assert (_classify_removal_trigger("UPDATE_INTERNATIONAL_CANCEL_TIMEOUT")
            == "left_coverage")
    assert _classify_removal_trigger("SOME_FUTURE_TRIGGER") == "unknown"
    assert _classify_removal_trigger(None) == "unknown"


def test_origin_surveilled_scope():
    from ingest.parsers.tfms_parser import _origin_is_surveilled
    assert _origin_is_surveilled("KRDU")
    assert _origin_is_surveilled("RDU")       # FAA 3-letter = domestic
    assert _origin_is_surveilled("PANC")      # Alaska
    assert _origin_is_surveilled("PHNL")      # Hawaii
    assert _origin_is_surveilled("TJSJ")      # Puerto Rico
    assert not _origin_is_surveilled("EGLL")  # left US surveillance is
    assert not _origin_is_surveilled("MMMX")  # not a cancellation signal
    assert not _origin_is_surveilled(None)


# ── Detector C: real-capture storage ─────────────────────────────────────────

def test_real_capture_fd_flight_cancel_stored():
    """flightPlanCancellation_0.xml -- real EDV4956 KRDU->KLGA removal,
    fdTrigger=FD_FLIGHT_CANCEL_MSG, igtd 2026-07-21T12:30:00Z."""
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        xml = (_CAPTURE_DIR / "flightPlanCancellation_0.xml").read_bytes()
        parse_tfms_message(xml)
        rows = _rows("SELECT * FROM tfms_plan_removals")
        assert len(rows) == 1
        r = rows[0]
        assert r["callsign"] == "EDV4956"
        assert r["removal_trigger"] == "FD_FLIGHT_CANCEL_MSG"
        assert r["kind"] == "cancellation"
        assert r["igtd"] == "2026-07-21T12:30:00Z"
        assert r["origin"] == "RDU" and r["destination"] == "LGA"
        assert r["carrier"] == "EDV"
        assert r["origin_surveilled"] == 1
        assert r["confirmed_at"] is None and r["reinstated_at"] is None
        # removed 2026-07-20T20:33:36Z, igtd next day 12:30 -> ~15.9 h lead
        assert r["filed_lead_h"] is not None and 15.0 < r["filed_lead_h"] < 17.0
    finally:
        _restore_db(orig, tmp)


def test_real_capture_hcs_cancellation_is_superseded():
    """flightPlanCancellation_4.xml -- real HCS_CANCELLATION_MSG removal:
    'cancellation' in the name, superseded in the classification (70.6%
    of these flew anyway in the reference measurement)."""
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        xml = (_CAPTURE_DIR / "flightPlanCancellation_4.xml").read_bytes()
        parse_tfms_message(xml)
        rows = _rows("SELECT * FROM tfms_plan_removals WHERE "
                     "removal_trigger='HCS_CANCELLATION_MSG'")
        assert len(rows) == 1
        assert rows[0]["kind"] == "superseded"
    finally:
        _restore_db(orig, tmp)


def test_all_twelve_real_captures_classify_into_vocabulary():
    """Every real removal capture stores a row and no capture produces
    kind='unknown' -- the locally-observed trigger set is a subset of the
    closed vocabulary."""
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        for p in sorted(_CAPTURE_DIR.glob("flightPlanCancellation_*.xml")):
            parse_tfms_message(p.read_bytes())
        rows = _rows("SELECT removal_trigger, kind, COUNT(*) AS n FROM "
                     "tfms_plan_removals GROUP BY removal_trigger, kind")
        assert rows, "no removal rows stored from real captures"
        by_trigger = {r["removal_trigger"]: r for r in rows}
        assert by_trigger["FD_FLIGHT_CANCEL_MSG"]["kind"] == "cancellation"
        assert by_trigger["HCS_CANCELLATION_MSG"]["kind"] == "superseded"
        assert all(r["kind"] != "unknown" for r in rows)
    finally:
        _restore_db(orig, tmp)


# ── Detector C: activity evidence + reinstatement + settle window ────────────

def test_flew_evidence_blocks_confirmation():
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        from common import db_swim
        now = datetime.now(timezone.utc)
        igtd = _iso(now - timedelta(hours=3))
        parse_tfms_message(_removal_xml(
            "UAL111", "KDCA", "KORD", "UPDATE_CANCEL_TIMEOUT", igtd,
            _iso(now - timedelta(hours=3))))
        # SYNTHETIC departure message for the same leg (same igtd).
        parse_tfms_message(_activity_xml(
            "UAL111", "KDCA", "KORD", "departureInformation", igtd,
            _iso(now - timedelta(hours=2))))
        r = _rows("SELECT * FROM tfms_plan_removals")[0]
        assert '"flew": true' in r["evidence"]
        assert '"departure_msg"' in r["evidence"]
        # Settle window long since passed -- but flew-evidence vetoes.
        assert db_swim.sweep_confirm_removals(settle_secs=60) == 0
        assert _rows("SELECT confirmed_at FROM tfms_plan_removals")[0][
            "confirmed_at"] is None
    finally:
        _restore_db(orig, tmp)


def test_reinstatement_blocks_confirmation():
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        from common import db_swim
        now = datetime.now(timezone.utc)
        igtd = _iso(now - timedelta(hours=3))
        parse_tfms_message(_removal_xml(
            "DAL222", "KBWI", "KATL", "FD_FLIGHT_CANCEL_MSG", igtd,
            _iso(now - timedelta(hours=3))))
        # SYNTHETIC reinstatement (a new FlightCreate for the same leg) --
        # the reference system saw 57% of removals reinstated.
        parse_tfms_message(_activity_xml(
            "DAL222", "KBWI", "KATL", "FlightCreate", igtd,
            _iso(now - timedelta(hours=2))))
        r = _rows("SELECT * FROM tfms_plan_removals")[0]
        assert r["reinstated_at"] is not None
        assert '"replanned_after"' in r["evidence"]
        assert db_swim.sweep_confirm_removals(settle_secs=60) == 0
    finally:
        _restore_db(orig, tmp)


def test_settle_window_confirmation_and_surveillance_gate():
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        from common import db_swim
        now = datetime.now(timezone.utc)
        old_igtd = _iso(now - timedelta(hours=4))
        # Confirmable: cancellation trigger, surveilled origin, aged out.
        parse_tfms_message(_removal_xml(
            "AAL333", "KDCA", "KMIA", "FD_FLIGHT_CANCEL_MSG", old_igtd,
            _iso(now - timedelta(hours=4))))
        # NOT confirmable: international origin (left_coverage territory
        # even under a cancellation-looking trigger scope test).
        parse_tfms_message(_removal_xml(
            "BAW444", "EGLL", "KJFK", "FD_FLIGHT_CANCEL_MSG", old_igtd,
            _iso(now - timedelta(hours=4))))
        # NOT confirmable: superseded kind.
        parse_tfms_message(_removal_xml(
            "SWA555", "KBWI", "KMDW", "CANCEL_CMD", old_igtd,
            _iso(now - timedelta(hours=4))))
        # NOT confirmable yet: igtd still inside the settle window.
        parse_tfms_message(_removal_xml(
            "JBU666", "KDCA", "KBOS", "UPDATE_CANCEL_TIMEOUT",
            _iso(now + timedelta(hours=2)), _iso(now)))
        assert db_swim.sweep_confirm_removals(settle_secs=3600) == 1
        confirmed = _rows("SELECT callsign FROM tfms_plan_removals "
                          "WHERE confirmed_at IS NOT NULL")
        assert [r["callsign"] for r in confirmed] == ["AAL333"]
        # Idempotent: nothing new on a second sweep.
        assert db_swim.sweep_confirm_removals(settle_secs=3600) == 0
    finally:
        _restore_db(orig, tmp)


def test_wrong_leg_activity_does_not_match():
    """Same callsign, different igtd AND different airport pair -- the
    next day's leg must not count as this leg's flew-evidence."""
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        now = datetime.now(timezone.utc)
        igtd = _iso(now - timedelta(hours=3))
        other_igtd = _iso(now + timedelta(hours=21))
        parse_tfms_message(_removal_xml(
            "UAL777", "KDCA", "KDEN", "FD_FLIGHT_CANCEL_MSG", igtd,
            _iso(now - timedelta(hours=3))))
        parse_tfms_message(_activity_xml(
            "UAL777", "KIAD", "KSFO", "departureInformation", other_igtd,
            _iso(now)))
        r = _rows("SELECT evidence FROM tfms_plan_removals")[0]
        assert '"flew"' not in r["evidence"]
    finally:
        _restore_db(orig, tmp)


def test_measure_removal_fly_rates_tabulates():
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        from common import db_swim
        now = datetime.now(timezone.utc)
        for i, trig in enumerate(["FD_FLIGHT_CANCEL_MSG",
                                  "FD_FLIGHT_CANCEL_MSG", "CANCEL_CMD"]):
            igtd = _iso(now - timedelta(hours=2, minutes=i))
            parse_tfms_message(_removal_xml(
                f"MEA{i}00", "KDCA", "KPHL", trig, igtd,
                _iso(now - timedelta(hours=2))))
        # One of the FD_FLIGHT_CANCEL_MSG legs flew anyway.
        parse_tfms_message(_activity_xml(
            "MEA000", "KDCA", "KPHL",
            "trackInformation", _iso(now - timedelta(hours=2)), _iso(now)))
        stats = db_swim.measure_removal_fly_rates(days=1)
        by_trig = {s["removal_trigger"]: s for s in stats}
        assert by_trig["FD_FLIGHT_CANCEL_MSG"]["legs"] == 2
        assert by_trig["FD_FLIGHT_CANCEL_MSG"]["flew"] == 1
        assert by_trig["FD_FLIGHT_CANCEL_MSG"]["flew_pct"] == 50.0
        assert by_trig["CANCEL_CMD"]["legs"] == 1
        assert by_trig["CANCEL_CMD"]["flew"] == 0
    finally:
        _restore_db(orig, tmp)


def test_re_removal_after_reinstatement_reopens_cycle():
    orig, tmp = _isolated_db()
    try:
        _quiet_tfms_captures()
        _reset_removal_watch(suppress_db_refresh=True)
        from ingest.parsers.tfms_parser import parse_tfms_message
        now = datetime.now(timezone.utc)
        igtd = _iso(now - timedelta(hours=2))
        removal = _removal_xml("NKS888", "KBWI", "KFLL",
                               "UPDATE_CANCEL_TIMEOUT", igtd,
                               _iso(now - timedelta(hours=2)))
        parse_tfms_message(removal)
        parse_tfms_message(_activity_xml(
            "NKS888", "KBWI", "KFLL", "FlightModify", igtd,
            _iso(now - timedelta(hours=1))))
        assert _rows("SELECT reinstated_at FROM tfms_plan_removals")[0][
            "reinstated_at"] is not None
        # Removed AGAIN -- one leg-keyed row, cycle re-opened.
        parse_tfms_message(removal)
        rows = _rows("SELECT * FROM tfms_plan_removals")
        assert len(rows) == 1
        assert rows[0]["reinstated_at"] is None
        assert rows[0]["confirmed_at"] is None
        # Prior evidence is KEPT (replanned_after survives the re-removal).
        assert '"replanned_after"' in rows[0]["evidence"]
    finally:
        _restore_db(orig, tmp)


# ── Detector D: FDPS route extraction (real captures) ────────────────────────

def test_real_ah_capture_route_text_extracted():
    """fixtures/fdps_fixm30_real/AH_sample_9.xml -- a fully-real,
    untouched 2026-08-17 capture snapshot carrying an agreed route: the
    extraction path against a complete real envelope."""
    from ingest.parsers.fdps_parser import parse_fdps_messages
    msgs = parse_fdps_messages(
        (_FIXTURES / "fdps_fixm30_real" / "AH_sample_9.xml").read_bytes())
    assert len(msgs) >= 1
    routes = [m["route_text"] for m in msgs if m.get("route_text")]
    assert "KDFW./.ROD237028..DKK..KBUF/0302" in routes


def test_hu_source_route_text_extracted():
    """The HU source (previously DROPPED by the allowlist) must parse and
    yield the agreed route + arrival estimate. Real captured field
    values; see the fixture's header for provenance."""
    from ingest.parsers.fdps_parser import parse_fdps_messages
    msgs = parse_fdps_messages(
        (_FIXTURES / "swim_audit" / "fdps_hu_route_filed.xml").read_bytes())
    assert len(msgs) == 1
    m = msgs[0]
    assert m["source"] == "HU"
    assert m["callsign"] == "JIA5230"
    assert m["route_text"] == "KDCA.CLTCH3.MAULS.Q40.NIOLA..MCB..KBTR/0319"
    assert m["eta_estimated"] == "2026-08-31T03:19:00Z"


def test_route_version_pair_classified_re_expression():
    """The JIA5230 filed route (HU) then its from-present-position
    re-expression (AH, same GUFI, 35 ms later). Two distinct versions
    must be stored and the second classified as noise (re_expression),
    never a genuine reroute."""
    orig, tmp = _isolated_db()
    try:
        from ingest.parsers.fdps_parser import parse_fdps_messages, write_flight_event
        for name in ("fdps_hu_route_filed.xml", "fdps_ah_route_activated.xml"):
            for m in parse_fdps_messages(
                    (_FIXTURES / "swim_audit" / name).read_bytes()):
                assert write_flight_event(m)  # KDCA origin passes the geo gate
        rows = _rows("SELECT * FROM fdps_route_versions ORDER BY version_num")
        assert len(rows) == 2
        assert rows[0]["version_num"] == 1 and rows[0]["change_class"] is None
        assert rows[1]["version_num"] == 2
        assert rows[1]["change_class"] == "re_expression"
        assert rows[0]["flight_id"] == rows[1]["flight_id"]
        # Same ETA on both versions -> zero cost movement.
        assert rows[1]["eta_delta_min"] == 0.0
        # Rebroadcast of a known version only bumps counters.
        for m in parse_fdps_messages(
                (_FIXTURES / "swim_audit" / "fdps_ah_route_activated.xml").read_bytes()):
            write_flight_event(m)
        rows = _rows("SELECT * FROM fdps_route_versions WHERE version_num=2")
        assert rows[0]["times_seen"] == 2
        assert _rows("SELECT COUNT(*) AS n FROM fdps_route_versions")[0]["n"] == 2
    finally:
        _restore_db(orig, tmp)


# ── Detector D: genuine-vs-noise classifier ──────────────────────────────────

def test_route_classifier_rules():
    from ingest.parsers.fdps_parser import _classify_route_change
    filed = "KDCA.CLTCH3.MAULS.Q40.NIOLA..MCB..KBTR/0319"
    # Real capture pair: filed -> activated re-expression.
    assert _classify_route_change(
        filed, "KDCA./.MAULS.Q40.NIOLA..MCB..KBTR/0319") == "re_expression"
    assert _classify_route_change(filed, filed) == "identical"
    # Progressive suffix trim as the flight proceeds.
    assert _classify_route_change(
        "KDCA./.MAULS.Q40.NIOLA..MCB..KBTR/0319",
        "KDCA./.NIOLA..MCB..KBTR/0319") == "suffix_trim"
    # Arrival procedure name change -> genuine.
    assert _classify_route_change(
        "KBOS.LOGAN4.MERIT..OTT.SKILS1.KDCA",
        "KBOS.LOGAN4.MERIT..OTT.CAPSS3.KDCA") == "genuine"
    # Departure procedure change with both non-null -> genuine.
    assert _classify_route_change(
        "KDCA.CLTCH3.MAULS.Q40.NIOLA..KBTR",
        "KDCA.DOCTR4.MAULS.Q40.NIOLA..KBTR") == "genuine"
    # Enroute body divergence beyond a trim -> genuine.
    assert _classify_route_change(
        "KDCA.CLTCH3.MAULS.Q40.NIOLA..MCB..KBTR",
        "KDCA.CLTCH3.MAULS.Q22.HRV..MCB..KBTR") == "genuine"
    # Same STAR, deep route unchanged, entry fix reassigned -> noise.
    assert _classify_route_change(
        "KJFK.TRUPS4.MERIT.J121.OTT.SKILS1.KDCA",
        "KJFK.TRUPS4.MERIT.J121.BUFFR.SKILS1.KDCA") == "entry_fix_only"
    assert _classify_route_change(None, filed) == "unclassified"
    assert _classify_route_change("...", filed) == "unclassified"


def test_parse_nas_route_components():
    from ingest.parsers.fdps_parser import _parse_nas_route
    p = _parse_nas_route("KDCA.CLTCH3.MAULS.Q40.NIOLA..MCB..KBTR/0319")
    assert p["origin"] == "KDCA" and p["dest"] == "KBTR"
    assert p["dep_proc"] == "CLTCH3"
    assert p["body"] == ["MAULS", "Q40", "NIOLA", "MCB"]  # Q40 = airway, kept
    assert p["arr_proc"] is None and not p["from_position"]
    p2 = _parse_nas_route("KDCA./.MAULS.Q40.NIOLA..MCB..KBTR/0319")
    assert p2["from_position"] and p2["dep_proc"] is None
    assert p2["body"] == p["body"]
    p3 = _parse_nas_route("KBOS.LOGAN4.MERIT..OTT.SKILS1.KDCA")
    assert p3["arr_proc"] == "SKILS1" and p3["arr_entry_fix"] == "OTT"
    assert _parse_nas_route("") is None
    assert _parse_nas_route("KDCA") is None


def test_init_db_swim_v46_is_idempotent():
    orig, tmp = _isolated_db()
    try:
        from common import db_swim
        db_swim.init_db_swim_v46()
        db_swim.init_db_swim_v46()
        assert _rows("SELECT COUNT(*) AS n FROM tfms_plan_removals")[0]["n"] == 0
        assert _rows("SELECT COUNT(*) AS n FROM fdps_route_versions")[0]["n"] == 0
    finally:
        _restore_db(orig, tmp)
