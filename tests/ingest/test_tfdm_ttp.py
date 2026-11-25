"""2026-11-18: SWIM TFDM Terminal Publication (TTP) consumer.

The FAA warned that the TFDM subscription would be disabled for lack of
connectivity; the feed's first job is to stay connected and acknowledge every
message. Pinned here:
  - every message is acknowledged, even when the handler raises (ack-always);
  - the departure stand parses from the TTP Flight Data shape (by local name);
  - only gate evidence reaches gate_assignments (source swim_tfdm, departure gate),
    no time/status/tail, DC departures or watched flights only;
  - opt-in: only a container with SWIM_NMS_TFDM_ENABLED starts the session;
  - not configured: ingest.main exits 0 instead of idling.

ALL XML BELOW IS SYNTHETIC. Its element names follow the NASA ATD-2 "TTP Flight
Data Messages" example (NTRS 20180007226); flights, stands and ids are made up.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from ingest.parsers import tfdm_parser as tp

REPO = Path(__file__).resolve().parents[2]

# SYNTHETIC -- shape of the ATD-2 example, invented values.
SYNTH_FLIGHT_ADD = b"""<?xml version="1.0" encoding="UTF-8"?>
<nas:NasMessage xmlns:nas="http://www.faa.aero/nas/4.1"
  xmlns="http://www.fixm.aero/messaging/4.0" xmlns:base="http://www.fixm.aero/base/4.0"
  xmlns:fx="http://www.fixm.aero/flight/4.0">
  <nas:flight xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="nas:TfdmFlightType">
    <fx:aircraft />
    <fx:arrival xsi:type="nas:TfdmArrivalType" />
    <fx:departure xsi:type="nas:TfdmDepartureType" departurePointText="KDCA">
      <nas:offBlockTime><nas:scheduled>2030-01-02T15:00:00.000Z</nas:scheduled></nas:offBlockTime>
      <nas:standInformation standName="C35" />
    </fx:departure>
    <fx:destination xsi:type="nas:NasDestinationType" destinationPointText="KORD">
      <nas:standInformation standName="B9" />
    </fx:destination>
    <nas:additionalFlightInformation>
      <nas:nameValue name="TFDMID" value="SYNTH.QQQ1.DCA.ORD.0001" />
    </nas:additionalFlightInformation>
    <nas:flightIdentificationPrevious aircraftIdentification="QQQ123" />
  </nas:flight>
  <nas:metadata messageType="FlightAdd" />
</nas:NasMessage>"""

SYNTH_ELSEWHERE = SYNTH_FLIGHT_ADD.replace(b'departurePointText="KDCA"', b'departurePointText="KCLT"')

SYNTH_DELAY = b"""<x:FlightDelayMessage xmlns:x="urn:synthetic:not-the-real-schema"><x:item/></x:FlightDelayMessage>"""


# ── parsing ──────────────────────────────────────────────────────────────────

def test_departure_stand_parses_by_local_name():
    out = tp.parse_ttp_message(SYNTH_FLIGHT_ADD, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd"})
    assert out["data_group"] == "FlightData" and out["message_type"] == "FlightAdd"
    (f,) = out["flights"]
    assert f["callsign"] == "QQQ123"
    assert f["departure_airport"] == "KDCA" and f["destination_airport"] == "KORD"
    assert f["departure_stand"] == "C35" and f["arrival_stand"] == "B9"
    assert f["tfdm_id"] == "SYNTH.QQQ1.DCA.ORD.0001"
    assert f["sched_off_block"].startswith("2030-01-02T15:00")


def test_message_type_from_metadata_when_headers_absent():
    out = tp.parse_ttp_message(SYNTH_FLIGHT_ADD)
    assert out["data_group"] == "FlightData" and out["message_type"] == "FlightAdd"


@pytest.mark.parametrize("payload", [b"", b"   ", b"<not xml", b"\x00\xff garbage"])
def test_unparseable_or_header_only_messages_never_raise(payload):
    out = tp.parse_ttp_message(payload, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "Heartbeat"})
    assert out["flights"] == [] and out["message_type"] == "Heartbeat"


def test_other_data_groups_are_counted_not_parsed(monkeypatch):
    tp.reset_counts()
    called = []
    monkeypatch.setattr(tp, "record_stands", lambda parsed: called.append(parsed) or 0)
    assert tp.handle(SYNTH_DELAY, {"DATA_GROUP": "FlightDelay", "MESSAGE_TYPE": "DelayUpdate"}) is False
    assert tp.handle(b"", {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "PeriodicStart"}) is False
    assert called == []
    c = tp.counts()
    assert c[("FlightDelay", "DelayUpdate")] == 1 and c[("FlightData", "PeriodicStart")] == 1


# ── allow-list: gate only, DC or watched only ───────────────────────────────

@pytest.fixture
def captured(monkeypatch):
    from common import gate_assignments as ga
    seen = []

    def fake_record(observations, **kw):
        seen.extend(observations)
        return ga.RecordResult(stored=len(observations))
    monkeypatch.setattr(ga, "record", fake_record)
    monkeypatch.setattr(ga, "tdes_leg",
                        lambda cs, ap, dest, t: (ga.make_key("QQQ", "123", ap, dest, time.time()), "route_date"))
    # an arrival stand needs exactly one known instance of the route (arrival_leg); the fixture provides it
    monkeypatch.setattr(ga, "arrival_leg",
                        lambda cs, ap, dest, now=None: ga.make_key("QQQ", "123", ap, dest, time.time()))
    from ingest.parsers import smes_parser
    monkeypatch.setattr(smes_parser, "_match_watchlist_flight", lambda rec: None)
    return seen, smes_parser


def test_dc_departure_stand_is_departure_gate_evidence_only(captured):
    seen, _ = captured
    assert tp.handle(SYNTH_FLIGHT_ADD, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd"}) is True
    (o,) = seen
    assert (o.source, o.side, o.field, o.value_raw) == ("swim_tfdm", "departure", "gate", "C35")
    assert o.evidence_type == "assignment_msg" and o.airport == "KDCA"
    # the event key is the flight's stable fields, never a message timestamp
    assert o.event_key == "KDCA|QQQ123|SYNTH.QQQ1.DCA.ORD.0001"
    # no time / status / tail field exists on the observation to carry one
    assert not any(hasattr(o, k) for k in ("registration", "tail", "status", "oooi"))


def test_resync_repeat_has_the_same_event_key(captured):
    seen, _ = captured
    tp.handle(SYNTH_FLIGHT_ADD, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd", "SYNC": "per"})
    tp.handle(SYNTH_FLIGHT_ADD, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightUpdate", "SYNC": "rtm"})
    assert len(seen) == 2 and seen[0].content_key("C35") == seen[1].content_key("C35")


def test_non_dc_unwatched_departure_is_not_recorded(captured):
    seen, _ = captured
    assert tp.handle(SYNTH_ELSEWHERE, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd"}) is False
    assert seen == []


def test_non_dc_watched_departure_is_recorded(captured, monkeypatch):
    seen, sp = captured
    monkeypatch.setattr(sp, "_match_watchlist_flight", lambda rec: {"id": "wl-synth"})
    assert tp.handle(SYNTH_ELSEWHERE, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd"}) is True
    assert seen[0].airport == "KCLT"


def test_gate_store_failure_is_swallowed(monkeypatch):
    from common import gate_assignments as ga

    def boom(rec, **kw):
        raise RuntimeError("gate tables missing")
    monkeypatch.setattr(ga, "record_tfdm", boom)
    assert tp.handle(SYNTH_FLIGHT_ADD, {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd"}) is False


def test_swim_tfdm_is_a_tier_40_gate_source():
    from common import gate_assignments as ga
    assert ga.SOURCE_TIERS["swim_tfdm"] == 40 == ga.SOURCE_TIERS["swim_tdes"]


# ── 2026-11-24: arrival stand -> arrival gate, destination aerodrome's own publication only ──

# SYNTHETIC, the live shape (destination on <arrival>), a KCLT -> KDCA arrival published by KDCA.
SYNTH_ARRIVAL = b"""<nas:NasMessage xmlns:nas="http://www.faa.aero/nas/4.1" xmlns:fx="http://www.fixm.aero/flight/4.0">
 <nas:flight>
  <fx:aircraft/>
  <fx:arrival destinationPointText="KDCA"><nas:standInformation standName="D7"/></fx:arrival>
  <fx:departure departurePointText="KCLT">
   <nas:offBlockTime><nas:scheduled>2030-01-02T13:00:00.000Z</nas:scheduled></nas:offBlockTime>
  </fx:departure>
  <nas:flightIdentification aircraftIdentification="QQQ456"><nas:tfdmId>SYNTH.QQQ4.CLT.DCA.0002</nas:tfdmId></nas:flightIdentification>
 </nas:flight>
 <nas:metadata messageType="FlightUpdate"/>
</nas:NasMessage>"""
H_DCA = {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightUpdate", "AERODROME": "KDCA"}


def test_arrival_shape_parses_destination_from_arrival_element():
    (f,) = tp.parse_ttp_message(SYNTH_ARRIVAL, H_DCA)["flights"]
    assert f["destination_airport"] == "KDCA" and f["arrival_stand"] == "D7"
    assert f["departure_stand"] is None and f["aerodrome"] == "KDCA"


def test_dc_arrival_stand_from_the_destination_is_arrival_gate_evidence(captured):
    seen, _ = captured
    assert tp.handle(SYNTH_ARRIVAL, H_DCA) is True
    (o,) = seen
    assert (o.source, o.side, o.field, o.value_raw, o.airport) == ("swim_tfdm", "arrival", "gate", "D7", "KDCA")
    assert o.evidence_type == "assignment_msg" and o.confidence >= 0.8
    assert o.event_key == "KDCA|QQQ456|SYNTH.QQQ4.CLT.DCA.0002"


@pytest.mark.parametrize("hdr", [{"AERODROME": "KCLT"}, {}])
def test_arrival_stand_not_published_by_the_destination_is_not_recorded(captured, hdr):
    """Another aerodrome's idea of the far-end stand, or no AERODROME header at all: fail closed
    (tier 40 would otherwise outrank an observed ACARS arrival gate)."""
    seen, _ = captured
    assert tp.handle(SYNTH_ARRIVAL, {"DATA_GROUP": "FlightData", **hdr}) is False
    assert seen == []


def test_departure_publication_never_records_the_far_end_stand(captured):
    seen, _ = captured
    tp.handle(SYNTH_FLIGHT_ADD, {"DATA_GROUP": "FlightData", "AERODROME": "KDCA"})
    assert [(o.side, o.value_raw) for o in seen] == [("departure", "C35")]      # not KORD's B9


def test_non_dc_arrival_needs_a_watch(captured, monkeypatch):
    seen, sp = captured
    other = SYNTH_ARRIVAL.replace(b'destinationPointText="KDCA"', b'destinationPointText="KORD"')
    hdr = {"DATA_GROUP": "FlightData", "AERODROME": "KORD"}
    assert tp.handle(other, hdr) is False and seen == []
    monkeypatch.setattr(sp, "_match_watchlist_flight", lambda rec: {"id": "wl-synth"})
    assert tp.handle(other, hdr) is True and seen[0].side == "arrival" and seen[0].airport == "KORD"


def test_both_stands_go_in_one_leg_record(captured):
    seen, _ = captured
    both = SYNTH_FLIGHT_ADD.replace(b'departurePointText="KDCA"', b'departurePointText="KBWI"') \
                           .replace(b'destinationPointText="KORD"', b'destinationPointText="KDCA"')
    assert tp.handle(both, {"DATA_GROUP": "FlightData", "AERODROME": "DCA"}) is True    # 3-letter header agrees
    assert sorted((o.side, o.value_raw) for o in seen) == [("arrival", "B9"), ("departure", "C35")]
    assert len({str(o.key) for o in seen}) == 1


def test_record_tfdm_passes_registration_and_address_to_the_ladd_check(monkeypatch):
    from common import gate_assignments as ga
    got = {}
    monkeypatch.setattr(ga, "tdes_leg",
                        lambda cs, ap, dest, t: (ga.make_key("QQQ", "456", ap, dest, time.time()), "route_date"))
    monkeypatch.setattr(ga, "arrival_leg",
                        lambda cs, ap, dest, now=None: ga.make_key("QQQ", "456", ap, dest, time.time()))
    monkeypatch.setattr(ga, "record", lambda obs, **kw: got.update(kw, obs=obs) or ga.RecordResult(stored=len(obs)))
    rec = {"callsign": "QQQ456", "departure_airport": "KCLT", "destination_airport": "KDCA",
           "arrival_stand": "D7", "registration": "NQ0002", "aircraft_address": "abc124"}
    ga.record_tfdm(rec, sides=("arrival",))
    assert got["ladd_idents"] == ["NQ0002"] and got["ladd_hexes"] == ["abc124"]
    assert [o.side for o in got["obs"]] == ["arrival"]
    assert ga.record_tfdm(rec) == (None, None)          # default: departure only, and there is none


def test_arrival_gate_resolves_with_tfdm_above_acars():
    """Precedence is the resolver's, unchanged: a TFDM arrival gate (tier 40) wins over ACARS (30)."""
    from common import gate_assignments as ga
    rows = [{"id": 1, "source": "acars_airframes", "value_norm": "D9", "is_placeholder": 0,
             "observed_at": "2030-01-02T14:00:00Z", "leg_match": "route_date",
             "evidence_type": "assignment_msg", "confidence": 0.9, "flags": None},
            {"id": 2, "source": "swim_tfdm", "value_norm": "D7", "is_placeholder": 0,
             "observed_at": "2030-01-02T13:50:00Z", "leg_match": "route_date",
             "evidence_type": "assignment_msg", "confidence": 0.9, "flags": None}]
    r = ga.resolve(rows, now=ga._ep("2030-01-02T14:01:00Z"))
    assert (r.value, r.status, r.source) == ("D7", "observed", "swim_tfdm")


# ── ack-always (the subscription's lifeline) ────────────────────────────────

class _Msg:
    def __init__(self, payload):
        self._p = payload

    def get_payload_as_bytes(self):
        return self._p

    def get_sender_timestamp(self):
        return time.time() * 1000.0

    def get_property(self, name):
        return {"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd"}.get(name)


def _run_session_with(monkeypatch, handler, n_msgs=3):
    """Drive _NmsFeedSession._connect_and_receive against a fake Solace SDK."""
    import sys
    import types
    from ingest import swim_client as sc
    from ingest.config import NmsFeedConfig

    acked, stop = [], threading.Event()
    msgs = [_Msg(SYNTH_FLIGHT_ADD) for _ in range(n_msgs)]

    class Receiver:
        def start(self):
            pass

        def receive_message(self, timeout=0):
            if msgs:
                return msgs.pop(0)
            stop.set()
            return None

        def ack(self, m):
            acked.append(m)

        def terminate(self):
            pass

    class Builder:
        def with_message_client_acknowledgement(self):
            return self

        def with_message_auto_acknowledgement(self):
            return self

        def build(self, q):
            return Receiver()

    class Service:
        is_connected = True

        def connect(self):
            pass

        def disconnect(self):
            pass

        def create_persistent_message_receiver_builder(self):
            return Builder()

    class SB:
        def from_properties(self, p):
            return self

        def with_transport_security_strategy(self, t):
            return self

        def build(self):
            return Service()

    class TLS:
        @staticmethod
        def create():
            return TLS()

        def with_certificate_validation(self, **k):
            return self

    mods = {
        "solace": types.ModuleType("solace"),
        "solace.messaging": types.ModuleType("solace.messaging"),
        "solace.messaging.messaging_service": types.SimpleNamespace(
            MessagingService=types.SimpleNamespace(builder=lambda: SB())),
        "solace.messaging.resources": types.ModuleType("solace.messaging.resources"),
        "solace.messaging.resources.queue": types.SimpleNamespace(
            Queue=types.SimpleNamespace(durable_non_exclusive_queue=lambda n: n)),
        "solace.messaging.config": types.ModuleType("solace.messaging.config"),
        "solace.messaging.config.transport_security_strategy": types.SimpleNamespace(TLS=TLS),
        "solace.messaging.errors": types.ModuleType("solace.messaging.errors"),
        "solace.messaging.errors.pubsubplus_client_error": types.SimpleNamespace(
            PubSubPlusClientError=RuntimeError),
    }
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    monkeypatch.setattr(sc, "_bandwidth_priority_says_pause", lambda f: False)
    monkeypatch.setattr(sc, "_capture_bad_message", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_accumulate_feed_bytes", lambda *a, **k: None)
    monkeypatch.setattr(sc._db_pool, "submit", lambda *a, **k: None)
    cfg = NmsFeedConfig(host="tcps://example.invalid:55443", vpn="TFDM", username="u",
                        password="p", queue_name="q")
    sess = sc._NmsFeedSession("tfdm", cfg, handler, wants_headers=True)
    sess._stop = stop
    sess._connect_and_receive()
    return acked


def test_every_message_is_acked_even_when_the_handler_raises(monkeypatch):
    def handler(payload, headers):
        raise ValueError("schema surprise")
    assert len(_run_session_with(monkeypatch, handler, n_msgs=3)) == 3


def test_handler_gets_the_ttp_headers(monkeypatch):
    got = []
    _run_session_with(monkeypatch, lambda p, h: got.append(h) or False, n_msgs=1)
    assert got == [{"DATA_GROUP": "FlightData", "MESSAGE_TYPE": "FlightAdd"}]


def test_tfdm_is_client_ack_and_low_priority():
    from ingest import swim_client as sc
    assert "tfdm" in sc._ALL_SWIM_ACK_FEEDS and "tfdm" in sc._LOW_PRIORITY_FEEDS


# ── opt-in and not-configured ───────────────────────────────────────────────

def _run_feeds(monkeypatch, env):
    from ingest import swim_client as sc
    from ingest.config import NmsConfig
    for k in ("SWIM_NMS_TFDM_ENABLED", "SWIM_NMS_USER_TFDM", "SWIM_NMS_PASS_TFDM", "SWIM_NMS_QUEUE_TFDM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SWIM_NMS_SKIP_FEEDS", "fdps,stdds,tfms,fns,tbfm,itws")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    started, skips = [], []
    monkeypatch.setattr(sc._NmsFeedSession, "start", lambda self, ev: started.append(self.feed_name))
    monkeypatch.setattr(sc._db, "init_feed_usage", lambda f: None)
    monkeypatch.setattr(sc._db, "upsert_feed_skip", lambda *a: skips.append(a[0]))
    monkeypatch.setattr(sc, "_start_counter_flush_thread", lambda: None)
    monkeypatch.setattr(sc, "_stop_counter_flush_thread", lambda: None)

    async def go():
        stop = asyncio.Event()
        stop.set()
        await sc.run(NmsConfig(), stop)
    asyncio.run(go())
    return started, skips


def test_tfdm_never_starts_without_the_opt_in(monkeypatch):
    started, skips = _run_feeds(monkeypatch, {"SWIM_NMS_USER_TFDM": "u", "SWIM_NMS_PASS_TFDM": "p",
                                              "SWIM_NMS_QUEUE_TFDM": "q"})
    assert started == [] and skips == []


def test_tfdm_starts_with_the_opt_in(monkeypatch):
    started, _ = _run_feeds(monkeypatch, {"SWIM_NMS_TFDM_ENABLED": "true", "SWIM_NMS_USER_TFDM": "u",
                                          "SWIM_NMS_PASS_TFDM": "p", "SWIM_NMS_QUEUE_TFDM": "q"})
    assert started == ["tfdm"]


def test_not_configured_tfdm_container_exits_zero(monkeypatch, caplog):
    from ingest import main as im
    for k in ("SWIM_NMS_USER_TFDM", "SWIM_NMS_PASS_TFDM", "SWIM_NMS_QUEUE_TFDM"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {"SWIM_NMS_ENABLED": "true", "SWIM_NMS_TFDM_ENABLED": "true",
                 "SWIM_NMS_SKIP_FEEDS": "fdps,stdds,tfms,fns,tbfm,itws", "NWWS_ENABLED": "false",
                 "AMTRAK_ENABLED": "false", "LOCAL_AIRSPACE_ENABLED": "false"}.items():
        monkeypatch.setenv(k, v)

    def must_not_run(*a, **k):
        raise AssertionError("schema bootstrap / sources must not start when not configured")
    monkeypatch.setattr(im.db_backend, "backend", must_not_run)
    caplog.set_level("WARNING")
    asyncio.run(im.main())                     # returns -> process exits 0
    assert "tfdm: not configured" in caplog.text


def test_configured_or_shared_containers_do_not_take_the_exit(monkeypatch):
    from ingest import main as im
    from ingest import config
    base = {"SWIM_NMS_ENABLED": "true", "SWIM_NMS_TFDM_ENABLED": "true",
            "SWIM_NMS_SKIP_FEEDS": "fdps,stdds,tfms,fns,tbfm,itws", "NWWS_ENABLED": "false",
            "AMTRAK_ENABLED": "false", "LOCAL_AIRSPACE_ENABLED": "false",
            "SWIM_NMS_USER_TFDM": "u", "SWIM_NMS_PASS_TFDM": "p", "SWIM_NMS_QUEUE_TFDM": "q"}
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    assert im._only_unconfigured_tfdm(config.load()) is False
    monkeypatch.delenv("SWIM_NMS_USER_TFDM")
    assert im._only_unconfigured_tfdm(config.load()) is True
    monkeypatch.setenv("SWIM_NMS_SKIP_FEEDS", "stdds,tfms,fns,tbfm,itws")   # a container that also runs fdps
    assert im._only_unconfigured_tfdm(config.load()) is False
    monkeypatch.delenv("SWIM_NMS_TFDM_ENABLED")
    monkeypatch.setenv("SWIM_NMS_SKIP_FEEDS", "fdps,stdds,tfms,fns,tbfm,itws")
    assert im._only_unconfigured_tfdm(config.load()) is False


# ── wiring ───────────────────────────────────────────────────────────────────

def test_quadlet_is_opt_in_and_does_not_restart_loop():
    q = (REPO / ".config/containers/systemd/corporatetraveldc-ingest-tfdm.container").read_text()
    assert "Environment=SWIM_NMS_TFDM_ENABLED=true" in q
    assert "Environment=SWIM_NMS_SKIP_FEEDS=fdps,stdds,tfms,fns,tbfm,itws" in q
    assert "Restart=on-failure" in q and "Restart=always" not in q
    assert "EnvironmentFile=/etc/corporatetraveldc/svc/ingest.env" in q
    assert "dispatch-secrets.env\n" not in q.split("[Container]", 1)[1].split("#")[0]
    for other in ("fdps", "stdds", "tfms", "tbfm", "itws", "notam", "core"):
        oq = (REPO / f".config/containers/systemd/corporatetraveldc-ingest-{other}.container").read_text()
        assert "SWIM_NMS_TFDM_ENABLED" not in oq


def test_allowlist_and_template_carry_the_names_without_values():
    al = (REPO / "scripts/service-env/ingest.allowlist").read_text()
    tpl = (REPO / "dispatch-secrets.env.template").read_text()
    for k in ("HOST", "VPN", "USER", "PASS", "QUEUE"):
        assert f"SWIM_NMS_{k}_TFDM  #" in al
        assert f"\nSWIM_NMS_{k}_TFDM=\n" in tpl


def test_push_tfdm_never_degrades_healthz():
    from ingest import failover
    assert failover.is_optional("push:tfdm")


# -- 2026-11-24: an arrival stand is kept only for exactly one known instance of the route ----------------
def test_arrival_stand_without_a_known_instance_is_dropped(monkeypatch):
    """tdes_leg fell back to the record's date (route_date) and arrival_leg found no single instance:
    nothing is recorded -- never the wrong day's flight around midnight."""
    from common import gate_assignments as ga
    seen = []
    monkeypatch.setattr(ga, "tdes_leg",
                        lambda cs, ap, dest, t: (ga.make_key("QQQ", "456", ap, dest, time.time()), "route_date"))
    monkeypatch.setattr(ga, "arrival_leg", lambda cs, ap, dest, now=None: None)
    monkeypatch.setattr(ga, "record", lambda obs, **kw: seen.extend(obs) or ga.RecordResult(stored=len(obs)))
    rec = {"callsign": "QQQ456", "departure_airport": "KCLT", "destination_airport": "KDCA", "arrival_stand": "D7"}
    assert ga.record_tfdm(rec, sides=("arrival",)) == (None, None) and seen == []


def test_departure_stand_still_records_when_the_arrival_is_dropped(monkeypatch):
    from common import gate_assignments as ga
    seen = []
    monkeypatch.setattr(ga, "tdes_leg",
                        lambda cs, ap, dest, t: (ga.make_key("QQQ", "456", ap, dest, time.time()), "route_date"))
    monkeypatch.setattr(ga, "arrival_leg", lambda cs, ap, dest, now=None: None)
    monkeypatch.setattr(ga, "record", lambda obs, **kw: seen.extend(obs) or ga.RecordResult(stored=len(obs)))
    rec = {"callsign": "QQQ456", "departure_airport": "KDCA", "destination_airport": "KDCA",
           "departure_stand": "C35", "arrival_stand": "D7"}
    ga.record_tfdm(rec, sides=("departure", "arrival"))
    assert [(o.side, o.value_raw) for o in seen] == [("departure", "C35")]


def test_exact_tdes_match_is_reused_for_the_arrival(monkeypatch):
    from common import gate_assignments as ga
    seen = []
    k = ga.make_key("QQQ", "456", "KCLT", "KDCA", time.time())
    monkeypatch.setattr(ga, "tdes_leg", lambda cs, ap, dest, t: (k, "exact"))
    monkeypatch.setattr(ga, "arrival_leg", lambda *a, **kw: pytest.fail("not needed after an exact match"))
    monkeypatch.setattr(ga, "record", lambda obs, **kw: seen.extend(obs) or ga.RecordResult(stored=len(obs)))
    ga.record_tfdm({"callsign": "QQQ456", "departure_airport": "KCLT", "destination_airport": "KDCA",
                    "arrival_stand": "D7"}, sides=("arrival",))
    assert [(o.side, str(o.key), o.leg_match) for o in seen] == [("arrival", str(k), "exact")]


class _Inst:
    """A stand-in FlightInstance: scheduled times plus optional estimates/actuals."""
    def __init__(self, dep, arr=None, *, est_arr=None, act_dep=None, cancelled=False):
        from common import gate_assignments as ga
        self.sched_dep, self.sched_arr = dep, arr
        self.best_dep = act_dep or dep
        self.best_arr = est_arr or arr
        self.cancelled = cancelled
        self.key = ga.make_key("QQQ", "456", "KCLT", "KDCA", dep)


NOW = 1_900_000_000.0
H, M = 3600, 60


def _leg(monkeypatch, insts):
    from common import gate_assignments as ga
    monkeypatch.setattr(ga.fi, "resolve", lambda cs, o, d: insts)
    return ga.arrival_leg("QQQ456", "KCLT", "KDCA", now=NOW)


def test_one_airborne_flight_is_the_match(monkeypatch):
    target = _Inst(NOW - 1 * H, NOW + 30 * M)
    assert _leg(monkeypatch, [_Inst(NOW - 26 * H, NOW - 24 * H), target, _Inst(NOW + 22 * H, NOW + 24 * H)]) == target.key


def test_hourly_shuttle_picks_the_leg_in_the_air(monkeypatch):
    """45-minute legs every hour: at NOW the 13:30 departure is airborne; the 14:00 one has not left and the
    12:30 one has landed. Only the airborne leg matches (a fixed window would have seen several)."""
    legs = [_Inst(NOW + k * H - 30 * M, NOW + k * H + 15 * M) for k in range(-3, 4)]
    airborne = next(l for l in legs if l.best_dep <= NOW <= l.best_arr)
    assert _leg(monkeypatch, legs) == airborne.key


def test_shuttle_between_legs_with_nothing_airborne_uses_proportional_slack(monkeypatch):
    """Legs at -60..-15 min and +15..+60 min: nothing airborne at NOW; half-block slack (22.5 min) reaches
    neither, so nothing matches -- dropped, not guessed."""
    assert _leg(monkeypatch, [_Inst(NOW - 60 * M, NOW - 15 * M), _Inst(NOW + 15 * M, NOW + 60 * M)]) is None


def test_just_landed_within_slack_matches(monkeypatch):
    landed = _Inst(NOW - 3 * H, NOW - 10 * M)          # 10 min after arrival; slack is min(30 min, 85 min)
    assert _leg(monkeypatch, [landed]) == landed.key


def test_long_haul_is_covered_by_its_own_times(monkeypatch):
    lh = _Inst(NOW - 12 * H, NOW + 1 * H)
    assert _leg(monkeypatch, [lh]) == lh.key


def test_estimated_arrival_extends_the_window(monkeypatch):
    late = _Inst(NOW - 3 * H, NOW - 2 * H, est_arr=NOW + 20 * M)   # delayed: scheduled arrival long past
    assert _leg(monkeypatch, [late]) == late.key


def test_no_arrival_time_falls_back_to_the_route_median(monkeypatch):
    history = [_Inst(NOW - d * 24 * H - 2 * H, NOW - d * 24 * H) for d in (1, 2, 3)]   # 2 h blocks
    today = _Inst(NOW - 1 * H, None)                                                     # no arrival time
    assert _leg(monkeypatch, history + [today]) == today.key


def test_no_arrival_time_and_no_history_is_dropped(monkeypatch):
    assert _leg(monkeypatch, [_Inst(NOW - 1 * H, None)]) is None


def test_two_airborne_is_ambiguous(monkeypatch):
    assert _leg(monkeypatch, [_Inst(NOW - 2 * H, NOW + 1 * H), _Inst(NOW - 1 * H, NOW + 2 * H)]) is None


def test_cancelled_and_bad_block_times_are_ignored(monkeypatch):
    good = _Inst(NOW - 1 * H, NOW + 1 * H)
    assert _leg(monkeypatch, [_Inst(NOW - 1 * H, NOW + 1 * H, cancelled=True), _Inst(NOW - 2 * H, NOW + 30 * H), good]) == good.key


def test_unknown_flight_is_dropped(monkeypatch):
    assert _leg(monkeypatch, []) is None


def test_arrival_leg_survives_a_resolver_error(monkeypatch):
    from common import gate_assignments as ga
    def boom(*a):
        raise RuntimeError("db down")
    monkeypatch.setattr(ga.fi, "resolve", boom)
    assert ga.arrival_leg("QQQ456", "KCLT", "KDCA") is None
