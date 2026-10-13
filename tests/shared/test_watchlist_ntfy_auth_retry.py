"""2026-10-13 ntfy audit: _fire_ntfy_dual logged intermittent 403s as "treating as delivered";
3 of 3 checked were never stored by ntfy. Now one delayed retry, then a logged drop."""
from unittest.mock import MagicMock

import requests

from shared import watchlist as wl


def _resp(status):
    r = MagicMock(status_code=status, text="forbidden" if status >= 400 else "")
    r.raise_for_status.side_effect = requests.exceptions.HTTPError(response=r) if status >= 400 else None
    return r


def test_a_transient_403_is_retried_and_delivered(monkeypatch):
    calls = []
    seq = {"flight-alerts": [_resp(403), _resp(200)], "dispatch": [_resp(200)]}

    def post(url, **kw):
        topic = url.rsplit("/", 1)[1]; calls.append(topic)
        return seq[topic].pop(0)
    monkeypatch.setattr(wl.requests, "post", post)
    monkeypatch.setattr(wl.time, "sleep", lambda s: None)
    wl._fire_ntfy_dual("flight-alerts", "t", "detail", "short", 3)
    assert calls.count("flight-alerts") == 2 and calls.count("dispatch") == 1


def test_a_persistent_403_is_logged_as_not_delivered(monkeypatch, caplog):
    n = []
    monkeypatch.setattr(wl.requests, "post", lambda url, **kw: (n.append(url), _resp(403))[1])
    monkeypatch.setattr(wl.time, "sleep", lambda s: None)
    monkeypatch.setattr(wl._ntfy_ambiguous_dedup, "should_push_periodic", lambda *a: True)
    monkeypatch.setattr(wl._ntfy_ambiguous_dedup, "record", lambda *a: None)
    with caplog.at_level("ERROR"):
        wl._fire_ntfy_dual("flight-alerts", "t", "detail", "short", 3)
    assert len(n) == 4                                                 # two topics x (send + one retry)
    assert "NOT delivered" in caplog.text and "treating as delivered" not in caplog.text
