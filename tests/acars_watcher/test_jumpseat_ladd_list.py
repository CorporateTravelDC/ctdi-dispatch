"""
2026-10-16 (operator: keep the LADD list for Jumpseat). The watcher's Jumpseat
thread queries a third party by registration, so it polls ONLY the list from
GET /api/v1/watchlist/registrations (LADD-filtered by the dispatch API); the
full watchlist set stays with the local UDP / airframes.io matching. When the
route is unavailable the thread polls nothing. No network: both sessions are
mocked; HOME is a temp dir so no real token file is read.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def aw(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ACARSDRAMA_JUMPSEAT_TOKEN", "dummy-test-token")
    monkeypatch.setenv("DISPATCH_ADMIN_TOKEN", "dummy-test-token")
    monkeypatch.setenv("DISPATCH_BASE_URL", "http://127.0.0.1:9")
    sys.modules.pop("acars_watcher.acars_watcher", None)
    mod = importlib.import_module("acars_watcher.acars_watcher")
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    yield mod
    sys.modules.pop("acars_watcher.acars_watcher", None)


def _resp(status, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.headers = {}
    return r


def _dispatch_get(full, filtered_status, filtered_body=None):
    def get(url, timeout=10):
        if url.endswith("/api/v1/watchlist/registrations"):
            return _resp(filtered_status, filtered_body)
        if url.endswith("/api/v1/watchlist"):
            return _resp(200, {"entries": full})
        raise AssertionError(url)
    return get


def test_jumpseat_polls_only_the_filtered_list(aw, monkeypatch, caplog):
    full = [{"entry_type": "flight", "identifier": "N000OK"},
            {"entry_type": "flight", "identifier": "N000LD"}]
    monkeypatch.setattr(aw.DISPATCH_SESSION, "get", _dispatch_get(
        full, 200, {"registrations": ["n-000ok"], "ladd_filtered": 1}))
    queried: list = []

    def js_get(url, params=None, timeout=15):
        queried.append(params["registration"])
        return _resp(200, {"items": [{"id": "m1", "registration": params["registration"],
                                      "text": "hello"}]})
    monkeypatch.setattr(aw.JUMPSEAT_SESSION, "get", js_get)

    with caplog.at_level("INFO"):
        watched = aw.refresh_watchlists()
    assert watched == {"N000OK", "N000LD"}                 # full set for UDP/airframes
    assert aw._JUMPSEAT_REGS == {"N000OK"}                 # filtered list for Jumpseat
    assert aw._jumpseat_poll_once(set()) == 1
    assert queried == ["N000OK"]
    assert aw.MSG_QUEUE.get_nowait()["_source"] == "JUMPSEAT"
    assert "N000LD" not in caplog.text                      # a filtered reg is never logged


@pytest.mark.parametrize("failure", [503, "exc", "malformed"])
def test_route_unavailable_polls_nothing_with_a_warning(aw, monkeypatch, caplog, failure):
    full = [{"entry_type": "flight", "identifier": "N000OK"}]
    if failure == "exc":
        def get(url, timeout=10):
            if url.endswith("/registrations"):
                raise ConnectionError("down")
            return _resp(200, {"entries": full})
        monkeypatch.setattr(aw.DISPATCH_SESSION, "get", get)
    elif failure == "malformed":
        monkeypatch.setattr(aw.DISPATCH_SESSION, "get", _dispatch_get(full, 200, {"nope": 1}))
    else:
        monkeypatch.setattr(aw.DISPATCH_SESSION, "get", _dispatch_get(full, failure))
    js = MagicMock(side_effect=AssertionError("Jumpseat must not be queried"))
    monkeypatch.setattr(aw.JUMPSEAT_SESSION, "get", js)

    with caplog.at_level("WARNING"):
        watched = aw.refresh_watchlists()
    assert watched == {"N000OK"}                            # other threads keep the full set
    assert aw._JUMPSEAT_REGS == set()
    assert aw._jumpseat_poll_once(set()) == 0
    js.assert_not_called()
    assert "polling nothing" in caplog.text
    assert "N000OK" not in caplog.text


def test_jumpseat_thread_never_reads_the_full_set(aw):
    import inspect
    import ast
    fn = ast.parse(inspect.getsource(aw._jumpseat_poll_once)).body[0]
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "_JUMPSEAT_REGS" in names and "_WATCHED_REGS" not in names
    assert "Watchlist: %d reg(s)" in inspect.getsource(aw.get_watched_registrations)
