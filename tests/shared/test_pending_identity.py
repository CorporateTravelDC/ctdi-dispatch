"""
2026-11-18 (operator, UA4006 SAV-IAD) -- a flight watch added before the day's
plan exists is QUEUED for tail confirmation: one p3 "QUEUED ... awaiting tail
confirmation" push at creation, "still awaiting tail" from scheduled departure
minus 60 min, "no plan filed; watch expired" after its ET date, and "IDENTITY
CONFIRMED" (identifier withheld for LADD) when a same-occurrence source binds it.
Yesterday's plan never binds. Identities are synthetic (ZZZ4006, hex 000001,
tail N000TS).
"""
import asyncio
import json
import time

import pytest

import common.db as db
from common import flight_instance as fi


def _iso(ep):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))


RAW = ("<MessageCollection><message><flight>"
       "<flightIdentification aircraftIdentification=\"ZZZ4006\"/>"
       "<aircraftDescription aircraftAddress=\"000001\" registration=\"N000TS\"/>"
       "</flight></message></MessageCollection>")


@pytest.fixture
def env(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    import shared.watchlist as wl
    import web.routes.watchlist as wl_route
    sent = []
    monkeypatch.setattr(wl, "_fire_ntfy_dual",
                        lambda topic, title, body, dbody, priority: sent.append((title, body, priority)))
    monkeypatch.setattr(wl_route, "_fire_ntfy_dual", lambda **k: sent.append(("WATCHING", k, k["priority"])))
    monkeypatch.setattr(wl, "seed_departure_phase_from_tfms", lambda entry: None, raising=False)
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hexes: set())
    monkeypatch.setattr(db, "faa_is_ladd", lambda n: False)
    with db.conn() as c:
        c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex, updated_at) VALUES (?, ?, ?)",
                  ("000TS", "000001", 0))
    yield wl, wl_route, sent
    fi.clear_alias_cache()


def _add(wl_route, **body):
    from auth.auth import Tier
    resp = asyncio.run(wl_route.add_flight_watchlist(wl_route.FlightWatchlistRequest(**body), tier=Tier.ADMIN))
    return json.loads(resp.body)


def _stored(entry_id):
    return next((e for e in db.get_watchlist_entries(entry_type="flight") if e["id"] == entry_id), None)


def _yesterdays_plan():
    y = time.time() - 22 * 3600
    db.upsert_flight_event("G-Y", "ZZZ", "4006", "KSAV", "KIAD", None, y - 2 * 3600, y, "active",
                           None, None, None, None, RAW, message_time=y)


def test_add_with_no_identity_is_queued_with_one_p3_push(env):
    wl, wl_route, sent = env
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD")
    assert out["identity_state"] == "pending_identity" and out["queued_at"] == out["added_at"]
    assert out["hex_id"] is None and out["registration"] is None
    assert len(sent) == 1
    title, body, prio = sent[0]
    assert prio == 3 and "WATCHING" not in title
    assert "QUEUED ZZZ4006 KSAV-KIAD" in body and "awaiting tail confirmation" in body
    assert "no plan for this leg yet" in body


def test_yesterdays_plan_never_binds_at_add_or_in_the_sweep(env, monkeypatch):
    wl, wl_route, sent = env
    _yesterdays_plan()
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD")
    stored = _stored(out["id"])
    assert stored["hex_id"] is None and stored["registration"] is None
    assert "no plan for this leg yet" in sent[0][1]
    # bootstrap path (poller tick): local ADS-B dark, FDPS holds only yesterday's leg
    monkeypatch.setattr(wl, "_local_ac_by_callsign", lambda cs: None)
    monkeypatch.setattr(wl, "_local_registry_hex_lookup", lambda cs: None)
    monkeypatch.setattr(wl, "_remote_ac_by_callsign", lambda cs: None)
    assert wl.resolve_flight_identity(stored, "ZZZ4006") is None
    assert _stored(out["id"])["hex_id"] is None


def test_same_day_plan_binds_and_pushes_identity_confirmed(env, monkeypatch):
    wl, wl_route, sent = env
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD")
    now = time.time()
    db.upsert_flight_event("G-T", "ZZZ", "4006", "KSAV", "KIAD", None, now + 600, now + 7800, "active",
                           None, None, None, None, RAW, message_time=now)
    monkeypatch.setattr(wl, "_local_ac_by_callsign", lambda cs: None)
    monkeypatch.setattr(wl, "_local_registry_hex_lookup", lambda cs: None)
    ac = wl.resolve_flight_identity(_stored(out["id"]), "ZZZ4006")
    assert ac and ac["hex"] == "000001"
    stored = _stored(out["id"])
    assert stored["hex_id"] == "000001"
    confirmed = [s for s in sent if "IDENTITY CONFIRMED" in s[1]]
    assert len(confirmed) == 1 and "N000TS/000001" in confirmed[0][1]


def test_ladd_airframe_is_confirmed_without_an_identifier(env, monkeypatch):
    from shared import pending_identity as pi
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hexes: {"000001"})
    text, withheld = pi.confirmed_summary("000001", "N000TS")
    assert withheld and "000001" not in text and "N000TS" not in text


def test_ladd_check_failure_withholds(env, monkeypatch):
    from shared import pending_identity as pi

    def boom(hexes):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "faa_ladd_hex_batch", boom)
    text, withheld = pi.confirmed_summary("000001", "N000TS")
    assert withheld and "000001" not in text


def test_aggregator_contact_never_binds(env, monkeypatch):
    wl, wl_route, sent = env
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD")
    monkeypatch.setattr(wl, "_local_ac_by_callsign", lambda cs: None)
    monkeypatch.setattr(wl, "_local_registry_hex_lookup", lambda cs: None)
    monkeypatch.setattr(wl, "_local_fdps_ac", lambda cs, entry=None: None)
    monkeypatch.setattr(wl, "_remote_ac_by_callsign",
                        lambda cs: {"hex": "000001", "r": "N000TS", "_source": "airplanes_live"})
    wl.resolve_flight_identity(_stored(out["id"]), "ZZZ4006")
    assert _stored(out["id"])["hex_id"] is None
    assert not [s for s in sent if "IDENTITY CONFIRMED" in s[1]]


def test_still_awaiting_tail_fires_once_from_sched_minus_60(env):
    wl, wl_route, sent = env
    from shared import pending_identity as pi
    sched = time.time() + 30 * 60
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD",
               scheduled_departure=_iso(sched))
    sent.clear()
    entry = _stored(out["id"])
    pi.sweep([entry], now=sched - 90 * 60)           # too early
    assert sent == []
    pi.sweep([entry], now=sched - 45 * 60)
    pi.sweep([entry], now=sched - 40 * 60)           # same state: deduped by the parity gate
    still = [s for s in sent if "still awaiting tail" in s[1]]
    assert len(still) == 1 and still[0][2] == 4
    assert "ET)" in still[0][1] and "Z (" in still[0][1]


def test_expired_with_no_plan_pushes_and_closes(env):
    wl, wl_route, sent = env
    from shared import pending_identity as pi
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD")
    entry = _stored(out["id"])
    sent.clear()
    after = fi.watch_day_start(entry) + 25 * 3600
    res = pi.sweep([entry], now=after)
    assert res["expired"] == 1 and _stored(out["id"]) is None
    assert any("no plan filed; watch expired" in s[1] for s in sent)


def test_expiry_waits_when_a_plan_for_the_occurrence_exists(env):
    wl, wl_route, sent = env
    from shared import pending_identity as pi
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD")
    entry = _stored(out["id"])
    day0 = fi.watch_day_start(entry)
    db.upsert_flight_event("G-T2", "ZZZ", "4006", "KSAV", "KIAD", None, day0 + 20 * 3600,
                           day0 + 22 * 3600, "proposed", None, None, None, None, "<x/>",
                           message_time=day0 + 18 * 3600)
    res = pi.sweep([entry], now=day0 + 25 * 3600)
    assert res["expired"] == 0 and _stored(out["id"]) is not None


def test_bound_watches_are_ignored_by_the_sweep(env):
    wl, wl_route, sent = env
    from shared import pending_identity as pi
    out = _add(wl_route, identifier="ZZZ4007", hex_id="000001", origin="KSAV", destination="KIAD",
               scheduled_departure=_iso(time.time() + 600))
    assert out["identity_state"] == "bound"
    sent.clear()
    assert pi.sweep([_stored(out["id"])])["pending"] == 0 and sent == []


def test_watchlist_get_shows_the_queue_state(env):
    wl, wl_route, sent = env
    from auth.auth import Tier
    out = _add(wl_route, identifier="ZZZ4006", origin="KSAV", destination="KIAD")
    resp = asyncio.run(wl_route.list_watchlist_entries(tier=Tier.ADMIN, include=None, held=None))
    e = next(x for x in json.loads(resp.body)["entries"] if x["id"] == out["id"])
    assert e["identity_state"] == "pending_identity" and e["queued_at"] == out["added_at"]


def test_new_kinds_are_registered():
    from common import push_policy_registry as reg
    for k in ("identity_queued", "identity_still_pending", "identity_expired_no_plan"):
        assert reg.resolve("aviation", k) is not None


def test_skill_text_queues_instead_of_not_added():
    from pathlib import Path
    t = (Path(__file__).resolve().parents[2] / "skills/flight-hifi-track/SKILL.md").read_text()
    assert "QUEUED — awaiting tail confirmation" in t
    assert "never as a confirmed or ready watch" in t


def test_runner_badge_labels_pending_watches():
    from pathlib import Path
    t = (Path(__file__).resolve().parents[2] /
         "src/runner/frontend/src/components/aircraftMap/WatchlistBadge.jsx").read_text()
    assert "pending_identity" in t and "awaiting tail" in t


@pytest.mark.parametrize("day,hours", [("20261101", 25), ("20260308", 23), ("20261118", 24)])
def test_day_end_is_next_local_midnight_on_dst_days(day, hours):
    """The pending watch expires at 00:00 America/New_York the next calendar day: 25 h after the
    day's start on 2026-11-01 (fall back), 23 h on 2026-03-08 (spring forward), 24 h otherwise."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    entry = {"id": f"wl-flight-zzz4006-{day}"}
    start, end = fi.watch_day_start(entry), fi.watch_day_end(entry)
    assert end - start == hours * 3600
    local = datetime.fromtimestamp(end, ZoneInfo("America/New_York"))
    assert (local.hour, local.minute) == (0, 0)
    assert local.strftime("%Y%m%d") > day
