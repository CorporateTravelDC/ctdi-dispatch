"""
2026-10-08, security review 06.

1. LADD lookup: the CUI filter files store US registrations WITH the leading N
   ("N01AB"); the FAA registry stores them without ("01AB"). faa_is_ladd()
   stripped the N from every input and matched nothing: 0 of 200 sampled US
   entries were found on the live table. Identifiers here are synthetic.
2. FDPS callsign split: a GA registration flying as its own callsign is
   stored by the FDPS parser as airline=cs[:3], flight_num=cs[3:]; the
   airline-shaped regex in get_flight_plan_by_callsign() never matched it, so
   every private-tail watch reported FDPS:N.
"""
import tempfile
from pathlib import Path

import common.db as db


def _tmpdb():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def test_lookup_keys_cover_both_registration_forms_and_never_strip_callsigns():
    assert db.ladd_lookup_keys("N01AB") == ["N01AB", "01AB"]
    assert db.ladd_lookup_keys("n01ab ") == ["N01AB", "01AB"]
    assert db.ladd_lookup_keys("01AB") == ["01AB", "N01AB"]
    assert db.ladd_lookup_keys("NQQ000") == ["NQQ000"]          # flight-ID, not a registration
    assert db.ladd_lookup_keys("QZZZZ") == ["QZZZZ"]            # foreign mark
    assert db.ladd_lookup_keys("") == []


def test_faa_is_ladd_finds_entries_stored_as_the_cui_files_list_them():
    orig = db._db_path
    path = _tmpdb()
    try:
        db._db_path = lambda: path
        db.init_db_all()
        db.faa_upsert_ladd(["N01AB", "NQQ000", "QZZZZ", "04CD"])
        # the shapes that missed on the live table before the fix
        assert db.faa_is_ladd("N01AB")           # as broadcast / as listed
        assert db.faa_is_ladd("01AB")            # registry form (faa_lookup_by_* pass this)
        assert db.faa_is_ladd("NQQ000")          # flight ID starting with N
        assert db.faa_is_ladd("QZZZZ")
        assert db.faa_is_ladd("N04CD")           # an older row stored without the N
        assert not db.faa_is_ladd("N09ZZ")
        assert not db.faa_is_ladd("QQ000")       # never strip letters off a flight ID
    finally:
        db._db_path = orig
        path.unlink(missing_ok=True)


def test_registry_lookup_reports_ladd_for_a_listed_us_registration():
    orig = db._db_path
    path = _tmpdb()
    try:
        db._db_path = lambda: path
        db.init_db_all()
        db.faa_upsert_ladd(["N01AB"])
        with db.conn() as c:
            c.execute("INSERT INTO faa_aircraft_registry (n_number, mode_s_hex, updated_at) VALUES (?, ?, ?)", ("01AB", "119a75", 0))
        assert db.faa_lookup_by_n_number("N01AB")["ladd"] is True
        assert db.faa_lookup_by_hex("119A75")["ladd"] is True
    finally:
        db._db_path = orig
        path.unlink(missing_ok=True)


def test_fdps_lookup_finds_a_registration_callsign():
    orig = db._db_path
    path = _tmpdb()
    try:
        db._db_path = lambda: path
        db.init_db_all()
        db.upsert_flight_event("00000000-0000-0000-0000-000000000001", "N00", "0AA", "KSFO", "KIAD", None,
                               None, None, "active", 41.7, -103.5, 41000, 496,
                               '<flight><departure departurePoint="KSFO"/><arrival arrivalPoint="KIAD"/></flight>')
        db.upsert_flight_event("00000000-0000-0000-0000-000000000002", "ZZZ", "1234", "KSFO", "KIAD", None,
                               None, None, "active", None, None, None, None, "<flight/>")
        plan = db.get_flight_plan_by_callsign("N000AA")
        assert plan is not None and plan.get("destination") == "KIAD"
        assert db.get_flight_plan_by_callsign("n000aa") is not None
        assert db.get_flight_plan_by_callsign("ZZZ1234") is not None    # airline callsigns unchanged
        assert db.get_flight_plan_by_callsign("N00") is None            # too short to split
        assert db.get_flight_plan_by_callsign("N0 00AA") is None        # not a callsign
    finally:
        db._db_path = orig
        path.unlink(missing_ok=True)


def test_registry_fetcher_no_longer_replaces_the_ladd_table():
    src = (Path(__file__).resolve().parents[2] / "src/poller/fetchers/faa_registry.py").read_text()
    live = [l for l in src.splitlines() if "faa_upsert_ladd" in l and not l.lstrip().startswith("#")]
    assert live == [], f"the public LADD download must not full-replace the CUI-sourced list: {live}"
