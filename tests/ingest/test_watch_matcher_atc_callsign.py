"""
2026-10-20 (bellwether batch) -- SWIM watch matchers resolve a watch filed under a
PUBLIC flight number through (a) its ICAO form and (b) the codeshare_map lookaside
(relation 'atc_callsign' and 'codeshare'), with the route check: a lookaside match
must be proven on the watch's route, and another leg never advances the watch.

All numbers are synthetic (EK9871 filed as UAE9QZ, UA9520 operated as GJS9520):
nothing here is a real flight.
"""
import xml.etree.ElementTree as ET  # noqa: F401  (parity with the sibling matcher tests)

import pytest

import common.db as db
from common import flight_instance as fi

PUBLIC = {"id": "perm-flight-ek9871-test", "identifier": "EK9871", "origin": "OMDB",
          "destination": "KIAD", "scheduled_departure": None, "hex_id": None,
          "entry_type": "flight", "tier": "permanent"}


@pytest.fixture
def watches(monkeypatch):
    db.init_db_all()
    fi.clear_alias_cache()
    db.upsert_atc_callsign_link("EK9871", "UAE9QZ", "OMDB", "KIAD", "operator")
    entries = [dict(PUBLIC)]
    import shared.watchlist as wl
    monkeypatch.setattr(wl, "get_active_entries", lambda entry_type=None, **kw: entries)
    yield entries
    fi.clear_alias_cache()


# ── shared matcher ───────────────────────────────────────────────────────────

def test_alphanumeric_callsign_parses_to_the_fdps_split():
    assert fi.parse_flight_ident("UAE9QZ") == ("UAE", "9QZ")
    assert fi.parse_flight_ident("DLH9Y") == ("DLH", "9Y")
    assert fi.parse_flight_ident("abc1ef") is None          # a Mode-S hex, never a callsign
    assert fi.parse_flight_ident("EK9871") == ("UAE", "9871")


def test_watch_aliases_carry_their_relation(watches):
    rels = fi.watch_alias_relations(PUBLIC)
    assert rels["UAE9871"] == "identifier"
    assert rels["UAE9QZ"] == "atc_callsign"


# ── TFMS ─────────────────────────────────────────────────────────────────────

def test_tfms_matches_public_number_through_its_atc_callsign_on_route(watches):
    from ingest.parsers import tfms_parser as tp
    assert tp._match_watchlist_flight("UAE9QZ", origin="OMDB", destination="KIAD")["id"] == PUBLIC["id"]
    # ICAO form of the public number itself still matches (identifier relation)
    assert tp._match_watchlist_flight("UAE9871", origin="OMDB", destination="KIAD")["id"] == PUBLIC["id"]


def test_tfms_atc_callsign_other_leg_or_unproven_route_never_matches(watches):
    from ingest.parsers import tfms_parser as tp
    assert tp._match_watchlist_flight("UAE9QZ", origin="KIAD", destination="OMDB") is None   # return leg
    assert tp._match_watchlist_flight("UAE9QZ", origin="OMDB", destination="KJFK") is None   # other route
    assert tp._match_watchlist_flight("UAE9QZ") is None                                     # no airports


def test_tfms_codeshare_lookaside_also_needs_its_route(watches):
    from ingest.parsers import tfms_parser as tp
    db.upsert_codeshare_mapping("UAL", "9520", "GJS", "9520", "KDCA", "KORD", "test")
    watches[:] = [{"id": "wl-flight-ua9520-test", "identifier": "UA9520", "origin": "DCA",
                   "destination": "ORD", "scheduled_departure": None, "hex_id": None}]
    fi.clear_alias_cache()
    assert tp._match_watchlist_flight("GJS9520", origin="KDCA", destination="KORD")["id"] == \
        "wl-flight-ua9520-test"
    assert tp._match_watchlist_flight("GJS9520") is None


# ── TBFM ─────────────────────────────────────────────────────────────────────

def test_tbfm_matches_atc_callsign_by_its_metered_arrival_airport(watches):
    from ingest.parsers import tbfm_parser as bp
    assert bp._match_watchlist_flight("UAE9QZ", origin=None, destination="IAD")["id"] == PUBLIC["id"]
    assert bp._match_watchlist_flight("UAE9QZ", origin=None, destination="JFK") is None
    assert bp._match_watchlist_flight("UAE9QZ", origin=None, destination=None) is None


# ── FDPS ─────────────────────────────────────────────────────────────────────

def test_fdps_flight_plan_under_atc_callsign_fires_the_public_watch(watches, monkeypatch):
    from ingest.parsers import fdps_parser as fp
    import shared.watchlist as wl
    hits = []
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda eid, s, d, priority=3: hits.append(eid))
    monkeypatch.setattr(fp, "_fire_fdps_nas_alert", lambda *a, **k: None)
    fp.check_fdps_watchlist({"source": "FH", "callsign": "UAE9QZ", "gufi": "G-SYN-1",
                             "origin": "OMDB", "destination": "KIAD"})
    assert hits == [PUBLIC["id"]]
    hits.clear()
    fp.check_fdps_watchlist({"source": "FH", "callsign": "UAE9QZ", "gufi": "G-SYN-2",
                             "origin": "KIAD", "destination": "OMDB"})      # the return leg
    assert hits == []


# ── SMES / TDES surface ──────────────────────────────────────────────────────

def test_smes_surface_event_matches_atc_callsign_on_the_right_side(watches):
    from ingest.parsers import smes_parser as sp
    rec = {"callsign": "UAE9QZ", "airport": "KIAD", "event": "on",
           "event_time": "2026-10-20T13:00:00Z"}
    assert sp._match_watchlist_flight(rec)["id"] == PUBLIC["id"]
    assert sp._match_watchlist_flight({**rec, "event": "off"}) is None            # departure at IAD
    assert sp._match_watchlist_flight({**rec, "airport": "KJFK"}) is None         # arrival elsewhere


# ── retirement / relink ──────────────────────────────────────────────────────

def test_relinked_callsign_moves_the_watch_and_the_old_one_stops_matching(watches):
    from ingest.parsers import tfms_parser as tp
    res = db.upsert_atc_callsign_link("EK9871", "UAE9QW", "OMDB", "KIAD", "route_continuity")
    assert res["action"] == "relinked" and res["old_callsign"] == "UAE9QZ"
    fi.clear_alias_cache()
    assert tp._match_watchlist_flight("UAE9QZ", origin="OMDB", destination="KIAD") is None
    assert tp._match_watchlist_flight("UAE9QW", origin="OMDB", destination="KIAD")["id"] == PUBLIC["id"]


# ── shared/watchlist.py resolution ───────────────────────────────────────────

def test_watch_callsigns_include_icao_form_and_atc_link(watches):
    import shared.watchlist as wl
    assert wl._watch_callsigns(PUBLIC, "EK9871") == ["EK9871", "UAE9871", "UAE9QZ"]


def test_resolve_flight_identity_finds_local_adsb_under_the_atc_callsign(watches, monkeypatch):
    import shared.watchlist as wl
    seen = []

    def fake_local(cs):
        seen.append(cs)
        return {"hex": "f0f0f0", "r": None, "flight": cs} if cs == "UAE9QZ" else None

    monkeypatch.setattr(wl, "_local_ac_by_callsign", fake_local)
    monkeypatch.setattr(wl, "_local_registry_hex_lookup", lambda ident: None)
    monkeypatch.setattr(wl, "_local_fdps_ac", lambda cs: None)
    monkeypatch.setattr(wl, "_remote_ac_by_callsign", lambda cs: None)
    monkeypatch.setattr(db, "set_watchlist_identity", lambda *a, **k: None)
    monkeypatch.setattr(wl, "watchlist_event_hit", lambda *a, **k: None)
    ac = wl.resolve_flight_identity(dict(PUBLIC), "EK9871")
    assert ac and ac["hex"] == "f0f0f0"
    assert seen[:3] == ["EK9871", "UAE9871", "UAE9QZ"]


def test_tfms_departure_state_reads_the_atc_callsign_row(watches):
    import shared.watchlist as wl
    db.upsert_flight_ooooi("G-SYN-OOOI", callsign="UAE9QZ", origin="OMDB", destination="KIAD",
                           airline_off_time="2026-10-19T03:10:00Z")
    st = wl.tfms_departure_state("EK9871", "OMDB", "KIAD")
    assert st == ("off", "2026-10-19T03:10:00Z")
    assert wl.tfms_departure_state("EK9871", "KIAD", "OMDB") is None             # other direction
