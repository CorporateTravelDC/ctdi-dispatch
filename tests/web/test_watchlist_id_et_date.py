"""
2026-10-22: watch ids (wl-<type>-<ident>-<YYYYMMDD>) were keyed by the UTC date
of "now", so a watch added at ~19:00 ET and re-added after 20:00 ET (already the
next UTC day) got a new id -- a duplicate watch for the same leg. The date part
is now the America/New_York date of the scheduled departure, else of now.
Synthetic identifiers only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from common import flight_instance as fi
from web.routes import watchlist as wr


def _freeze(monkeypatch, epoch: float) -> None:
    monkeypatch.setattr(fi, "time", SimpleNamespace(time=lambda: epoch))


def _ep(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def test_evening_adds_either_side_of_utc_midnight_share_one_id(monkeypatch):
    # 19:00 EDT = 23:00Z on the 22nd; 20:30 EDT = 00:30Z on the 23rd.
    t1 = _ep("2026-10-22T19:00:00-04:00")
    t2 = _ep("2026-10-22T20:30:00-04:00")
    assert datetime.fromtimestamp(t2, tz=timezone.utc).day == 23   # precondition: next UTC day
    _freeze(monkeypatch, t1)
    id1 = wr._make_id("flight", "QQQ123")
    _freeze(monkeypatch, t2)
    id2 = wr._make_id("flight", "QQQ123")
    assert id1 == id2 == "wl-flight-qqq123-20261022"


def test_scheduled_departure_on_a_different_et_date_keys_to_that_date(monkeypatch):
    _freeze(monkeypatch, _ep("2026-10-22T19:00:00-04:00"))
    # 01:15Z on the 24th is 21:15 EDT on the 23rd -> the 23rd, not today, not the UTC date.
    assert wr._make_id("flight", "QQQ123", "2026-10-24T01:15:00Z") == "wl-flight-qqq123-20261023"
    assert wr._make_id("flight", "QQQ123", "2026-10-25T08:00:00-04:00") == "wl-flight-qqq123-20261025"
    assert wr._make_id("flight", "QQQ123", "2026-10-26") == "wl-flight-qqq123-20261026"


def test_unparseable_scheduled_departure_falls_back_to_et_now(monkeypatch):
    _freeze(monkeypatch, _ep("2026-10-22T20:30:00-04:00"))
    assert wr._make_id("flight", "QQQ123", "not-a-time") == "wl-flight-qqq123-20261022"
    assert wr._make_id("train", "Q 99", None) == "wl-train-q-99-20261022"


def test_bulk_import_uses_the_same_rule(monkeypatch):
    from poller.tools import watchlist_import as wi
    _freeze(monkeypatch, _ep("2026-10-22T20:30:00-04:00"))
    assert wi._make_entry_id("flight", "QQQ123") == "wl-flight-qqq123-20261022"
    e = wi._normalise_entry({"identifier": "qqq123", "scheduled_departure": "2026-10-24T01:15:00Z"},
                            "transient", "flight", "2026-10-23T00:30:00Z")
    assert e["id"] == "wl-flight-qqq123-20261023"
