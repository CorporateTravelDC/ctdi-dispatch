"""2026-10-20: gate-key helpers. Diagnosis: route-impact keyed every national NAS program
(127, ~33 changing per 5 min) and ep-advance keyed raw METAR numbers, so both gates logged
gate=new on every run and route-impact kept llama's second slot permanently busy."""
from __future__ import annotations

import json

from common import input_gate as ig


def _tfr(i, vip, fac):
    return {"tfr_id": i, "is_vip": vip, "raw_json": json.dumps({"facility": fac})}


def test_dc_vip_ids_only_dc_facilities_and_vip():
    tfrs = [_tfr("6/1", 1, "ZDC"), _tfr("6/2", 1, "ZLA"), _tfr("6/3", 0, "ZDC"), _tfr("6/4", 1, "PCT")]
    assert ig.dc_vip_tfr_ids(tfrs) == ["6/1", "6/4"]


def test_dc_nas_key_ignores_id_churn_and_national():
    a = [{"program_id": f"m{i}", "type": "MIT", "facility": "IAD"} for i in range(12)] + \
        [{"program_id": "g1", "type": "GDP", "facility": "EWR"}]
    b = [{"program_id": f"x{i}", "type": "MIT", "facility": "KIAD"} for i in range(3)] + \
        [{"program_id": "g9", "type": "GS", "facility": "ORD"}]
    assert ig.dc_nas_key(a) == ig.dc_nas_key(b) == [["MIT", "IAD"]]
    c = b + [{"program_id": "z", "type": "GDP", "facility": "DCA"}]
    assert ig.dc_nas_key(c) != ig.dc_nas_key(b)


def test_weather_key_stable_under_noise():
    a = [{"station": "KDCA", "ceiling_ft": 4500, "visibility_sm": 10, "wind_kt": 6, "precip_code": "-RA"}]
    b = [{"station": "KDCA", "ceiling_ft": 5000, "visibility_sm": 9, "wind_kt": 11, "precip_code": "RA"}]
    assert ig.weather_key(a) == ig.weather_key(b)


def test_weather_key_changes_on_situation_change():
    base = {"station": "KDCA", "ceiling_ft": 5000, "visibility_sm": 10, "wind_kt": 8}
    k = ig.weather_key([base])
    assert ig.weather_key([{**base, "ceiling_ft": 2500}]) != k          # VFR -> MVFR
    assert ig.weather_key([{**base, "visibility_sm": 2}]) != k          # IFR visibility
    assert ig.weather_key([{**base, "wind_kt": 25}]) != k               # strong wind
    assert ig.weather_key([{**base, "gust_kt": 38}]) != k               # high gust
    assert ig.weather_key([{**base, "precip_code": "TSRA"}]) != k        # thunder
    assert ig.weather_key([{**base, "precip_code": "FZRA"}]) != ig.weather_key([{**base, "precip_code": "RA"}])


def test_flight_categories():
    assert ig.flight_category(None, 10) == "VFR"
    assert ig.flight_category(2000, 10) == "MVFR"
    assert ig.flight_category(800, 10) == "IFR"
    assert ig.flight_category(10000, 0.5) == "LIFR"
    assert ig.flight_category(None, None) == "UNKNOWN"
