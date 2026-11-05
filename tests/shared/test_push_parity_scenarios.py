"""
2026-11-04 push parity lock -- end-to-end through watchlist_event_hit only (no
import of the policy module), so the same scenarios run against the code from
before the lock and show what it did wrong there.

ASH4067 (11-04): TBFM KILMR flipped between "runway ETA 21:18Z" and "no usable
ETA" six times (six pushes); "TMI assignment: -ZID" pushed three times
unchanged. Identities are synthetic (ZZZ4067, hex 000001).
"""
import time

import pytest

import common.db as db


def _iso(ep):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))


@pytest.fixture
def hits(monkeypatch):
    db.init_db_all()
    import shared.watchlist as wl
    sent = []
    monkeypatch.setattr(wl, "_fire_ntfy_dual",
                        lambda topic, title, body, dbody, priority: sent.append((title, priority)))
    entry = {"id": "wl-flight-zzz4067-parity", "entry_type": "flight", "tier": "transient",
             "identifier": "ZZZ4067", "origin": "KSDF", "destination": "KIAD", "route_name": None,
             "scheduled_departure": _iso(time.time() - 3600), "scheduled_arrival": _iso(time.time() + 3600),
             "auto_remove_at": None, "added_at": _iso(time.time() - 7200), "added_by": "test",
             "notes": None, "last_event_at": None, "last_event_summary": None,
             "hex_id": "000001", "registration": "N000TS"}
    db.upsert_watchlist_entry(entry)
    return wl, entry["id"], sent


def _tbfm(eta_ep, usable=True):
    return {"watchlist_trigger": "tbfm_sequenced", "meter_fix": "KILMR", "facility": "ZDC",
            "airport": "IAD", "eta": _iso(eta_ep), "eta_kind": "rwy" if usable else None,
            "eta_label": (f"runway ETA {_iso(eta_ep)}" if usable else None), "eta_source": "tbfm"}


def test_tbfm_wording_flip_six_events_one_push(hits):
    wl, eid, sent = hits
    eta = time.time() + 30 * 60
    for i in range(6):
        usable = i % 2 == 1
        wl.watchlist_event_hit(eid, "ZZZ4067 TBFM: IAD arrival, " +
                               ("runway ETA" if usable else "sequenced at meter fix KILMR (no usable ETA)"),
                               _tbfm(eta, usable), priority=3)
    assert len(sent) == 1, sent
    assert sent[0][1] == 4          # first usable runway ETA is news


def test_eta_move_of_twelve_minutes_is_priority_4(hits):
    wl, eid, sent = hits
    eta = time.time() + 30 * 60
    wl.watchlist_event_hit(eid, "ZZZ4067 TBFM: runway ETA a", _tbfm(eta), priority=3)
    wl.watchlist_event_hit(eid, "ZZZ4067 TBFM: runway ETA b", _tbfm(eta + 12 * 60), priority=3)
    assert [p for _, p in sent] == [4, 4]


def test_small_eta_move_is_priority_3_and_same_bucket_is_quiet(hits):
    wl, eid, sent = hits
    eta = (int(time.time() + 40 * 60) // 300) * 300 + 10       # just inside a 5-minute bucket
    wl.watchlist_event_hit(eid, "e1", _tbfm(eta), priority=3)
    wl.watchlist_event_hit(eid, "e2", _tbfm(eta + 60), priority=3)        # same bucket
    wl.watchlist_event_hit(eid, "e3", _tbfm(eta + 7 * 60), priority=3)    # new bucket, < 10 min
    assert [p for _, p in sent] == [4, 3]


def test_tbfm_eta_already_past_while_on_ground_is_suppressed(hits):
    wl, eid, sent = hits
    wl.watchlist_event_hit(eid, "stale", _tbfm(time.time() - 2 * 3600), priority=3)
    assert sent == []


def test_identical_tmi_three_times_one_push(hits):
    wl, eid, sent = hits
    for n in range(3):
        wl.watchlist_event_hit(eid, "TMI assignment: -ZID",
                               {"watchlist_trigger": "tfms_tmi", "fca_id": None, "fca_name": "-ZID",
                                "constraint_entry_time": _iso(time.time() + n * 1200),
                                "constraint_exit_time": None, "exit_indicator": None}, priority=3)
    assert [p for _, p in sent] == [3]


def test_off_is_priority_4(hits):
    wl, eid, sent = hits
    wl.watchlist_event_hit(eid, "ZZZ4067 OFF -- wheels up -- confirmed via FDPS",
                           {"watchlist_trigger": "oooi_off", "phase": "off", "source": "fdps"}, priority=3)
    wl.watchlist_event_hit(eid, "ZZZ4067 OFF -- again from another source",
                           {"watchlist_trigger": "oooi_off", "phase": "off", "source": "acars"}, priority=3)
    assert [p for _, p in sent] == [4]


def test_history_records_the_decision_and_suppressions_never_retry(hits):
    wl, eid, sent = hits
    eta = time.time() + 30 * 60
    wl.watchlist_event_hit(eid, "no eta", _tbfm(eta, usable=False), priority=3)
    wl.watchlist_event_hit(eid, "eta", _tbfm(eta), priority=3)
    rows = db.get_watchlist_history(entry_id=eid, limit=10)
    fired = {r["ntfy_fired"] for r in rows}
    assert fired == {1, 2}
    assert all("push_policy" in (r["event_detail"] or "") for r in rows)
    assert any(r["ntfy_fired"] == 1 and r["ntfy_priority"] == 4 for r in rows)
    assert db.get_watchlist_history_unfired(max_age_seconds=900) == []
