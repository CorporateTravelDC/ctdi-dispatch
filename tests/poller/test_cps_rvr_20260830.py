"""
Tests for the 2026-08-30 RVR -> CPS wiring (SWIM-audit backlog #1):
touchdown RVR on the active arrival runway substitutes for METAR
prevailing visibility in the Part 135.609 go/no-go engine, via the
official 14 CFR 91.175(h) RVR/visibility correlation table.

Covers, in order:
  1. The correlation table itself -- every published pair, the
     conservative floor-mapping between rows, the below-table floor, and
     the max-range (6000+) None sentinel.
  2. Runway-config name parsing + runway-name normalization (zero-padded
     APDS names vs unpadded ITWS config names).
  3. _compute_cps() source-selection semantics: RVR preferred when
     measuring, METAR fallback at max range, non-reporting sensors
     contribute nothing (never zero visibility), active-config filtering
     with worst-of fallback, and the vis-source audit annotation --
     including that the output is byte-identical to the pre-RVR engine
     when no RVR data exists.
  4. A REAL captured RVRDataUpdateMessage
     (tests/ingest/fixtures/swim_audit/RVRDataUpdateMessage_0.xml,
     unmodified live capture) run through smes_parser's normalization and
     then this scoring path end-to-end.
  5. build_inputs() against an isolated DB with real v41 tables: the new
     "rvr"/"runway_config" keys appear, carry values only (no timestamps
     -- the SR-2 hash discipline), and stale rows age out.
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from poller.skills import cps_recompute as cps

FIXTURES = Path(__file__).parent.parent / "ingest" / "fixtures" / "swim_audit"


def _utc_iso(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _base_inputs(**overrides) -> dict:
    inputs = {
        "metar": [
            {"station": "KDCA", "ceiling_ft": 5000, "visibility_sm": 10.0,
             "wind_kt": 8, "precip": None},
            {"station": "KIAD", "ceiling_ft": 6500, "visibility_sm": 10.0,
             "wind_kt": 9, "precip": None},
            {"station": "KBWI", "ceiling_ft": 4800, "visibility_sm": 10.0,
             "wind_kt": 9, "precip": None},
        ],
        "nas_programs": [],
        "itws": [],
        "rvr": [],
        "runway_config": [],
    }
    inputs.update(overrides)
    return inputs


def _rvr_row(airport="KDCA", runway="01", touchdown=6000, trend="U",
             midpoint=None, rollout=6000):
    return {"airport": airport, "runway": runway,
            "touchdown_rvr_ft": touchdown, "touchdown_trend": trend,
            "midpoint_rvr_ft": midpoint, "rollout_rvr_ft": rollout}


# ── 1. The 91.175(h) correlation table ───────────────────────────────────────

def test_rvr_table_exact_pairs_match_91_175h():
    """Every pair published in 14 CFR 91.175(h) 'Comparable values of RVR
    and ground visibility', verbatim. If this test ever needs editing to
    pass, the TABLE was edited -- re-verify against the CFR first."""
    expected = {
        1600: 0.25, 2400: 0.5, 3200: 0.625, 4000: 0.75,
        4500: 0.875, 5000: 1.0,
    }
    for ft, sm in expected.items():
        got = cps._rvr_equivalent_vis_sm(ft)
        assert got is not None and got[0] == sm, (ft, got)
    # 6000 is IN the published table (1 1/4 SM) but is also RVR's max
    # reportable value ("6000+") -- a saturated sensor attests only
    # ">= 1 1/4 SM", useless against a 3.0 SM minimum, so the scoring
    # helper deliberately returns the None sentinel there.
    assert cps._rvr_equivalent_vis_sm(6000) is None
    assert cps._rvr_equivalent_vis_sm(9999) is None


def test_rvr_table_floors_between_rows_and_below():
    """Between published rows the mapping goes DOWN (never credit more
    visibility than the table attests); below 1,600 ft it is '<1/4 SM'."""
    assert cps._rvr_equivalent_vis_sm(3000) == (0.5, "1/2")     # not 5/8
    assert cps._rvr_equivalent_vis_sm(5999) == (1.0, "1")       # not 1 1/4
    assert cps._rvr_equivalent_vis_sm(1601) == (0.25, "1/4")
    assert cps._rvr_equivalent_vis_sm(800) == (0.0, "<1/4")
    assert cps._rvr_equivalent_vis_sm(100) == (0.0, "<1/4")


def test_rvr_table_never_reaches_the_hems_minimum():
    """The structural safety property the wiring relies on: NO measuring
    RVR reading can convert to >= 3.0 SM, so the substitution can only
    ever force a visibility violation, never clear one."""
    for ft in range(100, 6000, 100):
        got = cps._rvr_equivalent_vis_sm(ft)
        assert got is not None
        assert got[0] < cps.CPS_MINIMUMS["visibility_sm"]


# ── 2. Runway-config parsing / runway normalization ──────────────────────────

def test_active_runways_from_real_config_name():
    # Real captured rc_config_name (Runway_Configuration_Product.xml,
    # PCT/IAD, 2026-08-30).
    assert cps._active_runways_from_config("IAD-19L-19C-12") == {
        "19L", "19C", "12"}
    assert cps._active_runways_from_config(None) == frozenset()
    assert cps._active_runways_from_config("IAD") == frozenset()


def test_runway_normalization_bridges_apds_and_itws_naming():
    # Live stdds_rvr rows use APDS zero-padded names ('01'); ITWS config
    # names don't ('1' inside e.g. 'DCA-1-33').
    assert cps._norm_runway("01") == "1"
    assert cps._norm_runway("01L") == "1L"
    assert cps._norm_runway("19C") == "19C"
    assert cps._norm_runway("0") == "0"  # degenerate: never empty out


# ── 3. _compute_cps() source selection ───────────────────────────────────────

def test_no_rvr_data_output_is_byte_identical_to_metar_only_engine():
    """The explicit don't-silently-change-the-output guardrail: with no
    RVR rows, score/label/factors/narrative are exactly the pre-RVR
    result (no annotation tail)."""
    data = cps._compute_cps(_base_inputs())
    assert data["score"] == "GREEN" and data["label"] == "GO"
    assert data["narrative"] == "All factors within Part 135.609 HEMS minimums"
    assert "[vis src:" not in data["narrative"]


def test_measuring_rvr_forces_visibility_violation_over_clear_metar():
    """Touchdown RVR 2400 ft (= 1/2 SM per 91.175(h)) at KDCA must go RED
    on visibility even though METAR prevailing visibility is 10 SM --
    the localized runway instrument wins when it is actually measuring."""
    data = cps._compute_cps(_base_inputs(rvr=[_rvr_row(touchdown=2400, trend="D")]))
    assert data["score"] == "RED"
    assert data["factors"]["visibility"] == "violated"
    assert "RVR 2400ft" in data["narrative"]
    assert "1/2SM" in data["narrative"]
    assert "91.175(h)" in data["narrative"]
    assert "KDCA=rvr rwy 01 2400ft" in data["narrative"]


def test_max_range_rvr_defers_to_metar_but_stays_in_audit_trail():
    """6000+ (saturated) RVR cannot attest 3.0 SM: METAR governs (GREEN
    here), and the deferral is visible in the stored narrative rather
    than only in a log line."""
    data = cps._compute_cps(_base_inputs(
        rvr=[_rvr_row(runway="01", touchdown=6000),
             _rvr_row(runway="19", touchdown=6000)]))
    assert data["score"] == "GREEN"
    assert data["factors"]["visibility"] == "ok"
    assert "[vis src: KDCA=metar (touchdown RVR at max range 6000ft+" \
        in data["narrative"]


def test_max_range_rvr_does_not_mask_a_metar_violation():
    """Saturated RVR defers to METAR in BOTH directions: a 2.0 SM METAR
    still violates the 3.0 SM minimum even with 6000+ on the runway."""
    inputs = _base_inputs(rvr=[_rvr_row(touchdown=6000)])
    inputs["metar"][0]["visibility_sm"] = 2.0
    data = cps._compute_cps(inputs)
    assert data["score"] == "RED"
    assert data["factors"]["visibility"] == "violated"
    assert "KDCA vis 2.0SM" in data["narrative"]


def test_offline_sensor_is_no_reading_never_zero_visibility():
    """touchdown None (blank/'00' on the wire -- see
    smes_parser._rvr_value_ft) must contribute nothing: with every sensor
    at the station non-reporting, scoring is pure METAR with no
    annotation, NOT a zero-visibility violation."""
    data = cps._compute_cps(_base_inputs(
        rvr=[_rvr_row(touchdown=None, trend=None),
             _rvr_row(runway="19", touchdown=None, trend=None)]))
    assert data["score"] == "GREEN"
    assert data["factors"]["visibility"] == "ok"
    assert "[vis src:" not in data["narrative"]


def test_active_config_prefers_active_runway_reading():
    """With a fresh ITWS config naming rwy 1 active, the active runway's
    reading is scored even when a NON-active runway reads lower -- the
    guardrail's 'touchdown RVR on the currently-active arrival runway'.
    (APDS '01' must match config '1' through normalization.)"""
    data = cps._compute_cps(_base_inputs(
        rvr=[_rvr_row(runway="01", touchdown=4000),
             _rvr_row(runway="19", touchdown=1600)],
        runway_config=[{"airport": "KDCA", "config": "DCA-1"}]))
    assert data["score"] == "RED"
    assert "RVR 4000ft rwy 01" in data["narrative"]
    assert "3/4SM" in data["narrative"]
    assert "active-config rwys" in data["narrative"]


def test_unknown_or_mismatched_config_falls_back_to_worst_of_all():
    """No config (or a config that intersects no reporting runway) scores
    the WORST reading across all reporting runways -- the conservative
    superset, never a silent discard."""
    rows = [_rvr_row(runway="01", touchdown=4000),
            _rvr_row(runway="19", touchdown=1600)]
    for cfg in ([], [{"airport": "KDCA", "config": "DCA-15-33"}]):
        data = cps._compute_cps(_base_inputs(rvr=list(rows), runway_config=cfg))
        assert data["score"] == "RED"
        assert "RVR 1600ft rwy 19" in data["narrative"]
        assert "all reporting rwys" in data["narrative"]


def test_mixed_stations_annotate_independently():
    """KDCA measuring low, KIAD saturated, KBWI no RVR at all: one
    violation, two annotations, KBWI stays out of the tail."""
    data = cps._compute_cps(_base_inputs(
        rvr=[_rvr_row(airport="KDCA", touchdown=2400),
             _rvr_row(airport="KIAD", runway="19C", touchdown=6000)]))
    assert data["score"] == "RED"
    assert "KDCA=rvr rwy 01 2400ft" in data["narrative"]
    assert "KIAD=metar (touchdown RVR at max range" in data["narrative"]
    assert "KBWI" not in data["narrative"].split("[vis src:")[1]


# ── 4. Real captured sample through the scoring path ─────────────────────────

def test_real_captured_rvr_sample_end_to_end(monkeypatch, tmp_path):
    """RVRDataUpdateMessage_0.xml is an UNMODIFIED live capture (KFSD,
    touchdown '60' = 6000 ft trend '+', midpoint blank = non-reporting).
    Its values -- exactly as smes_parser normalizes them -- must ride the
    max-range METAR-fallback path and surface the non-reporting midpoint
    as None in the audit row. (The station label is remapped to KDCA for
    scoring scope only; every VALUE is the capture's own.)"""
    from ingest.parsers.smes_parser import parse_tdes_apds_message
    rec = parse_tdes_apds_message((FIXTURES / "RVRDataUpdateMessage_0.xml").read_bytes())
    assert rec is not None and rec["kind"] == "rvr"
    rows = []
    for rw in rec["runways"]:
        assert rw["touchdown_rvr_ft"] == 6000       # '60' -> hundreds of ft
        assert rw["touchdown_trend"] == "U"          # '+' -> U
        assert rw["midpoint_rvr_ft"] is None         # blank -> no reading
        rows.append({"airport": "KDCA", "runway": rw["runway"],
                     "touchdown_rvr_ft": rw["touchdown_rvr_ft"],
                     "touchdown_trend": rw["touchdown_trend"],
                     "midpoint_rvr_ft": rw["midpoint_rvr_ft"],
                     "rollout_rvr_ft": rw["rollout_rvr_ft"]})
    data = cps._compute_cps(_base_inputs(rvr=rows))
    assert data["score"] == "GREEN"
    assert "max range 6000ft+" in data["narrative"]


# ── 5. build_inputs() against real v41 tables ────────────────────────────────

def test_build_inputs_rvr_values_only_and_staleness(monkeypatch):
    import common.db as db
    from common import db_swim

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    db._db_path = lambda: Path(tmp.name)
    try:
        db.init_db_all()
        db_swim.init_db_swim_v41()

        now = _utc_iso()
        db_swim.upsert_stdds_rvr("KDCA", "01", 6000, "U", None, None, 6000, "U",
                                 "0", "0", last_seen=now)
        # Stale row (2h old) must age out of the inputs entirely.
        db_swim.upsert_stdds_rvr("KDCA", "19", 1600, "D", None, None, 1600, "D",
                                 "0", "0", last_seen=_utc_iso(-7200))
        # Non-primary airport must not appear even when fresh.
        db_swim.upsert_stdds_rvr("KFSD", "03", 2400, "S", None, None, None, None,
                                 "0", "0", last_seen=now)
        db.upsert_itws_alert("KIAD", cps.RUNWAY_CONFIG_PRODUCT, 0,
                             "Active runway configuration: IAD-19L-19C-12",
                             now, None, "{}")

        inputs = cps.build_inputs()
        assert inputs["rvr"] == [{
            "airport": "KDCA", "runway": "01", "touchdown_rvr_ft": 6000,
            "touchdown_trend": "U", "midpoint_rvr_ft": None,
            "rollout_rvr_ft": 6000,
        }]
        assert inputs["runway_config"] == [
            {"airport": "KIAD", "config": "IAD-19L-19C-12"}]
        # SR-2 hash discipline: content only, no timestamps anywhere in
        # the new keys.
        for row in inputs["rvr"] + inputs["runway_config"]:
            assert "last_seen" not in row and "received_at" not in row
    finally:
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)


def test_build_inputs_survives_missing_v41_tables(monkeypatch):
    """A DB that predates db_swim (init_db_swim_v41 never ran -- e.g. the
    poller image running against a pre-audit volume) must degrade to
    rvr=[] and still produce a full inputs dict, never crash the score."""
    import common.db as db

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    db._db_path = lambda: Path(tmp.name)
    try:
        db.init_db_all()   # deliberately NOT init_db_swim_v41()
        inputs = cps.build_inputs()
        assert inputs["rvr"] == []
        assert "metar" in inputs and "runway_config" in inputs
    finally:
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)
