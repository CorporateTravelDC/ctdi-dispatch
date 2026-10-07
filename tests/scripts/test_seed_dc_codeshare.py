"""
tests/scripts/test_seed_dc_codeshare.py -- 2026-10-05 JZA825 / AC8825 / UA8366:
Air Canada Express by Jazz markets AC(8000 + n) for operating JZA n; the
DCA/IAD boards' codeshare lists (UA8366) map to the same operating flight.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from common import db  # noqa: E402

spec = importlib.util.spec_from_file_location("seed_dc_codeshare", ROOT / "scripts" / "seed-dc-codeshare.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


@pytest.fixture
def clean():
    db.init_db_all()
    with db.conn() as c:
        c.execute("DELETE FROM codeshare_map")
        c.execute("DELETE FROM flight_events WHERE airline = 'JZA'")
    yield


def test_marketing_number_rule():
    assert seed.marketing_number("JZA", "825") == "8825"
    assert seed.marketing_number("JZA", "781") == "8781"
    assert seed.marketing_number("EDV", "5134") == "5134"          # US regional: equal numbers


def test_seed_fixes_jazz_and_adds_board_codeshares(clean, monkeypatch):
    db.upsert_codeshare_mapping("ACA", "825", "JZA", "825", "CYUL", "KDCA", "dc_metro_seed")   # the old wrong row
    with db.conn() as c:
        c.execute("INSERT INTO flight_events (flight_id, airline, flight_num, origin, destination, updated_at) "
                  "VALUES ('t-jza825', 'JZA', '825', 'CYUL', 'KDCA', ?)", (time.time(),))
    board = {"arrivals": [{"IATA": "AC", "flightnumber": "8825", "codeshare": [{"IATA": "UA", "flightnumber": "8366"}]}],
             "departures": []}
    import common.airport_fids as fids
    monkeypatch.setattr(fids, "get_data", lambda ap, force=False: board if ap == "DCA" else {})
    assert seed.main() == 0
    assert db.get_codeshare_mapping_by_marketing("ACA", "825") == []
    ac = db.get_codeshare_mapping_by_marketing("ACA", "8825")
    ua = db.get_codeshare_mapping_by_marketing("UAL", "8366")
    assert ac and (ac[0]["operating_carrier"], ac[0]["operating_flight_num"]) == ("JZA", "825")
    assert ua and (ua[0]["operating_carrier"], ua[0]["operating_flight_num"], ua[0]["source"]) == ("JZA", "825", "fids_codeshare")


def test_marketed_numbers_resolve_to_the_operating_flight(clean):
    db.upsert_codeshare_mapping("ACA", "8825", "JZA", "825", "CYUL", "KDCA", "dc_metro_seed")
    db.upsert_codeshare_mapping("UAL", "8366", "JZA", "825", "CYUL", "KDCA", "fids_codeshare")
    for ident in ("AC8825", "ACA8825", "UA8366", "UAL8366", "ac8825"):
        assert db.resolve_operating_callsign(ident) == "JZA825", ident
    assert db.resolve_operating_callsign("JZA825") is None
    assert db.resolve_operating_callsign("DL123") is None
