"""2026-10-13 (operator): VIP TFRs are nationwide. The FAA marks them type "VIP" with a plain
place/date description, so the text patterns never matched one (69 rows, 0 is_vip, 9 active VIP
TFRs). DC area keeps the hot path (p5 + Pushover + hourly heartbeat); elsewhere one p4
tfr-alert per new or changed TFR. CRANE is a flight identifier only."""
import json

import pusher.main as pm
from poller.fetchers import tfr
from ingest import local_airspace as la


def test_faa_type_vip_is_vip_without_any_text_match():
    assert tfr.is_vip("Anchorage, AK, Tuesday, October 13, 2026", "VIP")
    assert not tfr.is_vip("Anchorage, AK, hazards", "HAZARDS")
    assert tfr.is_vip("MOVEMENT OF VIP", "SECURITY")


def _row(tid, facility, state="AK"):
    return {"tfr_id": tid, "is_vip": 1, "enriched_text": None,
            "raw_json": json.dumps({"facility": facility, "state": state, "type": "VIP"})}


def test_dc_area_is_hot_and_elsewhere_is_one_p4(monkeypatch):
    hot, normal = [], []
    monkeypatch.setattr(pm.db, "get_active_tfrs", lambda: [_row("6/1", "ZDC", "DC"), _row("6/2", "ZAN")])
    monkeypatch.setattr(pm.db, "mark_tfr_notified", lambda t: None)
    monkeypatch.setattr(pm, "hot_push", lambda topic, m, title: hot.append((topic, title)) or True)
    monkeypatch.setattr(pm, "send_ntfy", lambda topic, m, priority=3, *, title: normal.append((topic, priority, title)) or True)
    seen = set()
    monkeypatch.setattr(pm._tfr_dedup, "should_push_periodic", lambda k, h: True)
    monkeypatch.setattr(pm._tfr_dedup, "should_push", lambda k, h: k not in seen)
    monkeypatch.setattr(pm._tfr_dedup, "record", lambda k, h: seen.add(k))
    assert pm.push_vip_tfrs() == 2
    assert ("tfr-alert", "VIP TFR: 6/1") in hot and ("hot-alerts", "VIP TFR: 6/1") in hot
    assert normal == [("tfr-alert", 4, "VIP TFR AK ZAN: 6/2")]
    pm.push_vip_tfrs()
    assert len(normal) == 1                                              # no hourly repeat outside DC


def test_crane_sightings_are_not_labelled_marine_one():
    assert la._vip_local_kind("CRANE01", None).startswith("CRANE")
    assert la._vip_local_kind("MARINE1", None) == "Marine One"
    assert la._vip_local_kind("VENUS", None) == "VIP callsign VENUS"
