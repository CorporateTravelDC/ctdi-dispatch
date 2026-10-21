"""
tests/shared/test_drone_alerts_topic.py -- (operator 2026-10-16): drone (Part 107
UAS) watchlist events get their own ntfy topic, `drone-alerts`, at every place a
topic is chosen by entry type. Before this they fell through to the train branch
of shared.watchlist.watchlist_event_hit(), the pusher retry map sent them to
train-alerts with "Train <id>" copy, the web remove map dropped them to
"dispatch" only, and utm_watcher pushed Remote ID hits to flight-alerts.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common import db  # noqa: E402


def _iso(offset_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _drone_entry(ident: str = "1596F3A0B1C2D3E4", notes: str | None = "Mavic 3") -> dict:
    return {
        "id": f"wl-drone-{ident.lower()}-test", "entry_type": "drone", "tier": "transient",
        "identifier": ident, "origin": None, "destination": None, "route_name": None,
        "scheduled_departure": None, "scheduled_arrival": None, "auto_remove_at": _iso(60),
        "added_at": _iso(), "added_by": "test", "notes": notes,
        "last_event_at": None, "last_event_summary": None,
    }


def test_watchlist_event_hit_drone_branch_routes_to_drone_alerts():
    db.init_db_all()
    from shared import watchlist as wl
    entry = _drone_entry()
    db.upsert_watchlist_entry(entry)
    fired = []
    with patch("shared.watchlist._fire_ntfy_dual",
               side_effect=lambda topic, title, detail, dispatch, prio: fired.append((topic, title, detail, dispatch))):
        wl.watchlist_event_hit(entry["id"], "launched 38.90N 77.03W 120 ft AGL",
                               {"watchlist_trigger": "uas_launched"}, priority=3)
    assert len(fired) == 1
    topic, title, detail, dispatch = fired[0]
    assert topic == "drone-alerts"
    assert title.startswith("UAS " + entry["identifier"] + ": ")
    assert dispatch.startswith("Drone " + entry["identifier"] + ": ")
    assert "Mavic 3 (UAS " + entry["identifier"] + ")" in detail
    assert "Train" not in dispatch and "TRN " not in title
    hist = db.get_watchlist_history(entry_id=entry["id"])
    assert len(hist) == 1 and hist[0]["entry_type"] == "drone"


def test_pusher_retries_drone_rows_to_drone_alerts(monkeypatch):
    import pusher.main as pm
    sent = []
    monkeypatch.setattr(db, "get_watchlist_history_unfired",
                        lambda max_age_seconds=900: [{"id": 7, "entry_type": "drone",
                                                       "identifier": "1596F3A0B1C2D3E4",
                                                       "event_summary": "landed", "ntfy_priority": 3}])
    monkeypatch.setattr(db, "mark_watchlist_history_fired", lambda rid: None)
    monkeypatch.setattr(pm, "send_ntfy",
                        lambda topic, msg, priority=3, *, title: sent.append((topic, title, msg)) or True)
    assert pm.push_watchlist_retries() == 1
    topics = [s[0] for s in sent]
    assert topics == ["drone-alerts", "dispatch"]
    assert sent[0][1].startswith("UAS 1596F3A0B1C2D3E4: ")
    assert sent[1][2].startswith("Drone 1596F3A0B1C2D3E4: ")


def test_web_remove_pushes_drone_removal_to_drone_alerts():
    db.init_db_all()
    import web.routes.watchlist as wr
    entry = _drone_entry("1596F3A0B1C2D3E5")
    db.upsert_watchlist_entry(entry)
    fired = []
    with patch("web.routes.watchlist._fire_ntfy_dual",
               side_effect=lambda **kw: fired.append(kw)):
        asyncio.run(wr.remove_watchlist_entry(entry["id"], tier=None))
    assert len(fired) == 1
    assert fired[0]["domain_topic"] == "drone-alerts"
    assert all(e["id"] != entry["id"] for e in db.get_watchlist_entries(entry_type="drone"))


def test_utm_watcher_default_topic_is_drone_alerts(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    from utm_watcher import utm_watcher as uw
    uw = importlib.reload(uw)
    assert uw.NTFY_TOPIC == "drone-alerts"
    monkeypatch.setenv("NTFY_TOPIC", "flight-alerts")      # the env override still wins
    assert importlib.reload(uw).NTFY_TOPIC == "flight-alerts"
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    importlib.reload(uw)


def test_every_vessel_topic_site_also_names_drone():
    """Mirror rule: wherever code picks a topic for vessels it picks one for drones."""
    root = Path(__file__).resolve().parents[2] / "src"
    # 2026-10-20 (bellwether batch): the pusher's retry map moved to the shared
    # per-entry helper shared/watch_topic.py (TYPE_TOPICS); it is the mirror site now,
    # and the pusher names neither literal.
    for rel in ("shared/watchlist.py", "shared/watch_topic.py"):
        text = (root / rel).read_text()
        assert text.count('"drone-alerts"') >= 1, rel
        assert text.count('"vessel-alerts"') == text.count('"drone-alerts"'), rel
    pusher = (root / "pusher/main.py").read_text()
    assert pusher.count('"vessel-alerts"') == pusher.count('"drone-alerts"') == 0
    # The web routes have a vessel ADD route and no drone one; the per-type remove map is the mirror site.
    assert '"drone": "drone-alerts"' in (root / "web/routes/watchlist.py").read_text()
