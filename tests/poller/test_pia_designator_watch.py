"""2026-11-02: the FAA JO 7340.2 monitor that keeps the withheld PIA call-sign prefixes
current. Parsers against page shapes copied from the live FAA pages; reconcile adds a
newly named provider at once, alerts (never removes) on a missing prefix, and the shared
LADD gate picks the addition up. No network: fetch and alert are replaced."""
from __future__ import annotations

import time
from datetime import date

import pytest

from common import db
from poller.skills import pia_designator_watch as w

INDEX = ('<html><body><h1>FAA Order JO 7340.2P - Contractions</h1><p>Effective: 7/9/2026</p>'
         '<p>Change: Change 3</p></body></html>')
INDEX_R = INDEX.replace("7340.2P", "7340.2R").replace("7/9/2026", "10/29/2026").replace("Change 3", "Basic")
ROW = '<tr><td><p>{0}</p></td><td><p>{1}</p></td><td><p>{2}</p></td><td><p>{3}</p></td></tr>'
BASE_ROWS = [("AERONAUTICAL RADIO, INC. (ARINC)", "UNITED STATES", "ROCKFISH", "XAA"),
             ("FLIGHTAWARE (HOUSTON, TX)", "UNITED STATES", "FLIGHTAWARE", "FWR"),
             ("FLTPLAN, LLC (SOUTHBURY, CT)", "UNITED STATES", "DOT COM", "DCM"),
             ("FOREFLIGHT, LLC", "UNITED STATES", "FOREFLIGHT", "FFL"),
             ("PAKISTAN INTERNATIONAL AIRLINES CORPORATION (PIA)", "PAKISTAN", "PAKISTAN", "PIA"),
             ("EXAMPLE PRIVACY CALLSIGNS, LLC", "UNITED STATES", "QUIETSKY", "QSK")]
PRIVACY = ('<p>For the purposes of this program, a third-party call sign can be obtained from FlightPlan, '
           'ForeFlight, FlightAware, or ARINCDirect.</p>')


def _table(rows):
    return "<table><tr><th>Company</th><th>Country</th><th>Telephony</th><th>3-Ltr</th></tr>" + \
        "".join(ROW.format(*r) for r in rows) + "</table>"


@pytest.fixture
def env(monkeypatch):
    db.init_db_all()
    with db.conn() as c:
        w._ensure(c)
        c.execute("DELETE FROM pia_callsign_prefixes")
        c.execute("DELETE FROM pia_watch_state")
    alerts = []
    monkeypatch.setattr(w, "alert", lambda title, body: alerts.append(title))
    db._pia_cache["at"] = 0.0
    return alerts


def test_parsers_read_the_live_page_shapes():
    assert w.parse_edition(INDEX) == "7340.2P Change 3 Effective 7/9/2026"
    d = w.parse_designators(_table(BASE_ROWS))
    assert d["XAA"]["telephony"] == "ROCKFISH" and d["DCM"]["telephony"] == "DOT COM"
    providers = w.parse_providers(PRIVACY)
    assert providers == ["FlightPlan", "ForeFlight", "FlightAware", "ARINCDirect"]
    assert {p: w.match_designator(p, d) for p in providers} == \
        {"FlightPlan": "DCM", "ForeFlight": "FFL", "FlightAware": "FWR", "ARINCDirect": "XAA"}


def test_cycle_is_112_days_from_the_published_schedule():
    assert w.expected_effective(date(2026, 10, 29)) == date(2026, 10, 29)
    assert w.expected_effective(date(2026, 11, 2)) == date(2027, 2, 18)
    assert w.expected_effective(date(2027, 6, 11)) == date(2027, 9, 30)


def test_new_provider_is_withheld_at_once_and_reaches_the_gate(env):
    d = w.parse_designators(_table(BASE_ROWS))
    providers = w.parse_providers(PRIVACY.replace("or ARINCDirect", "ARINCDirect, or Example Privacy Callsigns"))
    assert not db.is_pia_callsign("QSK123")
    w.full_check(d, providers, time.time())
    assert "PIA watch: new provider withheld" in env
    db._pia_cache["at"] = 0.0
    assert db.is_pia_callsign("QSK123") and db.faa_ladd_listed_batch(["QSK123"]) == {"QSK123"}


def test_missing_prefix_alerts_and_is_never_removed(env):
    d = w.parse_designators(_table([r for r in BASE_ROWS if r[3] != "DCM"]))
    lines = w.full_check(d, w.parse_providers(PRIVACY), time.time())
    assert "PIA watch: withheld prefix missing from JO 7340.2" in env
    assert any("DCM: MISSING" in ln for ln in lines)
    assert db.is_pia_callsign("DCM123")                                   # still withheld


def test_unmatched_provider_alerts_without_guessing(env):
    d = w.parse_designators(_table(BASE_ROWS))
    w.full_check(d, ["FlightPlan", "Brand New Provider"], time.time())
    assert "PIA watch: provider without a designator" in env
    assert set(w.withheld_prefixes()) == set(db.PIA_CALLSIGN_PREFIXES)


def test_main_checks_daily_and_rechecks_on_a_new_edition(env, monkeypatch):
    pages = {w.INDEX_URL: INDEX, w.TABLE_URL: _table(BASE_ROWS * 200), w.PRIVACY_URL: PRIVACY}
    calls = []
    monkeypatch.setattr(w, "fetch", lambda url, timeout=30: calls.append(url) or pages[url])
    monkeypatch.setattr(w, "parse_designators", _many)
    assert w.main() == 0 and calls == [w.INDEX_URL, w.TABLE_URL, w.PRIVACY_URL]   # first run: full check
    calls.clear()
    assert w.main() == 0 and calls == []                                   # same day: nothing fetched
    assert w.main(force=True) == 0 and w.TABLE_URL in calls
    calls.clear()
    st = w._get_state()
    w._set_state(last_index_try=0)
    pages[w.INDEX_URL] = INDEX_R
    assert w.main() == 0 and w.TABLE_URL in calls and "PIA watch: JO 7340.2 updated" in env
    assert w._get_state()["edition"].startswith("7340.2R") and st["edition"].startswith("7340.2P")


def _many(h):
    """The real parse plus filler rows, so the 'table unreadable' guard (< 1000) passes."""
    d = {f"{chr(65 + i // 676)}{chr(65 + i // 26 % 26)}{chr(65 + i % 26)}": {"company": "FILLER", "country": "X",
                                                                          "telephony": "X"} for i in range(1100)}
    d.update(_real_parse(h))
    return d


_real_parse = w.parse_designators
