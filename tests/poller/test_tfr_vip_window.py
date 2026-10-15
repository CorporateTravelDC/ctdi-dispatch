"""2026-10-14 (operator: VIP pushes read "Active ? to ?"): the FAA list feed carries no times; the window
comes from the per-NOTAM detail record, in UTC, and is shown UTC + ET. Synthetic fixture with the real
document's structure."""
import json
from pathlib import Path

import pytest

from poller.fetchers import tfr

FIX = (Path(__file__).resolve().parents[1] / "fixtures/tfr/detail_vip_synthetic.xml").read_text(encoding="utf-8")


def test_detail_window_is_utc_and_areas_dedupe():
    s, e, areas = tfr.parse_detail_window(FIX)
    assert s == 1792084500.0       # 2026-10-15T17:15:00Z == 13:15 EDT (the FAA text pairs them)
    assert e == 1792109700.0       # 2026-10-16T00:15:00Z
    assert areas == [[1792084500.0, 1792092600.0], [1792098900.0, 1792109700.0]]


def test_fetch_fills_vip_windows_and_caches_by_mod_time(monkeypatch):
    calls = []

    class R:
        content = FIX.encode("utf-8")
        def raise_for_status(self): pass

    class L:
        def raise_for_status(self): pass
        def json(self):
            return [{"notam_id": "6/9999", "type": "VIP", "description": "Somewhere, Thursday Local",
                     "mod_abs_time": "202610142107"},
                    {"notam_id": "6/1111", "type": "HAZARDS", "description": "x", "mod_abs_time": "1"}]

    def get(url, **kw):
        calls.append(url)
        return L() if url == tfr.TFR_URL else R()

    tfr._detail_cache.clear()
    monkeypatch.setattr(tfr.requests, "get", get)
    recs = {r.tfr_id: r for r in tfr.fetch()}
    assert recs["6/9999"].effective_start == 1792084500.0 and recs["6/9999"].effective_end == 1792109700.0
    assert len(json.loads(recs["6/9999"].raw_json)["area_windows_utc"]) == 2
    assert recs["6/1111"].effective_start is None                      # non-VIP: no detail fetch
    tfr.fetch()
    assert sum("detail_" in u for u in calls) == 1                     # same mod time: cached


def test_detail_failure_is_not_cached(monkeypatch):
    tfr._detail_cache.clear()
    def boom(url, **kw):
        raise OSError("down")
    monkeypatch.setattr(tfr.requests, "get", boom)
    assert tfr.fetch_detail_window("6/9999", "1") == (None, None, [])
    assert "6/9999" not in tfr._detail_cache


def test_pusher_window_text_utc_and_et():
    import pusher.main as pm
    t = {"effective_start": 1792084500.0, "effective_end": 1792109700.0,
         "raw_json": json.dumps({"area_windows_utc": [[1792084500.0, 1792092600.0], [1792098900.0, 1792109700.0]]})}
    w = pm._tfr_window(t)
    assert w.startswith("Active 17:15Z / 13:15 ET Thu 10-15 to 00:15Z / 20:15 ET Thu 10-15")
    assert "Area windows: 17:15Z / 13:15 ET Thu 10-15 to 19:30Z / 15:30 ET Thu 10-15;" in w
    assert "?" not in w
    assert pm._tfr_window({}) == "Active window not published by the FAA yet"
