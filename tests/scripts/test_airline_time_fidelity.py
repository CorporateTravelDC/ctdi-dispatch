"""
2026-10-08: the airline time-fidelity research collector
(docs/research/AIRLINE_TIME_FIDELITY_SPEC.md). Pure derivation only -- the
collector's database reads are exercised by its live dry run, never by tests.
Identifiers here are synthetic.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location(
        "atf", REPO / "scripts" / "research" / "airline_time_fidelity_collect.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FIXM = ('<flight><departure departurePoint="KAUS"><runwayPositionAndTime><runwayTime>'
        '<actual time="2026-10-08T17:24:00Z" /></runwayTime></runwayPositionAndTime></departure>'
        '<arrival arrivalPoint="KIAD"><runwayPositionAndTime><runwayTime>'
        '<estimated time="2026-10-08T20:05:00Z" /></runwayTime></runwayPositionAndTime></arrival></flight>')


def flight(**kw):
    base = {"airline_out_time": "2026-10-08T17:07:00Z", "airline_off_time": "2026-10-08T17:24:00Z",
            "airline_on_time": "2026-10-08T20:13:00Z", "airline_in_time": "2026-10-08T20:18:00Z",
            "original_departure": "2026-10-08T17:24:00Z", "original_arrival": "2026-10-08T20:19:00Z"}
    return {**base, **kw}


def test_fdps_runway_times_reads_both_sides_and_kinds():
    m = load()
    assert m.fdps_runway_times(FIXM) == {"departure_actual": "2026-10-08T17:24:00Z",
                                         "arrival_estimated": "2026-10-08T20:05:00Z"}
    assert m.fdps_runway_times(None) == {}


def test_frozen_off_and_deltas_against_surface_observation():
    m = load()
    sfc_org = [{"airport": "KXXX", "event": "spotout", "event_time": "2026-10-08T17:09:30Z"},
               {"airport": "KXXX", "event": "off", "event_time": "2026-10-08T17:20:00Z"}]
    sfc_dst = [{"airport": "KIAD", "event": "on", "event_time": "2026-10-08T20:12:30Z"},
               {"airport": "KIAD", "event": "spotin", "event_time": "2026-10-08T20:15:00Z"}]
    d = m.derive(flight(), m.fdps_runway_times(FIXM), sfc_org, sfc_dst)
    assert d["frozen_off"] is True and d["frozen_on"] is False
    assert d["off_vs_filed_min"] == 0.0 and d["off_vs_fdps_min"] == 0.0
    assert d["off_vs_surface_min"] == 4.0                 # reported 4 min after the observed takeoff
    assert d["on_vs_surface_min"] == 0.5
    assert d["reported_taxi_out_min"] == 17.0 and d["observed_ramp_exit_to_off_min"] == 10.5
    assert d["in_vs_ramp_entry_min"] == 3.0


def test_no_observation_means_none_never_zero():
    m = load()
    d = m.derive(flight(airline_off_time=None), {}, [], [])
    assert d["frozen_off"] is False
    assert d["off_vs_surface_min"] is None and d["off_vs_fdps_min"] is None
    assert d["surface_coverage"] == {"origin": False, "destination": False}


def test_events_outside_the_window_are_not_matched():
    m = load()
    far = [{"airport": "KXXX", "event": "off", "event_time": "2026-10-07T17:20:00Z"}]   # the day before
    assert m.derive(flight(), {}, far, [])["off_vs_surface_min"] is None


# -- rolling rollup and the publication rule (operator, 2026-10-08) ---------------------------

def load_rollup():
    spec = importlib.util.spec_from_file_location(
        "atf_rollup", REPO / "scripts" / "research" / "airline_time_fidelity_rollup.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _row(gufi, airline, collected, dep="2026-10-08T17:24:00Z", on_vs=3.0, brands=()):
    return {"kind": "flight", "gufi": gufi, "callsign": f"{airline}0", "airline": airline, "origin": "KQQQ",
            "destination": "KIAD", "collected_at": collected, "brands": list(brands), "scope": ["dc"],
            "reported": {"original_departure": dep},
            "derived": {"on_vs_filed_min": on_vs, "frozen_off": True, "off_vs_surface_min": 4.0}}


def test_rollup_keeps_the_latest_snapshot_per_flight_and_respects_the_window(tmp_path):
    import json as _json
    from datetime import datetime, timezone
    r = load_rollup()
    rows = [_row("g1", "AAL", "2026-10-08T18:00:00Z", on_vs=1.0), _row("g1", "AAL", "2026-10-08T23:00:00Z", on_vs=20.0),
            _row("g2", "DAL", "2026-10-08T18:00:00Z", dep="2026-07-01T10:00:00Z")]     # outside 90 days
    (tmp_path / "2026-10-08.jsonl").write_text("\n".join(_json.dumps(x) for x in rows) + "\n")
    got = r.load(tmp_path, datetime(2026, 10, 9, tzinfo=timezone.utc), 30)
    assert [(x["gufi"], x["derived"]["on_vs_filed_min"]) for x in got] == [("g1", 20.0)]


def test_public_output_carries_anchors_only_and_no_codeshare_detail(tmp_path):
    import json as _json
    from datetime import datetime, timezone
    r = load_rollup()
    rows = [_row("g1", "UAL", "2026-10-08T18:00:00Z"), _row("g2", "QQX", "2026-10-08T18:00:00Z", brands=("UAL",))]
    (tmp_path / "2026-10-08.jsonl").write_text("\n".join(_json.dumps(x) for x in rows) + "\n")
    r.run(tmp_path, datetime(2026, 10, 9, tzinfo=timezone.utc))
    pub = _json.loads((tmp_path / "rolling" / "public-anchors-30d.json").read_text())
    assert set(pub["by_anchor"]) == {"UAL"} and pub["by_anchor"]["UAL"]["flights"] == 1   # the regional row is not attributed
    assert "QQX" not in _json.dumps(pub) and "brand" not in _json.dumps(pub)
    priv = _json.loads((tmp_path / "rolling" / "private-30d.json").read_text())
    assert "QQX" in priv["by_operating_carrier"] and "UAL" in priv["by_brand"]


def test_the_private_ledger_never_reaches_the_public_mirror():
    spec = importlib.util.spec_from_file_location("scrub_public_tree_atf", REPO / "scripts" / "scrub-public-tree.py")
    m = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    assert "docs/research/private" in m.DROP_DIRS
    # bellwether identities live only in the private file; neither the public spec nor the code names them
    bw = REPO / "docs/research/private/bellwethers.txt"
    if not bw.is_file():                                   # the public edition has no private ledger
        return
    private = bw.read_text().split()
    ids = [x for x in private if x.isalnum() and any(ch.isdigit() for ch in x)]
    assert ids
    for f in ("docs/research/AIRLINE_TIME_FIDELITY_SPEC.md", "scripts/research/airline_time_fidelity_collect.py",
              "scripts/research/airline_time_fidelity_rollup.py", "scripts/research/airline-time-fidelity-collect.sh"):
        text = (REPO / f).read_text()
        assert not any(i in text for i in ids), f
