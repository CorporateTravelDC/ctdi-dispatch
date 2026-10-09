"""
2026-10-08: airplanes.live reinstated as a FALLBACK-ONLY lookup (operator; feeder
whitelisted). These tests pin the contract: off by default, local sources always
first, LADD identifiers never sent, rate limited and cached, hex-only tracking
URLs, never raises. No network: requests.get is replaced. Identifiers are
synthetic (Q registry / unassigned hexes).
"""
from __future__ import annotations

import pytest

from common import airplanes_live as al
from shared import watchlist as wl


class _Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


@pytest.fixture
def api(monkeypatch):
    calls = []
    state = {"payload": {"ac": [{"hex": "0f1e2d", "flight": "QQQ123  ", "r": "Q7K2M", "lat": 39.0, "lon": -77.0,
                                 "alt_baro": 32000, "gs": 420.0, "seen_pos": 0.4}]}}

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _Resp(state["payload"])
    monkeypatch.setattr(al.requests, "get", fake_get)
    monkeypatch.setattr(al, "MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(al, "_ladd_listed", lambda ident, kind: False)
    al._cache.clear()
    monkeypatch.setenv("AIRPLANES_LIVE_FALLBACK", "1")
    return {"calls": calls, "state": state}


def test_off_by_default_never_calls_the_network(monkeypatch, api):
    monkeypatch.delenv("AIRPLANES_LIVE_FALLBACK", raising=False)
    monkeypatch.setattr(al.config, "get", lambda k, d="": d)
    assert al.by_hex("0f1e2d") is None and al.by_callsign("QQQ123") is None
    assert api["calls"] == []


def test_lookups_return_a_normalized_contact_tagged_with_its_source(api):
    ac = al.by_hex("0F1E2D")
    assert ac["hex"] == "0f1e2d" and ac["flight"] == "QQQ123" and ac["_source"] == "airplanes_live"
    assert api["calls"][-1].endswith("/hex/0f1e2d")
    assert al.by_callsign("qqq 123")["hex"] == "0f1e2d"


def test_a_callsign_answer_for_a_different_flight_is_rejected(api):
    assert al.by_callsign("QQQ999") is None


def test_ladd_listed_identifiers_are_never_sent(monkeypatch, api):
    monkeypatch.setattr(al, "_ladd_listed", lambda ident, kind: True)
    assert al.by_hex("0f1e2d") is None and al.by_callsign("QQQ123") is None
    assert api["calls"] == []


def test_ladd_check_failure_fails_closed(monkeypatch, api):
    monkeypatch.undo()
    al._cache.clear()
    import common.db as db
    monkeypatch.setattr(db, "faa_lookup_by_hex", lambda h: (_ for _ in ()).throw(RuntimeError("db down")))
    assert al._ladd_listed("0f1e2d", "hex") is True


def test_cached_and_stale_positions_and_errors(api):
    al.by_hex("0f1e2d"); al.by_hex("0f1e2d")
    assert len(api["calls"]) == 1                                     # cached
    al._cache.clear()
    api["state"]["payload"] = {"ac": [{"hex": "0f1e2d", "flight": "QQQ123", "lat": 1, "lon": 1, "seen_pos": 600}]}
    assert al.by_hex("0f1e2d") is None                                # stale position ignored
    al._cache.clear()
    api["state"]["payload"] = None
    assert al.by_hex("0f1e2d") is None                                # malformed reply: no raise


def test_tracking_url_is_hex_only():
    assert al.tracking_url("0F1E2D") == "https://globe.airplanes.live/?icao=0f1e2d"
    assert al.tracking_url("QQQ123") == "" and al.tracking_url(None) == ""


def test_resolution_asks_local_sources_first_and_the_api_only_when_they_are_dark(monkeypatch, api):
    order = []
    monkeypatch.setattr(wl, "_local_ac_by_callsign", lambda cs: order.append("local-adsb") or None)
    monkeypatch.setattr(wl, "_local_registry_hex_lookup", lambda cs: order.append("registry") or None)
    monkeypatch.setattr(wl, "_local_fdps_ac", lambda cs: order.append("fdps") or None)
    monkeypatch.setattr(wl.db, "set_watchlist_identity", lambda *a, **k: None)
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda *a, **k: order.append("notify"))
    ac = wl.resolve_flight_identity({"id": "wl-test", "notes": ""}, "QQQ123")
    assert order[:3] == ["local-adsb", "registry", "fdps"] and ac["_source"] == "airplanes_live"
    assert order[-1] == "notify"


def test_a_local_contact_means_the_api_is_never_asked(monkeypatch, api):
    monkeypatch.setattr(wl, "_local_ac_by_hex", lambda h: {"hex": h, "r": "Q7K2M", "alt_baro": 1000, "gs": 150})
    ac = wl.resolve_flight_identity({"id": "wl-test", "hex_id": "0f1e2d", "notes": ""}, "QQQ123")
    assert ac["hex"] == "0f1e2d" and "_source" not in ac
    assert api["calls"] == []
