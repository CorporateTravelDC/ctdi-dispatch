"""tests/demo/test_ladd_timing.py -- LADD timing anti-correlation on the demo
path (operator decisions 2026-10-20; src/demo/ladd_timing.py). Synthetic Q
marks and a throwaway key only; no live database, no network."""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from demo import ladd_timing as lt  # noqa: E402

KEY = b"k" * 32
Q1, Q2 = "QABCDE", "QFGHJK"


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _legs_on(counts: dict):
    return lambda d: counts.get(d, 0)


def test_offset_deterministic_in_range_and_key_dependent():
    t, u = lt.LegTimer(KEY), lt.LegTimer(b"j" * 32)
    d = date(2026, 10, 18)
    on = _legs_on({d: 3})
    for k in range(3):
        o = t.offset(Q1, d, k, on)
        assert o == t.offset(Q1, d, k, on) == lt.LegTimer(KEY).offset(Q1.lower(), d, k, on)
        assert 0 <= o < lt.BUCKET_S
    assert any(t.offset(f"Q{i:05d}", d, 0, on) != u.offset(f"Q{i:05d}", d, 0, on) for i in range(20))
    assert len({t.offset(f"Q{i:05d}", d, 0, on) for i in range(200)}) > 150      # spread, not a constant


def test_every_leg_of_a_day_gets_its_own_offset_with_the_margin():
    """Operator 2026-10-20: two legs of one aircraft on one day never share an offset."""
    t = lt.LegTimer(KEY)
    d = date(2026, 10, 18)
    for n in (2, 3, 4):
        on = _legs_on({d: n})
        for i in range(150):
            offs = [t.offset(f"Q{i:05d}", d, k, on) for k in range(n)]
            assert None not in offs
            assert all(abs(a - b) >= lt.ROTATION_MARGIN_S for a, b in zip(offs, offs[1:]))


def test_margin_holds_across_day_boundaries_for_published_offsets():
    """Every consecutive pair of PUBLISHED legs (same day or across midnight,
    empty days break the chain) differs by the margin; a leg whose offset
    cannot be guaranteed is withheld (None = dropped), never published."""
    import random
    t = lt.LegTimer(KEY)
    rnd = random.Random(7)
    start = date(2026, 1, 1)
    for i in range(80):
        counts = {start + timedelta(days=j): rnd.choice((0, 1, 1, 2, 3, 4)) for j in range(40)}
        on = _legs_on(counts)
        seq = []
        for j in range(40):
            d = start + timedelta(days=j)
            if counts[d] == 0:
                seq.append(None)                       # an empty day ends the chain
                continue
            seq += [t.offset(f"Q{i:05d}", d, k, on) for k in range(counts[d])]
        for a, b in zip(seq, seq[1:]):
            if a is not None and b is not None:
                assert abs(a - b) >= lt.ROTATION_MARGIN_S
        assert sum(x is not None for x in seq) > 0.9 * sum(counts.values())   # withheld is rare


def test_chain_is_idempotent_and_independent_of_later_days():
    t = lt.LegTimer(KEY)
    d = date(2026, 10, 18)
    base = {d - timedelta(days=j): 2 for j in range(10)}
    later = {**base, d + timedelta(days=1): 4, d + timedelta(days=2): 1}
    assert [t.offset(Q1, d, k, _legs_on(base)) for k in range(2)] == \
           [t.offset(Q1, d, k, _legs_on(later)) for k in range(2)]


def test_unknown_leg_counts_fail_closed():
    t = lt.LegTimer(KEY)
    d = date(2026, 10, 18)
    assert t.offset(Q1, d, 0, lambda x: None) is None
    assert t.offset(Q1, d, 2, _legs_on({d: 2})) is None                          # no such leg


def test_leg_windows_split_at_arrival_midpoint_or_before_departure():
    legs = [(1000.0, 4000.0), (10000.0, None), (12000.0, None)]
    assert lt.leg_index(legs, 500) == 0 and lt.leg_index(legs, 6900) == 0     # midpoint (4000+10000)/2 = 7000
    assert lt.leg_index(legs, 7000) == 1
    assert lt.leg_index(legs, 10999) == 1 and lt.leg_index(legs, 11000) == 2   # no arrival: half the 2000 s gap


def test_bucket_lands_on_the_15_minute_grid():
    for off in (0, 1, 329, 570, 899):
        for e in (1_760_000_000, 1_760_000_123.5, 1_760_000_899):
            b = lt.LegTimer.bucket(e, off)
            assert b % lt.BUCKET_S == 0 and e + off - lt.BUCKET_S < b <= e + off


def test_operational_day_rolls_at_0500_eastern():
    assert lt.opday(_dt("2026-10-19T08:30:00")) == date(2026, 10, 18)    # 04:30 EDT
    assert lt.opday(_dt("2026-10-19T09:30:00")) == date(2026, 10, 19)    # 05:30 EDT


T0 = 1_760_790_000


class FakeLegs:
    """SWIM legs by (identifier, day); default: Q1's real ident flies one leg a
    day around T0's time of day. legs(...) -> None simulates a failed lookup."""

    def __init__(self, table=None, fail=False):
        self.table = table
        self.fail = fail
        self.calls = 0

    def legs(self, idents, day):
        self.calls += 1
        if self.fail:
            return None
        if self.table is not None:
            return self.table.get((frozenset(idents), day), [])
        if day not in DEFAULT_DAYS:
            return []                                                          # empty days around: chain uncapped
        lo, _ = lt.opday_bounds(day)
        return [(lo + 3600, lo + 3 * 3600)]                                    # one leg, 10:00Z-12:00Z


DEFAULT_DAYS = {date(2025, 10, 18), date(2026, 10, 18)}
REAL1 = "QREAL1"
IDS = {Q1: {REAL1}}


def _scrubber(rows=(), legs=None):
    return lt.Scrubber(lt.LegTimer(KEY), lt.Charts(rows), FakeLegs() if legs is None else legs)


def _apply(s, text, marks=frozenset({Q1}), at="2026-10-18T15:00:00", hex_listed=False, ids=IDS):
    return s.apply(text, set(marks), _dt(at), hex_listed, ids)


def test_json_record_times_bucketed_and_arrival_fields_dropped():
    s = _scrubber()
    row_at = _dt("2026-10-18T15:00:00")
    text = json.dumps({"flights": [
        {"callsign": Q1, "eta": "2026-10-18T14:07:12Z", "etd": 1760790000, "gate": "B12",
         "baggage_belt": "4", "fbo": "an FBO", "status": "landed", "destination": "KIAD"},
        {"callsign": "UAL1", "eta": "2026-10-18T14:07:12Z", "gate": "C3"}]})
    out = json.loads(_apply(s, text))
    q, other = out["flights"]
    assert set(q) == {"callsign", "eta", "etd", "destination"}             # gate, belt, FBO, status gone
    eta = datetime.fromisoformat(q["eta"].replace("Z", "+00:00"))
    assert eta.minute % 15 == 0 and eta.second == 0
    assert q["etd"] % lt.BUCKET_S == 0
    assert other == {"callsign": "UAL1", "eta": "2026-10-18T14:07:12Z", "gate": "C3"}   # non-LADD untouched


def test_same_leg_same_bucket_in_text_and_json():
    """No second offset on one leg (two windows would intersect)."""
    s = _scrubber()
    row_at = _dt("2026-10-18T15:00:00")
    j = json.loads(_apply(s, json.dumps({"callsign": Q1, "eta": "2026-10-18T14:07:00Z", "destination": "KIAD"})))
    t = _apply(s, f"Watch {Q1} ETA 2026-10-18T14:07:00Z.")
    assert j["eta"] in t


def test_text_arrival_sentences_dropped_and_clock_times_bucketed():
    s = _scrubber()
    row_at = _dt("2026-10-18T15:00:00")
    text = (f"{Q1} departed TEB, ETA 14:07Z.\n{Q1} landed 14:21Z at gate 7.\n"
            f"{Q1} wheels-on confirmed.\nWeather is fine at 14:07Z.\n")
    out = _apply(s, text)
    assert "landed" not in out and "gate 7" not in out and "wheels-on" not in out
    assert "Weather is fine at 14:07Z." in out                               # non-LADD sentence untouched
    m = re.search(rf"{Q1} departed TEB, ETA (\d\d):(\d\d)Z", out)
    assert m and int(m.group(2)) % 15 == 0


def _track(times_pos):
    return [{"lat": la, "lon": lo, "time": t} for la, lo, t in times_pos]


THR = {"ident": "RW01", "type": "RUNWAY_THRESHOLD", "lat": 38.90, "lon": -77.40, "parent_airport": "KIAD"}
APT = {"ident": "KIAD", "type": "AIRPORT", "lat": 38.95, "lon": -77.45, "parent_airport": "KIAD"}
IM = {"ident": "IM01", "type": "INNER_MARKER", "lat": 38.85, "lon": -77.35, "parent_airport": "KIAD"}
TRACK = [(39.5, -78.0, T0), (38.85, -77.35, T0 + 600), (38.90, -77.40, T0 + 700), (38.93, -77.43, T0 + 800)]


def test_dark_point_inner_marker_first():
    t, path = lt.dark_time(_track(TRACK), "KIAD", T0 + 750, lt.Charts([THR, APT, IM]))
    assert (t, path) == (T0 + 600, "im")


def test_dark_point_threshold_when_no_marker():
    t, path = lt.dark_time(_track(TRACK), "KIAD", T0 + 750, lt.Charts([THR, APT]))
    assert (t, path) == (T0 + 700, "threshold")


def test_dark_point_wheels_on_when_no_chart():
    t, path = lt.dark_time(_track(TRACK), "KIAD", T0 + 750, lt.Charts([]))
    assert (t, path) == (T0 + 750, "wheels_on")


def test_dark_point_fail_closed_drops_everything_near_any_airport():
    s = _scrubber([APT])
    rec = {"callsign": Q1, "track": _track(TRACK)}           # no destination, no wheels-on, no thresholds
    out = json.loads(_apply(s, json.dumps(rec)))
    assert len(out["track"]) == 1 and s.counts["ladd dark point fail_closed"] == 1
    s2 = _scrubber([])                                       # no chart at all: nothing survives
    out2 = json.loads(_apply(s2, json.dumps(rec)))
    assert out2["track"] == []


def test_track_cut_at_dark_point_and_times_bucketed():
    s = _scrubber([THR, APT])
    rec = {"callsign": Q1, "destination": "KIAD", "track": _track(TRACK)}
    out = json.loads(_apply(s, json.dumps(rec)))
    assert len(out["track"]) == 2                            # cut at the threshold crossing
    assert all(p["time"] % lt.BUCKET_S == 0 for p in out["track"])


def test_hex_only_listed_record_is_dropped_fail_closed():
    """A record known only by a redacted hex has no identifier to order its
    legs from SWIM: it is dropped, never shown with a coarser rule."""
    s = _scrubber()
    out = json.loads(_apply(s, json.dumps({"rows": [{"hex": "[hex-redacted]", "seen_at": "2026-10-18T14:07:12Z"},
                                                   {"hex": "abc", "seen_at": "2026-10-18T14:07:12Z"}]}),
                            marks=set(), hex_listed=True, ids={}))
    assert out == {"rows": [{"hex": "abc", "seen_at": "2026-10-18T14:07:12Z"}]}
    assert s.counts["ladd aircraft-day dropped (no SWIM leg order)"] == 1


def test_no_swim_leg_order_drops_records_and_sentences():
    for legs in (FakeLegs(table={}), FakeLegs(fail=True)):                     # no leg that day / lookup failed
        s = _scrubber(legs=legs)
        out = json.loads(_apply(s, json.dumps({"f": [{"callsign": Q1, "eta": "2026-10-18T14:07:00Z"},
                                                     {"callsign": "UAL1", "eta": "x"}]})))
        assert out == {"f": [{"callsign": "UAL1", "eta": "x"}]}
        txt = _apply(s, f"{Q1} departed TEB, ETA 14:07Z.\nCalm winds.\n")
        assert Q1 not in txt and "Calm winds." in txt


def test_two_legs_same_day_get_different_offsets_and_prose_matches_rows():
    """Leg order from SWIM: a reference goes to the leg whose window holds the
    time it mentions, so prose (no destination) and rows agree per leg."""
    d = date(2026, 10, 18)
    lo, _ = lt.opday_bounds(d)
    legs = [(lo + 3600, lo + 2 * 3600), (lo + 6 * 3600, lo + 7 * 3600)]        # 10:00Z-11:00Z, 15:00Z-16:00Z
    s = _scrubber(legs=FakeLegs(table={(frozenset({REAL1}), d): legs}))
    t = lt.LegTimer(KEY)
    offs = [t.offset(Q1, d, k, lambda x: 2 if x == d else 0) for k in range(2)]
    assert None not in offs and abs(offs[0] - offs[1]) >= lt.ROTATION_MARGIN_S
    rec0 = json.loads(_apply(s, json.dumps({"callsign": Q1, "etd": "2026-10-18T10:00:00Z",
                                            "eta": "2026-10-18T10:58:00Z", "destination": "KTEB"})))
    rec1 = json.loads(_apply(s, json.dumps({"callsign": Q1, "etd": "2026-10-18T15:00:00Z",
                                            "eta": "2026-10-18T15:58:00Z"})))
    prose = _apply(s, f"{Q1} ETA 2026-10-18T10:58:00Z.\n{Q1} ETA 2026-10-18T15:58:00Z.\n").splitlines()
    assert rec0["eta"] in prose[0] and rec1["eta"] in prose[1]
    for rec, raw, off in ((rec0, "2026-10-18T10:58:00Z", offs[0]), (rec1, "2026-10-18T15:58:00Z", offs[1])):
        want = lt.LegTimer.bucket(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp(), off)
        assert datetime.fromisoformat(rec["eta"].replace("Z", "+00:00")).timestamp() == want


def test_swim_legs_reads_flight_events_by_registration_and_callsign():
    import sqlite3
    from contextlib import contextmanager
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE flight_events (flight_id TEXT, airline TEXT, flight_num TEXT, registration TEXT, "
              "departure_time REAL, arrival_time REAL)")
    d = date(2026, 10, 18)
    lo, hi = lt.opday_bounds(d)
    c.executemany("INSERT INTO flight_events VALUES (?, ?, ?, ?, ?, ?)", [
        ("a", None, None, "QREG01", lo + 100, None),
        ("a2", None, None, "QREG01", lo + 150, lo + 900),                 # same 10-min slot: one leg, keeps the arrival
        ("b", "QXA", "123", None, lo + 7200, None),
        ("c", None, None, "QREG01", hi + 10, None),                       # next op day
        ("d", None, None, "QOTHER", lo + 50, None)])

    @contextmanager
    def src():
        yield c
    legs = lt.SwimLegs(src)
    assert legs.legs(frozenset({"QREG01", "QXA123"}), d) == [(lo + 150, lo + 900), (lo + 7200, None)]
    assert legs.legs(frozenset({"QNONE1"}), d) == []

    @contextmanager
    def broken():
        raise RuntimeError("down")
        yield
    assert lt.SwimLegs(broken).legs(frozenset({"QREG01"}), d) is None


def test_rows_without_ladd_are_untouched():
    s = _scrubber([THR])
    for text in ('{"tfrs":[{"start":"2026-10-18T14:07:12Z"}]}', "plain advisory 14:07Z gate 4"):
        assert _apply(s, text, marks=set()) == text


def test_idempotent_for_the_same_input():
    a = _apply(_scrubber(), f"{Q1} ETA 14:07Z.")
    b = _apply(_scrubber(), f"{Q1} ETA 14:07Z.")
    assert a == b


def test_only_the_demo_scrub_imports_ladd_timing():
    """Live/tailnet paths never apply it: the one importer is the scrub."""
    importers = []
    for p in list((REPO / "src").rglob("*.py")) + list((REPO / "scripts").rglob("*.py")):
        if p.name == "ladd_timing.py":
            continue
        if re.search(r"\bladd_timing\b", p.read_text(encoding="utf-8", errors="replace")):
            importers.append(p.relative_to(REPO).as_posix())
    assert importers == ["scripts/scrub-demo-source.py"], importers


def test_key_and_offsets_never_in_counts():
    s = _scrubber([THR, APT])
    _apply(s, json.dumps({"callsign": Q1, "destination": "KIAD", "track": _track(TRACK)}))
    assert all(not re.search(r"\d", k) and Q1 not in k for k in s.counts)
