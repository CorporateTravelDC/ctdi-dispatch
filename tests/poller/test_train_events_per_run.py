"""2026-10-13: train pushes compared the watch's last event across days, so a train that
had ever run counted as "already running" forever (2155: last event "LATE 31min" on 10-05,
no en-route push since) and identical "arrived" texts were suppressed across days.
Only an event from THIS run (after amtraker's origSchDep) counts now."""
from unittest.mock import MagicMock

import pytest

import poller.main as pm


def _train(state, dep="2026-10-13T16:00:00-04:00", arr_status="", sch_arr=None, arr=None):
    return {"2155": [{"trainState": state, "origSchDep": dep,
                      "stations": [{"code": "WAS", "status": arr_status,
                                    "schArr": sch_arr, "arr": arr}]}]}


def _run(monkeypatch, entry, payload):
    resp = MagicMock(status_code=200); resp.json.return_value = payload
    monkeypatch.setattr("requests.get", lambda *a, **k: resp)
    monkeypatch.setattr(pm.db, "update_train_phase", lambda *a, **k: None, raising=False)
    hits = []
    pm._check_train_amtraker(entry, "2155", "http://x", lambda eid, s, d, priority=3: hits.append(s))
    return hits


def _entry(last, at):
    return {"id": "wl-2155", "identifier": "2155", "route_name": "Acela", "destination": "WAS",
            "origin": None, "last_event_summary": last, "last_event_at": at}


def test_en_route_fires_on_a_new_run_despite_an_old_late_event(monkeypatch):
    hits = _run(monkeypatch, _entry("#2155 LATE 31min", "2026-10-05T18:07:30Z"), _train("Active"))
    assert hits and "en route" in hits[0]


def test_en_route_does_not_repeat_within_the_same_run(monkeypatch):
    hits = _run(monkeypatch, _entry("#2155 Acela →WAS en route", "2026-10-13T20:30:00Z"), _train("Active"))
    assert hits == []


def test_identical_arrival_text_from_an_earlier_day_still_fires(monkeypatch):
    hits = _run(monkeypatch, _entry("#2155 arrived", "2026-10-12T23:58:00Z"),
                _train("Completed", arr_status="Arrived"))
    assert hits == ["#2155 arrived"]


@pytest.mark.parametrize("at,expect", [("2026-10-13T20:00:00Z", "kept"), ("2026-10-12T20:00:00Z", "reset")])
def test_run_boundary_rule(at, expect):
    got = pm._train_last_event_for_run({"last_event_summary": "x", "last_event_at": at},
                                       {"origSchDep": "2026-10-13T16:00:00-04:00"})
    assert (got == "x") == (expect == "kept")


def test_without_origschdep_events_older_than_16h_are_reset():
    e = {"last_event_summary": "x", "last_event_at": "2026-10-13T00:00:00Z"}
    t0 = pm._iso_epoch("2026-10-13T00:00:00Z")
    assert pm._train_last_event_for_run(e, {}, now=t0 + 3600) == "x"
    assert pm._train_last_event_for_run(e, {}, now=t0 + 17 * 3600) == ""
