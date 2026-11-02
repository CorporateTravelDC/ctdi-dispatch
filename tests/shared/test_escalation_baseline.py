"""2026-11-01: time-of-day escalation baselines, and ESCALATING meaning a surge only."""
from datetime import datetime, timezone
from pathlib import Path
import importlib.util
import json

import pytest

from shared import escalation_baseline as eb
from shared import sector_coalesce as sc

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_baseline", REPO / "scripts/build-escalation-baseline.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

# Monday 2026-11-02 14:30 America/New_York (EST) = 19:30 UTC
MON_1430 = datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc).timestamp()


def cell(n=30, weeks=6, p50=4, p90=8, p99=12):
    return {"n": n, "weeks": weeks, "p50": p50, "p90": p90, "p99": p99}


def test_slots_use_local_time_and_weekday_split():
    assert eb.slot_keys(MON_1430) == ("dow0-h14", "wk-h14")
    assert eb.slot_label("dow0-h14") == "Mon 14:00"
    assert eb.slot_label("we-h09") == "weekend 09:00"


def test_lookup_uses_weekday_slot_until_twenty_weeks_then_day_of_week():
    data = {"signals": {"tbfm_fix_queue": {"FIXA": {"wk-h14": cell(p90=8), "dow0-h14": cell(weeks=19, p90=20)}}}}
    assert eb.lookup("tbfm_fix_queue", "fixa", MON_1430, data)["slot"] == "wk-h14"
    data["signals"]["tbfm_fix_queue"]["FIXA"]["dow0-h14"]["weeks"] = 20
    assert eb.lookup("tbfm_fix_queue", "FIXA", MON_1430, data)["slot"] == "dow0-h14"


def test_lookup_refuses_thin_or_missing_cells():
    data = {"signals": {"tbfm_fix_queue": {"FIXA": {"wk-h14": cell(n=eb.MIN_SAMPLES - 1)}}}}
    assert eb.lookup("tbfm_fix_queue", "FIXA", MON_1430, data) is None
    assert eb.lookup("tbfm_fix_queue", "NOPE", MON_1430, data) is None
    assert eb.lookup("missing_signal", "FIXA", MON_1430, {}) is None


@pytest.mark.parametrize("value,expected", [
    (8, (False, False)),    # at p90 is normal
    (9, (True, False)),     # above p90
    (16, (True, True)),     # 2 x p90 and > p99
    (2, (False, False)),    # under the floor even if the cell is tiny
])
def test_classify_thresholds(value, expected):
    assert eb.classify(value, cell(p90=8, p99=12), floor=3) == expected


@pytest.fixture
def baseline_file(tmp_path, monkeypatch):
    path = tmp_path / "baseline.json"
    monkeypatch.setattr(eb, "BASELINE_FILE", path)
    eb._cache.update(mtime=None, data={})
    def write(signals):
        path.write_text(json.dumps({"signals": signals}))
    return write


@pytest.fixture
def fired(monkeypatch):
    calls = []
    import shared.watchlist as wl
    monkeypatch.setattr(wl, "_fire_ntfy_dual", lambda topic, title, detail, dispatch, priority=3:
                        calls.append((topic, title, detail, priority)))
    monkeypatch.setattr(sc, "_throttle_allows", lambda topic: True)
    monkeypatch.setattr(sc, "_record_fire", lambda topic: None)
    monkeypatch.setattr(sc.time, "time", lambda: MON_1430)
    monkeypatch.setattr(sc, "_PROCESS_START", MON_1430 - 7200)
    sc._feed_sector_events.clear()
    sc._sector_events.clear()
    return calls


def test_baseline_rise_fires_without_escalating_tag(baseline_file, fired):
    baseline_file({"tbfm_fix_queue": {"FIXA": {"wk-h14": cell(p90=8, p99=12)}}})
    r = sc.fire_family_alert("tbfm", "tbfm", "ZDC", "TBFM Metering -- FIXA", "FIXA: 10 in sequence",
                             "x", base_priority=2, activity=("tbfm_fix_queue", "FIXA", 10))
    assert r["baseline_source"] == "time_of_day" and r["escalating"] and not r["surge"]
    title, detail, priority = fired[0][1], fired[0][2], fired[0][3]
    assert not title.startswith("[ESCALATING") and priority == 2
    assert "10 now; typical 4, high 8 for weekday 14:00" in detail


def test_baseline_surge_is_tagged_escalating(baseline_file, fired):
    baseline_file({"tbfm_fix_queue": {"FIXA": {"wk-h14": cell(p90=8, p99=12)}}})
    sc.fire_family_alert("tbfm", "tbfm", "ZDC", "TBFM Metering -- FIXA", "d", "x", base_priority=2,
                         activity=("tbfm_fix_queue", "FIXA", 20))
    assert fired[0][1].startswith("[ESCALATING/") and fired[0][3] == 3


def test_normal_for_the_hour_does_not_fire(baseline_file, fired):
    baseline_file({"tbfm_fix_queue": {"FIXA": {"wk-h14": cell(p90=8, p99=12)}}})
    r = sc.fire_family_alert("tbfm", "tbfm", "ZDC", "t", "d", "x",
                             activity=("tbfm_fix_queue", "FIXA", 7))
    assert not r["escalating"] and fired == []


def test_without_a_baseline_the_rolling_check_applies_and_reports_counts(baseline_file, fired):
    baseline_file({})
    for _ in range(3):
        r = sc.fire_family_alert("tfms", "tfms", "ZDC", "t", "d", "x", base_priority=3)
    assert r["baseline_source"] == "rolling" and r["escalating"] and not r["surge"]
    assert not fired[-1][1].startswith("[ESCALATING") and "3 in the last 15 min" in fired[-1][2]


def test_builder_skips_outage_bins_and_splits_weekday_weekend():
    hour = 3600
    start = int(MON_1430 // hour) * hour
    rows = [("FIXA", start + 60)] * 5 + [("FIXA", start + 2 * hour + 60)] * 3
    built = builder.build_signal(rows, hour, min_rows=1)["FIXA"]
    # the empty hour in between belongs to no key: an outage bin, so it is not a zero sample
    assert built["wk-h14"]["n"] == 1 and built["wk-h14"]["p50"] == 5
    assert "wk-h15" not in built and built["wk-h16"]["p50"] == 3


def test_percentile_nearest_rank():
    assert builder.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == 9
    assert builder.percentile([], 50) == 0.0
