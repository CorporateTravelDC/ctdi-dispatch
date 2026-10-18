"""2026-10-18 (relay L1, panel phase 0): the Dispatch Drawer's context reads the
live shapes of every endpoint it fetches. Fixtures are the shapes verified with
one GET each against dispatch-web on 2026-10-18 (synthetic values). Before the
fix the model was told "TFRS: none active" with ~80 active, got no weather, and
the text/plain brief was dropped."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from runner import main as rm  # noqa: E402

LIVE = {
    "cps": {"score": "72", "label": "GO", "factors": {"ceiling": "ok", "wind": "ok"},
            "narrative": "VFR, light winds", "computed_at": 1760800000.0},
    "weather": {"metars": [
        {"station": "KDCA", "ceiling_ft": None, "visibility_sm": 10.0, "wind_kt": 8,
         "precip_code": None, "obs_time": 1760799000.0, "fetched_at": 1760799100.0},
        {"station": "KIAD", "ceiling_ft": 2500, "visibility_sm": 6.0, "wind_kt": 12,
         "precip_code": "RA", "obs_time": 1760799000.0, "fetched_at": 1760799100.0},
    ]},
    "tfr": {"tfrs": [
        {"tfr_id": "5/1234", "is_vip": False, "effective_start": None, "effective_end": None},
        {"tfr_id": "5/9999", "is_vip": True, "effective_start": 1760799000.0, "effective_end": 1760810000.0},
        {"tfr_id": "5/2222", "is_vip": False, "effective_start": None, "effective_end": None},
    ], "count": 3},
    "feeds": {"feeds": [
        {"feed_name": "metar", "age_seconds": 100, "stale_threshold_seconds": 1200,
         "push_covered": False, "error": None},
        {"feed_name": "nas", "age_seconds": 5000, "stale_threshold_seconds": 600,
         "push_covered": True, "error": None},
    ]},
    "alerts": {"alerts": [
        {"alert_id": "a1", "event_type": "Wind Advisory", "area_desc": "DC",
         "severity": "Moderate", "headline": "Wind Advisory until 6 PM"},
    ], "count": 1},
    "notam": {"notams": [
        {"notam_id": "!DCA 10/001", "facility": "DCA", "classification": "DOM",
         "text_body": "RWY 1/19 CLSD"},
    ], "count": 6628},
    "amtrak": {"available": True, "summary": "2 trains delayed", "fetched_at": 1760799000.0, "trains": []},
    "brief": "DAILY BRIEF 2026-10-18\nAll quiet in the FRZ.\n" + "x" * 2000,
}


def test_context_reports_tfrs_weather_alerts_notams_brief():
    out = rm._context_to_str(LIVE)
    assert "TFRS: 3 active (1 VIP/POTUS)" in out
    assert "5/9999" in out and "none active" not in out.split("NWS")[0]
    assert "KDCA: no ceiling, vis 10.0 sm, wind 8 kt" in out
    assert "KIAD: ceiling 2500 ft" in out and "precip RA" in out
    assert "NWS ALERTS (1): Wind Advisory until 6 PM" in out
    assert "NOTAMS: 6628 active" in out
    assert "AMTRAK/WAS: 2 trains delayed" in out
    assert "CPS: 72 / GO" in out
    assert "FEEDS: nominal" in out           # nas is old but push-covered
    assert "LATEST BRIEF" in out and "All quiet in the FRZ." in out
    assert len(out) < 2500                   # the brief is capped


def test_stale_uses_the_feeds_own_threshold():
    ctx = {"feeds": {"feeds": [{"feed_name": "metar", "age_seconds": 1300,
                                "stale_threshold_seconds": 1200, "push_covered": False, "error": None}]}}
    assert "FEEDS STALE: metar" in rm._context_to_str(ctx)


def test_empty_tfr_list_says_none_active():
    assert "TFRS: none active" in rm._context_to_str({"tfr": {"tfrs": [], "count": 0}})


@pytest.mark.parametrize("q,expect", [
    ("any tfr up?", "TFRs active: 3 (1 VIP/POTUS)"),
    ("weather at dca", "KDCA: no ceiling"),
    ("nws alerts", "Wind Advisory until 6 PM"),
    ("notams", "NOTAMs: 6628 active"),
    ("feed health", "Feeds nominal: 2 active"),
    ("brief me", "All quiet in the FRZ."),
    ("amtrak", "2 trains delayed"),
    ("cps", "CPS: 72"),
])
def test_local_answer_reads_live_shapes(q, expect):
    assert expect in (rm._local_answer(q, LIVE) or "")


def test_local_answer_vip_tfr_listed_first():
    ans = rm._local_answer("tfr", LIVE)
    lines = [ln.strip() for ln in ans.splitlines()[1:]]
    assert lines[0].startswith("5/9999 VIP")


def test_bare_lists_still_accepted():
    ctx = {"tfr": [{"tfr_id": "X", "is_vip": False}], "alerts": [], "notam": []}
    out = rm._context_to_str(ctx)
    assert "TFRS: 1 active" in out and "NWS ALERTS: none active" in out


def test_build_context_keeps_the_text_brief(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/brief"):
            return httpx.Response(200, text=LIVE["brief"], headers={"content-type": "text/plain; charset=utf-8"})
        topic = {"cps": "cps", "weather": "weather", "tfr": "tfr", "feeds": "feeds",
                 "alerts": "alerts", "notams": "notam", "amtrak": "amtrak"}[path.rsplit("/", 1)[-1]]
        return httpx.Response(200, json=LIVE[topic])

    real = httpx.AsyncClient
    monkeypatch.setattr(rm.httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(handler), **k))
    ctx = asyncio.run(rm._build_context_rich())
    assert set(ctx) == set(LIVE)
    assert isinstance(ctx["brief"], str) and ctx["brief"].startswith("DAILY BRIEF")
    assert ctx["tfr"]["count"] == 3
