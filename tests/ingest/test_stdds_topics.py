"""2026-10-13 (operator): STDDS alerts go to the ZONE topic for every tracked airport, plus an
airport topic only inside the priority centers (Washington, New York, Atlanta, Cleveland).
Before: taxi alerts went to bare airport topics everywhere (stdds-lax, stdds-sea ...), DC
airports never reached stdds-zdc, and the documented zone topics were dead."""
import pytest

from ingest.parsers import smes_parser as sp
from shared import sector_coalesce as sc


@pytest.mark.parametrize("apt,zone,extra", [
    ("KDCA", "zdc", ("stdds-dca",)), ("KIAD", "zdc", ("stdds-iad",)), ("KJFK", "zny", ("stdds-jfk",)),
    ("KATL", "zatl", ("stdds-atl",)), ("KCLE", "zob", ("stdds-cle",)), ("KDTW", "zob", ("stdds-dtw",)),
    ("KLAX", "zla", ()), ("KSEA", "zse", ()), ("KIAH", "zhu", ()), ("KCVG", "zid", ()), ("KORD", None, ()),
])
def test_zone_for_every_tracked_airport_and_airport_topics_only_in_priority_zones(apt, zone, extra):
    assert sp._stdds_sector_for(apt) == zone
    assert sp._stdds_airport_topics(apt, zone) == extra


def test_taxi_alert_uses_the_zone_not_the_bare_airport(monkeypatch):
    seen = {}
    monkeypatch.setattr(sp.db, "count_onsurface", lambda a: 40)
    monkeypatch.setattr(sp._STDDS_TAXI_DEDUP, "should_push", lambda *a: True)
    monkeypatch.setattr(sp._STDDS_TAXI_DEDUP, "record", lambda *a: None)
    monkeypatch.setattr(sc, "fire_family_alert", lambda *a, **k: seen.update(k) or {})
    sp.check_taxi_alerts({"airport": "KLAX"})
    assert seen["sector_override"] == "zla" and seen["extra_topics"] == ()
    sp.check_taxi_alerts({"airport": "KLGA"})
    assert seen["sector_override"] == "zny" and seen["extra_topics"] == ("stdds-lga",)


def test_extra_topics_fire_once_each_alongside_the_zone(monkeypatch):
    sent, dual = [], []
    import shared.watchlist as wl
    from common import ntfy_push
    monkeypatch.setattr(wl, "_fire_ntfy_dual", lambda topic, *a, **k: dual.append(topic))
    monkeypatch.setattr(ntfy_push, "send", lambda topic, *a, **k: sent.append(topic) or True)
    monkeypatch.setattr(sc, "_throttle_allows", lambda t: True)
    monkeypatch.setattr(sc, "_record_fire", lambda t: None)
    monkeypatch.setattr(sc, "record_event", lambda *a, **k: {"sector_silenced": False, "feed_silenced": False,
                                                              "escalating": True})
    r = sc.fire_family_alert("stdds", "stdds_taxi", "KDCA", "t", "d", "x", sector_override="zdc",
                             extra_topics=("stdds-dca",))
    assert dual == ["stdds-alerts", "stdds-zdc"] and sent == ["stdds-dca"] and r["extra_fired"] == ["stdds-dca"]
