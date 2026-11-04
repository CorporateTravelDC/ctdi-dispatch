"""
2026-11-04 -- an FDPS plan for the watched number on ANOTHER DAY never binds
the watch's airframe (ASH4067 KSDF-KIAD: today's plan was not filed yet, the
newest row was yesterday's leg, and the poller tick repointed the 11-04 watch
to yesterday's airframe and stamped it corroborated).

Identities are synthetic (hex 000001/000002, tails N000TS/N001TS).
"""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import common.db as db
from common import flight_instance as fi

ET = ZoneInfo("America/New_York")


def _raw(callsign, hex_id, reg=None):
    reg_attr = f' registration="{reg}"' if reg else ""
    return ("<MessageCollection><message><flight>"
            f"<flightIdentification aircraftIdentification=\"{callsign}\"/>"
            f"<aircraftDescription aircraftAddress=\"{hex_id}\"{reg_attr}/>"
            "</flight></message></MessageCollection>")


def _iso(ep):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))


def _id_date(ep):
    return datetime.fromtimestamp(ep, tz=ET).strftime("%Y%m%d")


@pytest.fixture
def poller(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    import shared.watchlist as wl
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit",
                        lambda eid, summary, detail, priority=3: hits.append((summary, detail)))
    import poller.main as pm
    yield pm, hits
    fi.clear_alias_cache()


def _watch(dep_ep, ident="ZZZ4067"):
    e = {"id": f"wl-flight-{ident.lower()}-{_id_date(dep_ep)}", "entry_type": "flight",
         "tier": "transient", "identifier": ident, "origin": "SDF", "destination": "IAD",
         "route_name": None, "scheduled_departure": _iso(dep_ep),
         "scheduled_arrival": _iso(dep_ep + 2 * 3600), "auto_remove_at": None,
         "added_at": _iso(time.time()), "added_by": "test", "notes": None,
         "last_event_at": None, "last_event_summary": None,
         "hex_id": "000001", "registration": "N000TS"}
    db.upsert_watchlist_entry(e)
    return next(x for x in db.get_watchlist_entries(entry_type="flight") if x["id"] == e["id"])


def _stored(entry):
    return next(x for x in db.get_watchlist_entries(entry_type="flight") if x["id"] == entry["id"])


def _yesterdays_plan(dep_today, gufi="G-Y"):
    """The previous occurrence of the same number and route: departed and
    arrived ~23 h before today's leg, last updated just after it landed."""
    y_dep, y_arr = dep_today - 24 * 3600, dep_today - 24 * 3600 + 100 * 60
    db.upsert_flight_event(gufi, "ZZZ", "4067", "KSDF", "KIAD", None, y_dep, y_arr, "active",
                           None, None, None, None, _raw("ZZZ4067", "000002", "N001TS"))
    with db.conn() as c:
        c.execute("UPDATE flight_events SET updated_at=? WHERE flight_id=?", (y_arr + 180, gufi))


def test_yesterdays_plan_does_not_repoint_todays_watch(poller):
    pm, hits = poller
    dep = time.time() + 40 * 60
    entry = _watch(dep)
    _yesterdays_plan(dep)
    pm._check_flight_fdps_cache(entry, "ZZZ4067")
    stored = _stored(entry)
    assert stored["hex_id"] == "000001" and stored["registration"] == "N000TS"
    assert not stored.get("hex_corroborated_at")
    assert not [d for _, d in hits if "tail_change" in (d.get("watchlist_trigger") or "")]


def test_todays_plan_still_repoints(poller):
    pm, hits = poller
    dep = time.time() + 40 * 60
    entry = _watch(dep)
    db.upsert_flight_event("G-T", "ZZZ", "4067", "KSDF", "KIAD", None, dep, dep + 100 * 60, "proposed",
                           None, None, None, None, _raw("ZZZ4067", "000002", "N001TS"))
    pm._check_flight_fdps_cache(entry, "ZZZ4067")
    assert _stored(entry)["hex_id"].lower() == "000002"
    assert [d for _, d in hits if d.get("watchlist_trigger") == "fdps_tail_change"]


def _day0(ep):
    d = datetime.fromtimestamp(ep, tz=ET)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


@pytest.mark.parametrize("plan,why", [
    ({"departure_time": None, "arrival_time": None, "updated_at": None}, "plan_has_no_time"),
    ({"departure_time": "S-24h", "arrival_time": "S-22h", "updated_at": "S-22h"}, "departure_outside_instance_window"),
    ({"departure_time": None, "arrival_time": "S-23h", "updated_at": "S-23h"}, "arrival_outside_instance_window"),
    ({"departure_time": None, "arrival_time": None, "updated_at": "S-30h"}, "untimed_plan_older_than_instance"),
    ({"departure_time": "S+45m", "arrival_time": "S+3h", "updated_at": "S-1h"}, None),     # delayed, same leg
    ({"departure_time": "S+5h", "arrival_time": None, "updated_at": "S-1h"}, "departure_outside_instance_window"),
    ({"departure_time": None, "arrival_time": None, "updated_at": "S-5h"}, "untimed_plan_older_than_instance"),
    ({"departure_time": None, "arrival_time": None, "updated_at": "S-2h"}, None),          # updated within 3 h
])
def test_rejection_with_a_schedule(plan, why):
    s = 1793819000.0
    off = {"S-24h": -24 * 3600, "S-22h": -22 * 3600, "S-23h": -23 * 3600, "S-30h": -30 * 3600, "S+5h": 5 * 3600, "S-5h": -5 * 3600, "S-2h": -2 * 3600,
           "S+45m": 45 * 60, "S+3h": 3 * 3600, "S-1h": -3600}
    p = {k: (s + off[v] if isinstance(v, str) else v) for k, v in plan.items()}
    assert fi.fdps_plan_occurrence_rejection({"id": "wl-flight-x-20261104"}, p, sched=_iso(s)) == why


def test_early_request_without_schedule_rejects_yesterdays_plan():
    # watch dated 2026-11-04 ET, no schedule; yesterday's leg landed 11-03 20:22Z
    entry = {"id": "wl-flight-zzz4067-20261104", "scheduled_departure": None}
    day0 = _day0(datetime(2026, 11, 4, 12, tzinfo=ET).timestamp())
    y_arr = datetime(2026, 11, 3, 15, 22, tzinfo=ET).timestamp()
    assert y_arr < day0
    assert fi.fdps_plan_occurrence_rejection(
        entry, {"departure_time": None, "arrival_time": y_arr, "updated_at": y_arr + 180}) == "prior_day_occurrence"
    assert fi.fdps_plan_occurrence_rejection(
        entry, {"departure_time": None, "arrival_time": None, "updated_at": y_arr + 180}) \
        == "untimed_plan_not_updated_on_watch_date"
    assert fi.fdps_plan_occurrence_rejection(
        entry, {"departure_time": day0 + 14 * 3600, "arrival_time": None, "updated_at": day0 + 3600}) is None


def test_add_time_window_is_three_hours():
    from web.routes import watchlist as rw
    assert rw.FDPS_IDENTITY_FRESH_WINDOW_S == 3 * 3600 == int(fi.FDPS_OCCURRENCE_WINDOW_H * 3600)
    now = 1793819000.0
    assert rw._fdps_plan_is_current_leg({"updated_at": now - 2.5 * 3600}, None, now_epoch=now)
    assert not rw._fdps_plan_is_current_leg({"updated_at": now - 4 * 3600}, None, now_epoch=now)
