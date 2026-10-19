"""2026-10-18 (operator: TFR widening). Every VIP and SECURITY type TFR gets the detail
window treatment; the VIP-class push goes to type VIP, and to type SECURITY with
presidential language, the 40103(b)(3) / 99.7 legal basis, or a ZDC facility (the
operator's same-day change from a 30 nm DCA/IAD/BWI radius to all of Washington
Center). Fixtures are synthetic, shaped like the FAA list item and detail XML."""
import json
import time
from pathlib import Path

import pytest

import common.db as db
from common import tfr_geo
from poller.fetchers import tfr

FIX = Path(__file__).resolve().parents[1] / "fixtures/tfr"


def _x(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _item(nid, typ, fac, state, desc="Somewhere, Tuesday Local", mod="202610181533"):
    return {"notam_id": nid, "facility": fac, "state": state, "type": typ, "description": desc,
            "mod_date": "10/18/2026 15:33:00", "mod_abs_time": mod, "is_new": "Y", "gid": nid}


def _fetch(monkeypatch, items, details):
    """tfr.fetch() against a fake FAA: `details` maps notam id -> detail XML text."""
    calls = []

    class L:
        def raise_for_status(self): pass
        def json(self): return items

    class R:
        def __init__(self, body): self.content = body.encode("utf-8")
        def raise_for_status(self): pass

    def get(url, **kw):
        calls.append(url)
        if url == tfr.TFR_URL:
            return L()
        nid = url.rsplit("detail_", 1)[1].removesuffix(".xml").replace("_", "/")
        if nid not in details:
            raise OSError("no detail")
        return R(details[nid])

    tfr._detail_cache.clear()
    monkeypatch.setattr(tfr.requests, "get", get)
    return {r.tfr_id: r for r in tfr.fetch()}, calls


# The same stadium event filed by Washington Center (facility decides; no geometry needed).
ZDC_STADIUM = _x("detail_security_stadium_synthetic.xml").replace(
    "<codeFacility>ZNY</codeFacility>", "<codeFacility>ZDC</codeFacility>")


# ── classification ────────────────────────────────────────────────────────────

def test_vip_type_pushes_and_gets_window(monkeypatch):
    recs, _ = _fetch(monkeypatch, [_item("6/9999", "VIP", "ZTL", "NC")],
                     {"6/9999": _x("detail_vip_synthetic.xml")})
    r = recs["6/9999"]
    assert r.vip is True and r.vip_push_reason == "vip_type"
    assert r.effective_start == 1792084500.0 and r.effective_end == 1792109700.0


def test_security_presidential_pushes(monkeypatch):
    recs, _ = _fetch(monkeypatch, [_item("6/9002", "SECURITY", "ZOB", "OH")],
                     {"6/9002": _x("detail_security_presidential_synthetic.xml")})
    r = recs["6/9002"]
    assert r.vip is False                                   # is_vip keeps its old meaning
    assert r.vip_push_reason == "security_presidential"
    assert r.effective_start is not None


def test_security_legal_basis_outside_zdc_is_the_p3_tier(monkeypatch):
    recs, _ = _fetch(monkeypatch, [_item("6/9001", "SECURITY", "ZMA", "FL")],
                     {"6/9001": _x("detail_security_legal_synthetic.xml")})
    r = recs["6/9001"]
    raw = json.loads(r.raw_json)
    assert r.vip_push_reason == "security_legal_basis"
    assert raw["vip_push_reasons"] == ["security_legal_basis"]   # the UAS boilerplate is not "presidential"
    assert "zdc_basis" not in raw and "residence" not in raw
    assert tfr.push_tier(raw["vip_push_reasons"]) == "p3"      # tiered: no hot push
    assert raw["areas_geo"][0]["radius_nm"] == pytest.approx(1.0, abs=0.05)


def test_security_stadium_outside_zdc_window_treated_no_push(monkeypatch):
    recs, calls = _fetch(monkeypatch, [_item("6/9003", "SECURITY", "ZNY", "NY")],
                         {"6/9003": _x("detail_security_stadium_synthetic.xml")})
    r = recs["6/9003"]
    raw = json.loads(r.raw_json)
    assert r.vip_push_reason is None and r.vip is False
    assert r.effective_start is not None and r.effective_end is not None     # window treated
    assert len(raw["area_windows_utc"]) == 2 and len(raw["areas_geo"]) == 2
    assert sum("detail_" in u for u in calls) == 1


def test_security_stadium_outside_zdc_with_legal_text_is_p3_only(monkeypatch):
    legal = ("PURSUANT TO 49 USC 40103(B)(3), THE FAA CLASSIFIES THE AIRSPACE DEFINED IN THIS NOTAM "
             "AS 'NTL DEFENSE AIRSPACE'.")
    xml = _x("detail_security_stadium_synthetic.xml").replace("FOR A SPORTING EVENT.", "FOR A SPORTING EVENT. " + legal)
    recs, _ = _fetch(monkeypatch, [_item("6/9013", "SECURITY", "ZNY", "NY")], {"6/9013": xml})
    raw = json.loads(recs["6/9013"].raw_json)
    assert raw["vip_push_reasons"] == ["security_legal_basis"]
    assert tfr.push_tier(raw["vip_push_reasons"]) == "p3"


def test_security_residence_is_hot_by_city_text_and_by_geometry(monkeypatch):
    xml = (_x("detail_security_legal_synthetic.xml")
           .replace("<txtNameCity>Synthetic Beach</txtNameCity>", "<txtNameCity>Palm Beach</txtNameCity>"))
    recs, _ = _fetch(monkeypatch, [_item("6/0100", "SECURITY", "ZMA", "FL",
                                         desc="Palm Beach, FL, Tuesday, October 20, 2026 through "
                                              "Wednesday, October 20, 2027 Local")], {"6/0100": xml})
    raw = json.loads(recs["6/0100"].raw_json)
    assert raw["vip_push_reasons"] == ["security_residence", "security_legal_basis"]
    assert raw["residence"] == "Palm Beach, FL" and recs["6/0100"].vip_push_reason == "security_residence"
    assert tfr.push_tier(raw["vip_push_reasons"]) == "hot"
    # geometry alone (a centre within the residence radius, city text elsewhere)
    near_bedminster = {"city": "Somewhere", "state": "NEW JERSEY",
                       "areas_geo": [{"lat": 40.66, "lon": -74.67, "radius_nm": 1}]}
    assert tfr.protected_residence({"state": "NJ"}, near_bedminster) == "Bedminster, NJ"
    assert tfr.protected_residence({"description": "WEST PALM BEACH, FL, ..."},
                                   {"city": "West Palm Beach", "state": "FLORIDA", "areas_geo": []}) is None
    assert [r["name"] for r in tfr.PROTECTED_RESIDENCES] == ["Palm Beach, FL", "Bedminster, NJ"]


def test_tiers_by_reason():
    assert tfr.push_tier(["vip_type"]) == "hot"
    assert tfr.push_tier(["vip_text"]) == "hot"
    assert tfr.push_tier(["security_presidential", "security_legal_basis"]) == "hot"
    assert tfr.push_tier(["security_zdc", "security_legal_basis"]) == "hot"
    assert tfr.push_tier(["security_residence"]) == "hot"
    assert tfr.push_tier(["security_legal_basis"]) == "p3"
    assert tfr.push_tier([]) is None


def test_security_stadium_in_zdc_pushes_and_zny_does_not(monkeypatch):
    recs, _ = _fetch(monkeypatch, [_item("6/9005", "SECURITY", "ZDC", "MD"),
                                   _item("6/9003", "SECURITY", "ZNY", "NY")],
                     {"6/9005": ZDC_STADIUM, "6/9003": _x("detail_security_stadium_synthetic.xml")})
    assert recs["6/9005"].vip_push_reason == "security_zdc"
    assert json.loads(recs["6/9005"].raw_json)["zdc_basis"] == "facility"
    assert recs["6/9003"].vip_push_reason is None


def test_facility_decides_before_geometry():
    """An ARTCC facility on the record is final: a ZNY TFR whose centre sits in the
    coarse ZDC box is still not ZDC, and a ZDC TFR needs no geometry at all."""
    in_box = {"areas_geo": [{"lat": 38.9, "lon": -77.0, "radius_nm": 1}]}
    assert tfr.zdc_basis({"facility": "ZNY"}, in_box) is None
    assert tfr.zdc_basis({"facility": "ZDC"}, None) == "facility"
    assert tfr.zdc_basis({"facility": "KZDC"}, None) == "facility"


def test_no_facility_falls_back_to_geometry_then_text(monkeypatch):
    in_box = {"areas_geo": [{"lat": 38.9, "lon": -77.0, "radius_nm": 1}]}
    out_box = {"areas_geo": [{"lat": 26.7, "lon": -80.0, "radius_nm": 1}]}
    assert tfr.zdc_basis({"facility": "FDC"}, in_box) == "geometry_approx"
    assert tfr.zdc_basis({"facility": "FDC"}, out_box) is None
    # no geometry at all: the text decides (state / "WASHINGTON, DC")
    recs, _ = _fetch(monkeypatch, [_item("6/9004", "SECURITY", "FDC", "USA", desc="WASHINGTON, DC., USA")],
                     {"6/9004": _x("detail_security_nogeo_synthetic.xml")})
    r = recs["6/9004"]
    raw = json.loads(r.raw_json)
    assert "areas_geo" not in raw
    assert raw["zdc_basis"] == "text" and r.vip_push_reason == "security_zdc"
    assert tfr.zdc_basis({"facility": "FDC", "state": "USA", "description": "Special Notice, USA"},
                         {"areas_geo": [], "state": "UNITED STATES"}) is None


def test_detail_failure_keeps_list_facility_rule_and_is_retried(monkeypatch):
    recs, _ = _fetch(monkeypatch, [_item("6/9006", "SECURITY", "ZDC", "VA"),
                                   _item("6/9007", "SECURITY", "ZMA", "FL")], {})
    assert recs["6/9006"].vip_push_reason == "security_zdc"      # facility alone
    assert recs["6/9007"].vip_push_reason is None                 # legal basis unknown yet
    assert "6/9006" not in tfr._detail_cache                      # retried next poll


def test_non_detail_types_unchanged(monkeypatch):
    recs, calls = _fetch(monkeypatch, [_item("6/1111", "HAZARDS", "ZDC", "VA")], {})
    assert recs["6/1111"].vip_push_reason is None and recs["6/1111"].effective_start is None
    assert not any("detail_" in u for u in calls)


def test_geometry_polygon_and_point_tests():
    import xml.etree.ElementTree as ET
    root = ET.fromstring(_x("detail_security_stadium_synthetic.xml").encode())
    areas = tfr_geo.parse_areas(root)
    assert [a["name"] for a in areas] == ["Stadium", "Lot"]
    a = areas[0]
    assert a["lat"] == pytest.approx(40.83, abs=0.01) and a["radius_nm"] == pytest.approx(3.0, abs=0.1)
    assert tfr_geo.point_in_area(40.83, -73.93, a)
    assert not tfr_geo.point_in_area(40.95, -73.93, a)                 # ~7 nm north
    assert tfr_geo.point_in_area(40.95, -73.93, a, buffer_nm=30)


def test_upsert_stores_reason(tmp_path):
    db.init_db_all()
    db.upsert_tfr("6/9001", json.dumps({"type": "SECURITY"}), False, 1.0, 2e9,
                  vip_push_reason="security_legal_basis")
    row = next(t for t in db.get_active_tfrs() if t["tfr_id"] == "6/9001")
    assert row["vip_push_reason"] == "security_legal_basis" and row["is_vip"] == 0


# ── pusher ────────────────────────────────────────────────────────────────────

@pytest.fixture
def pm(monkeypatch):
    db.init_db_all()
    import pusher.main as pm
    sent, hot = [], []
    monkeypatch.setattr(pm, "send_ntfy",
                        lambda topic, msg, priority=3, *, title: sent.append((topic, priority, title, msg)) or True)
    monkeypatch.setattr(pm, "hot_push", lambda topic, msg, title: hot.append((topic, title, msg)) or True)
    monkeypatch.setattr(db, "mark_tfr_notified", lambda tid: None)
    pm._sent, pm._hot = sent, hot
    return pm


def _row(tid, typ, reasons, start=None, end=None, mod="1", is_vip=0, desc=None):
    now = time.time()
    raw = {"type": typ, "description": desc or f"Place {tid}", "mod_abs_time": mod,
           "class_v": "2026-10-18", "vip_push_reasons": reasons}
    return {"tfr_id": tid, "is_vip": is_vip, "vip_push_reason": reasons[0] if reasons else None,
            "effective_start": start if start is not None else now + 3600,
            "effective_end": end if end is not None else now + 7200, "raw_json": json.dumps(raw)}


def _landed(monkeypatch, at):
    monkeypatch.setattr(db, "get_feed_states", lambda: [{"feed_name": "tfr", "fetched_at": at, "error": None}])


def test_cutover_silences_existing_security_then_new_ones_push(pm, monkeypatch):
    tfrs = [_row("6/0001", "SECURITY", ["security_legal_basis"]),
            _row("6/0002", "SECURITY", []),
            _row("6/0003", "VIP", ["vip_type"], is_vip=1)]
    monkeypatch.setattr(db, "get_active_tfrs", lambda: tfrs)
    _landed(monkeypatch, 0)                                   # no poller run since the pusher started
    assert pm.tfr_security_cutover() == 0
    assert pm.push_vip_tfrs() == 1                            # VIP pushes as before; SECURITY waits
    assert [h[1] for h in pm._hot] == ["VIP TFR: 6/0003", "VIP TFR: 6/0003"]
    _landed(monkeypatch, time.time() + 1)
    assert pm.tfr_security_cutover() == 2                     # both SECURITY rows baselined, no push
    assert pm.tfr_security_cutover() == 0                     # once, ever
    pm._hot.clear()
    assert pm.push_vip_tfrs() == 0 and pm._hot == []
    tfrs.append(_row("6/0004", "SECURITY", ["security_legal_basis", "security_zdc"]))
    assert pm.push_vip_tfrs() == 1
    topics = sorted(h[0] for h in pm._hot)
    assert topics == ["hot-alerts", "tfr-alert"]
    msg = pm._hot[0][2]
    assert pm._hot[0][1] == "SECURITY TFR: 6/0004"
    assert "\nWhy: SECURITY TFR under 49 USC 40103(b)(3)" in msg
    assert "; SECURITY TFR in Washington Center (ZDC) airspace -- affects DCA/IAD/BWI" in msg
    assert " ET " in msg and "Z / " in msg                    # UTC + ET window


def test_dedup_unchanged_text_and_mod_time_only(pm, monkeypatch):
    a = _row("6/0005", "SECURITY", ["security_legal_basis"])
    b = dict(a, vip_push_reason="security_zdc",
             raw_json=json.dumps({**json.loads(a["raw_json"]), "vip_push_reasons": ["security_zdc"],
                                  "areas_geo": [{"lat": 1, "lon": 2}]}))
    assert pm._vip_tfr_alert(a)[1] == pm._vip_tfr_alert(b)[1]            # reasons/extras never hashed
    c = dict(a, raw_json=json.dumps({**json.loads(a["raw_json"]), "mod_abs_time": "2"}))
    assert pm._vip_tfr_alert(a)[1] != pm._vip_tfr_alert(c)[1]            # an FAA change re-alerts
    from common import config
    import pathlib
    (pathlib.Path(config.state_dir()) / pm.TFR_SECURITY_CUTOVER_MARKER).write_text("x\n")
    (pathlib.Path(config.state_dir()) / pm.TFR_WIDEN_FIX_MARKER).write_text("x\n")
    tfrs = [a]
    monkeypatch.setattr(db, "get_active_tfrs", lambda: tfrs)
    assert pm.push_vip_tfrs() == 1
    tfrs[0] = b
    assert pm.push_vip_tfrs() == 0                                       # same text + mod time
    tfrs[0] = c
    assert pm.push_vip_tfrs() == 1


def test_palm_beach_correction_fires_once(pm, monkeypatch):
    pb = _row("6/0100", "SECURITY", [], desc="Palm Beach, FL, Tuesday, October 20, 2026 through "
                                             "Wednesday, October 20, 2027 Local")
    pb["effective_start"] = pb["effective_end"] = None
    tfrs = [pb]
    monkeypatch.setattr(db, "get_active_tfrs", lambda: tfrs)
    _landed(monkeypatch, time.time() + 1)
    pm.tfr_security_cutover()                                 # 6/0100 is never baselined
    assert pm.tfr_security_widen_corrections() == 0 and pm._sent == []   # not classified / no window yet
    tfrs[0] = _row("6/0100", "SECURITY", ["security_legal_basis"], start=1792497660.0, end=1824033600.0,
                   desc="Palm Beach, FL, Tuesday, October 20, 2026 through Wednesday, October 20, 2027 Local")
    assert pm.tfr_security_widen_corrections() == 0         # p3 tier only: the correction waits
    tfrs[0] = _row("6/0100", "SECURITY", ["security_residence", "security_legal_basis"],
                   start=1792497660.0, end=1824033600.0,
                   desc="Palm Beach, FL, Tuesday, October 20, 2026 through Wednesday, October 20, 2027 Local")
    assert pm.push_vip_tfrs() == 0                            # held for its correction
    assert pm.tfr_security_widen_corrections() == 1
    assert sorted((s[0], s[1]) for s in pm._sent) == [("hot-alerts", 4), ("tfr-alert", 4)]
    title, msg = pm._sent[0][2], pm._sent[0][3]
    assert title == "SECURITY TFR: 6/0100 (corrected)"
    assert msg.startswith("CORRECTED:") and "40103(b)(3)" in msg and "protected residence" in msg
    assert "Active 12:01Z / 08:01 ET Tue 10-20 to 12:00Z / 08:00 ET Wed 10-20" in msg
    from common import config
    import pathlib
    assert (pathlib.Path(config.state_dir()) / pm.TFR_WIDEN_FIX_MARKER).exists()
    pm._sent.clear()
    assert pm.tfr_security_widen_corrections() == 0 and pm._sent == []   # once, ever
    assert pm.push_vip_tfrs() == 0 and pm._hot == []                      # recorded in the dedup


def test_window_lists_each_area():
    import pusher.main as pm
    t = {"effective_start": 1792084500.0, "effective_end": 1792109700.0,
         "raw_json": json.dumps({"area_windows_utc": [[1792084500.0, 1792092600.0]]})}
    assert "Area windows: 17:15Z / 13:15 ET Thu 10-15 to 19:30Z / 15:30 ET Thu 10-15" in pm._tfr_window(t)
    t["raw_json"] = json.dumps({"area_windows_utc": [[1792084500.0, 1792109700.0]]})
    assert "Area windows" not in pm._tfr_window(t)          # one area == the overall window


def test_pusher_tiers_hot_vs_p3(pm, monkeypatch):
    from common import config
    import pathlib
    (pathlib.Path(config.state_dir()) / pm.TFR_SECURITY_CUTOVER_MARKER).write_text("x\n")
    (pathlib.Path(config.state_dir()) / pm.TFR_WIDEN_FIX_MARKER).write_text("x\n")
    tfrs = [_row("6/0201", "SECURITY", ["security_legal_basis"]),          # outside ZDC, legal only
            _row("6/0202", "SECURITY", ["security_presidential", "security_legal_basis"]),
            _row("6/0203", "SECURITY", ["security_zdc"]),
            _row("6/0204", "SECURITY", ["security_residence", "security_legal_basis"]),
            _row("6/0205", "VIP", ["vip_type"], is_vip=1),
            _row("6/0206", "SPECIAL", ["vip_text"], is_vip=1)]
    monkeypatch.setattr(db, "get_active_tfrs", lambda: tfrs)
    assert pm.push_vip_tfrs() == 6
    hot_ids = sorted({h[1].split(": ")[1] for h in pm._hot})
    assert hot_ids == ["6/0202", "6/0203", "6/0204", "6/0205", "6/0206"]
    assert all(sorted(h[0] for h in pm._hot if h[1].endswith(tid)) == ["hot-alerts", "tfr-alert"]
               for tid in hot_ids)
    assert [(s[0], s[1], s[2]) for s in pm._sent] == [("tfr-alert", 3, "SECURITY TFR: 6/0201")]
    assert "Why: SECURITY TFR under 49 USC 40103(b)(3)" in pm._sent[0][3]
