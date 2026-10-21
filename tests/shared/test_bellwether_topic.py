"""
2026-10-20 (operator): bellwether pushes go to `bellwether-alerts` only -- never
flight-alerts, never a `dispatch` duplicate -- at every watch-keyed push site
(shared.watch_topic). VIP-listed and ordinary flight watches are unchanged.
Also checks the bellwether records in watchlists/permanent_flights.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from common import db

REPO = Path(__file__).resolve().parents[2]
PERM_FILE = REPO / "watchlists" / "permanent_flights.json"


def _perm(ident: str, *, alert_topic: str | None, notes: str = "", route_name: str | None = None) -> dict:
    return {"id": f"perm-flight-{ident.lower()}-test", "entry_type": "flight", "tier": "permanent",
            "identifier": ident, "origin": "EGLL", "destination": "KIAD", "route_name": route_name,
            "scheduled_departure": None, "scheduled_arrival": None, "auto_remove_at": None,
            "added_at": "2026-10-20T00:00:00Z", "added_by": "operator", "notes": notes,
            "last_event_at": None, "last_event_summary": None, "alert_topic": alert_topic}


@pytest.fixture
def no_file(monkeypatch, tmp_path):
    """Containers without the watchlists volume (ingest, pusher): the DB row decides."""
    import shared.vip_listed as vl
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", tmp_path / "no-watchlists")
    vl._cache.update(mtime=None, by_id={})
    db.init_db_all()
    yield
    vl._cache.update(mtime=None, by_id={})


@pytest.fixture
def with_file(monkeypatch, tmp_path):
    """Poller / web: the permanent file itself decides, matched by entry id."""
    import shared.vip_listed as vl
    d = tmp_path / "watchlists"
    d.mkdir()
    recs = [_perm("BA9293", alert_topic="bellwether-alerts"),
            _perm("25-9200", alert_topic=None, route_name="VC-25B", notes="New AF1. Hex: 000000")]
    (d / "permanent_flights.json").write_text(json.dumps({"watchlist": recs}))
    monkeypatch.setattr(vl, "PERMANENT_WATCHLIST_DIR", d)
    vl._cache.update(mtime=None, by_id={})
    db.init_db_all()
    yield recs
    vl._cache.update(mtime=None, by_id={})


def _fire(entry: dict) -> list[tuple[str, str]]:
    from shared import watchlist as wl
    sent: list[tuple[str, str]] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_post(url, data=None, **kw):
        sent.append((url.rsplit("/", 1)[-1], (data or b"").decode() if isinstance(data, bytes) else str(data)))
        return _Resp()

    with patch("shared.watchlist.requests.post", side_effect=fake_post):
        wl.watchlist_event_hit(entry["id"], "OFF -- airborne", {"watchlist_trigger": "oooi_off"}, priority=4)
    return sent


# ── helper ───────────────────────────────────────────────────────────────────

def test_topic_helper_file_and_db_fallback(no_file):
    from shared import watch_topic as wt
    bw = _perm("BA9293", alert_topic="bellwether-alerts")
    assert wt.topic_for_entry(bw) == "bellwether-alerts"
    assert wt.topic_for_entry({**bw, "tier": "transient"}) == "flight-alerts"     # never inherited
    assert wt.topic_for_entry(_perm("BA9294", alert_topic=None)) == "flight-alerts"
    assert wt.topic_for_entry(_perm("BA9295", alert_topic="dispatch")) == "flight-alerts"   # not allowed
    assert wt.topic_for_entry({"entry_type": "drone"}) == "drone-alerts"
    assert not wt.sends_dispatch_copy("bellwether-alerts") and wt.sends_dispatch_copy("flight-alerts")


def test_topic_helper_reads_the_file_by_id_when_mounted(with_file):
    from shared import watch_topic as wt
    bw, vip = with_file
    # the DB copy says nothing; the file decides
    assert wt.topic_for_entry({**bw, "alert_topic": None}) == "bellwether-alerts"
    assert wt.topic_for_entry(vip) == "flight-alerts"


# ── watchlist_event_hit ──────────────────────────────────────────────────────

def test_bellwether_watch_never_publishes_to_flight_alerts_or_dispatch(no_file):
    bw = _perm("BA9293", alert_topic="bellwether-alerts")
    db.upsert_watchlist_entry(bw)
    sent = _fire(bw)
    assert [t for t, _ in sent] == ["bellwether-alerts"]


def test_vip_and_ordinary_watches_unchanged(with_file):
    _bw, vip = with_file
    db.upsert_watchlist_entry(vip)
    assert sorted(t for t, _ in _fire(vip)) == ["dispatch", "flight-alerts"]
    plain = {**_perm("UA9919", alert_topic=None), "tier": "transient", "id": "wl-flight-ual9919-test"}
    db.upsert_watchlist_entry(plain)
    assert sorted(t for t, _ in _fire(plain)) == ["dispatch", "flight-alerts"]


def test_file_watcher_copies_alert_topic_and_transient_upsert_keeps_it(no_file):
    bw = _perm("BA9293", alert_topic="bellwether-alerts")
    db.upsert_watchlist_entry(bw)
    db.upsert_watchlist_entry({**bw, "tier": "transient", "alert_topic": None})
    row = next(e for e in db.get_watchlist_entries() if e["id"] == bw["id"])
    assert row["alert_topic"] == "bellwether-alerts"


# ── pusher ───────────────────────────────────────────────────────────────────

def test_pusher_retry_of_a_bellwether_row_goes_to_bellwether_alerts_only(no_file, monkeypatch):
    import pusher.main as pm
    bw = _perm("BA9293", alert_topic="bellwether-alerts")
    db.upsert_watchlist_entry(bw)
    monkeypatch.setattr(db, "get_watchlist_history_unfired", lambda max_age_seconds=900: [
        {"id": 1, "entry_id": bw["id"], "entry_type": "flight", "identifier": "BA9293",
         "event_summary": "landed", "ntfy_priority": 3},
        {"id": 2, "entry_id": "wl-flight-ual9919-test", "entry_type": "flight", "identifier": "UAL9919",
         "event_summary": "landed", "ntfy_priority": 3}])
    monkeypatch.setattr(db, "mark_watchlist_history_fired", lambda rid: None)
    sent = []
    monkeypatch.setattr(pm, "send_ntfy",
                        lambda topic, msg, priority=3, *, title: sent.append((topic, title)) or True)
    assert pm.push_watchlist_retries() == 2
    assert [t for t, _ in sent] == ["bellwether-alerts", "flight-alerts", "dispatch"]


def test_pusher_landing_of_a_bellwether_goes_to_bellwether_alerts(no_file, monkeypatch):
    import pusher.main as pm
    bw = _perm("BA9293", alert_topic="bellwether-alerts")
    monkeypatch.setattr(db, "get_active_watchlists", lambda: [])
    monkeypatch.setattr(db, "get_watchlist_entries", lambda entry_type=None: [bw])
    monkeypatch.setattr(pm, "_check_flight_landing", lambda cs, **kw: {"source": "test"})

    class _Dedup:
        def should_push_periodic(self, *a):
            return True

        def record(self, *a):
            return None

    monkeypatch.setattr(pm, "_landing_dedup_for", lambda result: _Dedup())
    sent = []
    monkeypatch.setattr(pm, "send_ntfy", lambda topic, message, priority=3, *, title: sent.append(topic) or True)
    assert pm.push_flight_watchlist_landings() == 1
    assert sent == ["bellwether-alerts"]


# ── web remove ───────────────────────────────────────────────────────────────

def test_web_remove_of_a_bellwether_goes_to_bellwether_alerts(no_file):
    import asyncio
    import web.routes.watchlist as wr
    bw = _perm("BA9293", alert_topic="bellwether-alerts")
    db.upsert_watchlist_entry(bw)
    fired = []
    with patch.object(wr, "_fire_ntfy_dual", side_effect=lambda **kw: fired.append(kw["domain_topic"])):
        asyncio.run(wr.remove_watchlist_entry(bw["id"], tier=None))
    assert fired == ["bellwether-alerts"]


def test_click_map_has_bellwether_alerts():
    from common import ntfy_push
    assert ntfy_push.TOPIC_CLICK["bellwether-alerts"] == ntfy_push.TOPIC_CLICK["flight-alerts"]


# ── the permanent file ───────────────────────────────────────────────────────

def test_permanent_bellwether_records():
    from shared.vip_listed import is_vip_record
    recs = json.loads(PERM_FILE.read_text())["watchlist"]
    by_id = {r["id"]: r for r in recs}
    bell = [r for r in recs if r.get("alert_topic")]
    assert len(bell) >= 40 and by_id["perm-flight-ba293"]["alert_topic"] == "bellwether-alerts"
    for r in bell:
        assert r["alert_topic"] == "bellwether-alerts", r["id"]
        assert re.fullmatch(r"[A-Z0-9]{2}\d{1,4}", r["identifier"]), r["identifier"]   # public IATA form
        assert r["id"] == f"perm-flight-{r['identifier'].lower()}"
        assert re.fullmatch(r"[A-Z]{4}", r["origin"]) and re.fullmatch(r"[A-Z]{4}", r["destination"])
        assert r["days_active"] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        assert not is_vip_record(r), r["id"]
        if r["id"] != "perm-flight-ba293":
            assert r["added"] == "2026-10-20" and r["added_by"] == "operator"
            assert r["notes"].startswith("Bellwether") and "\n" not in r["notes"]
        sis = r.get("sister_flight")
        if sis:     # route pair partner: the reverse direction, pointing back
            p = by_id[sis]
            assert p["sister_flight"] == r["id"]
            # ET500 (ADD-)FCO-IAD pairs with ET501 IAD-ADD: one endpoint reverses
            assert p["origin"] == r["destination"] or p["destination"] == r["origin"]
    # the VIP aircraft entries carry no topic override
    for r in recs:
        if is_vip_record(r):
            assert "alert_topic" not in r, r["id"]
    for ident in ("UA882", "UA881", "NH112", "NH111", "AA280", "AA281", "KE031", "KE032", "NH125"):
        assert f"perm-flight-{ident.lower()}" in by_id
