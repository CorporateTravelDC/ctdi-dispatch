"""2026-10-15 (operator): the VIP TFRs pushed with "? to ?" between the 10-14 cutover and the window fix
get ONE corrected alert each, only while still active, only once their window is known, once ever."""
import json
import time

import pytest

import common.db as db


@pytest.fixture
def pm(monkeypatch):
    db.init_db_all()
    import pusher.main as pm
    sent = []
    monkeypatch.setattr(pm, "send_ntfy", lambda topic, msg, priority=3, *, title: sent.append((topic, priority, title, msg)) or True)
    pm._sent = sent
    return pm


def _iso(ep):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))


def _setup(pm, monkeypatch, tmp_dedup, tfrs):
    from common import config
    sd = config.state_dir()
    import pathlib
    cut = time.time() - 10 * 3600
    (pathlib.Path(sd) / pm.VIP_CUTOVER_MARKER).write_text(f"{_iso(cut)} silenced 2 active VIP TFR(s)\n")
    monkeypatch.setattr(pm._tfr_dedup, "_load", lambda: tmp_dedup(cut))
    monkeypatch.setattr(db, "get_active_tfrs", lambda: tfrs)
    return pathlib.Path(sd)


def _t(tid, start=None, end=None):
    return {"tfr_id": tid, "is_vip": 1, "effective_start": start, "effective_end": end,
            "raw_json": json.dumps({"description": f"Place {tid}", "mod_abs_time": "1"})}


def test_only_pushed_since_cutover_and_still_active(pm, monkeypatch):
    now = time.time()
    dedup = lambda cut: {"6/0001": {"ts": cut}, "6/0002": {"ts": cut + 3600}, "6/0003": {"ts": cut + 7200}}  # noqa: E731
    sd = _setup(pm, monkeypatch, dedup, [_t("6/0001", now, now + 3600), _t("6/0002", now, now + 3600)])
    assert pm.vip_window_corrections() == 1                  # 0001 silenced at cutover; 0003 no longer active
    topics = sorted((x[0], x[1]) for x in pm._sent)
    assert topics == [("hot-alerts", 4), ("tfr-alert", 4)]
    assert all("6/0002" in x[2] and "CORRECTED TIMES" in x[3] and "?" not in x[3].split("\n", 1)[1] for x in pm._sent)
    assert (sd / pm.VIP_WINDOW_FIX_MARKER).exists()
    pm._sent.clear()
    assert pm.vip_window_corrections() == 0 and pm._sent == []   # once, ever


def test_waits_for_the_window_then_sends(pm, monkeypatch):
    now = time.time()
    dedup = lambda cut: {"6/0002": {"ts": cut + 3600}}  # noqa: E731
    tfrs = [_t("6/0002")]                                    # poller has not filled the window yet
    sd = _setup(pm, monkeypatch, dedup, tfrs)
    assert pm.vip_window_corrections() == 0 and pm._sent == []
    assert not (sd / pm.VIP_WINDOW_FIX_MARKER).exists()
    tfrs[0] = _t("6/0002", now, now + 3600)
    assert pm.vip_window_corrections() == 1
    assert (sd / pm.VIP_WINDOW_FIX_MARKER).exists()
