"""NOTAM changes 2026-10-20 (operator): VIP callsigns need a number, every US
Antarctic station is a keep class, and GML-id ("Event_...") duplicates are
absorbed (year derived from issued)."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ingest.parsers.aim_parser as aim_parser  # noqa: E402

MCM_TEXT = ("FIXED-WING ACFT PROHIBITED WITHIN 1.5NM RADIUS OF MCM HELIPAD (S7751.000 E16640.400) "
            "SFC-2000FT UNLESS ON AN APPROVED IFR PROCEDURE. AREA RESERVED FOR HELICOPTER OPERATIONS.")


# ── 1. VIP callsigns ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["SAM41 ARRIVING", "MCM47 DEPARTS", "VENUS 2 MOVEMENT", "SAM 28000 INBOUND"])
def test_numbered_vip_callsigns_still_match(text):
    assert aim_parser._is_vip_notam(text)


@pytest.mark.parametrize("text", [MCM_TEXT, "SAM SITE ACTIVE", "VENUS TRANSIT OBS", "MCM 1.5NM RADIUS",
                                  "SAM 200FT AGL", "SAMPLE TEXT", "TRANSAM TOWER"])
def test_bare_words_and_measurements_are_not_vip(text):
    assert not aim_parser._is_vip_notam(text)


# ── 2. US Antarctic keep class ──────────────────────────────────────────────

@pytest.mark.parametrize("n,expected", [
    ({"facility": "NZWD", "fir": "NZZO", "text_body": MCM_TEXT}, True),                 # coordinate south of 60
    ({"facility": "NZCM", "fir": "", "text_body": MCM_TEXT}, True),                     # empty FIR, NZ designator
    ({"facility": "NZFX", "fir": "NZZO", "text_body": "RWY 15/33 CLSD"}, True),         # no coordinate: live table
    ({"facility": "NZXX", "fir": "NZZO", "text_body": "RWY CLSD"}, False),              # unseen designator, no position
    ({"facility": "NZXX", "fir": "NZZO", "text_body": "OBST 775100S1664024E"}, True),   # unseen, but coordinate
    ({"facility": "NZAA", "fir": "NZZO", "text_body": "OBST 370030S1744730E"}, False),  # Auckland, 37 S
    ({"facility": "KDCA", "fir": "ZDC", "text_body": "OBST 385100N0770200W"}, False),
])
def test_is_us_antarctic(n, expected):
    assert aim_parser.is_us_antarctic(n) is expected


def _patched():
    return mock.patch.multiple(aim_parser, db=mock.DEFAULT, _get_transient_airports=mock.DEFAULT,
                               _get_facility_filter=mock.DEFAULT, is_core_airport=mock.DEFAULT,
                               _fire_notam_alert=mock.DEFAULT, _maybe_cleanup_expired=mock.DEFAULT)


def _notam(**kw):
    n = {"notam_id": "NZWD/2026/202", "facility": "NZWD", "location": "NZWD", "fir": "NZZO",
         "classification": "NOTAM-D", "effective_start": None, "effective_end": None,
         "text_body": MCM_TEXT, "raw_json": json.dumps({"number": "202", "issued": "2026-10-20T19:46:00.000Z"})}
    n.update(kw)
    return n


def _run(n):
    with _patched() as p:
        p["_get_transient_airports"].return_value = frozenset()
        p["_get_facility_filter"].return_value = frozenset({"KDCA"})
        p["is_core_airport"].return_value = False
        p["db"].delete_notam_event_aliases.return_value = 0
        written = aim_parser.write_aim_notams([n])
    return written, p


def test_antarctic_notam_stored_but_not_alerted():
    written, p = _run(_notam())
    assert written == 1
    p["db"].upsert_notam.assert_called_once()
    p["_fire_notam_alert"].assert_not_called()


# ── 4. Event_ dedupe ────────────────────────────────────────────────────────

def test_year_derived_from_issued():
    assert aim_parser.notam_year_from_issued("2026-10-20T19:46:00.000Z") == "2026"
    assert aim_parser.notam_year_from_issued("") == ""
    assert aim_parser.notam_year_from_issued(None) == ""


def test_canonical_write_absorbs_event_aliases_and_event_write_does_not():
    _, p = _run(_notam())
    p["db"].delete_notam_event_aliases.assert_called_once_with("NZWD", "202", "2026-10-20T19:46:00.000Z")
    _, p = _run(_notam(notam_id="Event_1_7099843419935658"))
    p["db"].delete_notam_event_aliases.assert_not_called()


def _with_sqlite(fn):
    import common.db as db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    close = getattr(db, "close_thread_connection", lambda: None)
    try:
        close()
        db._db_path = lambda: Path(tmp.name)
        db.init_db_all()
        return fn(db)
    finally:
        close()
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)


def _raw(nid, num="202", issued="2026-10-20T19:46:00.000Z", year=""):
    return json.dumps({"notam_id": nid, "number": num, "year": year, "location": "NZWD",
                       "issued": issued, "source": "swim_aim"})


def test_delete_notam_event_aliases_sql():
    def body(db):
        db.upsert_notam("Event_1_1", _raw("Event_1_1"), "NZWD", "NOTAM-D", None, None, "T")
        db.upsert_notam("Event_1_2", _raw("Event_1_2", num="203"), "NZWD", "NOTAM-D", None, None, "T")
        db.upsert_notam("Event_1_3", _raw("Event_1_3"), "NZCM", "NOTAM-D", None, None, "T")
        assert db.delete_notam_event_aliases("NZWD", "202", "2026-10-20T19:46:00.000Z") == 1
        with db.conn() as c:
            left = {r["notam_id"] for r in c.execute("SELECT notam_id FROM notams").fetchall()}
        assert left == {"Event_1_2", "Event_1_3"}
    _with_sqlite(body)


def _dedupe_module():
    spec = importlib.util.spec_from_file_location("ned", ROOT / "scripts/notam-event-dedupe.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_cleanup_plan_deletes_rekeys_and_leaves():
    m = _dedupe_module()
    rows = [{"notam_id": "Event_a", "raw_json": _raw("Event_a")},                    # canonical exists -> delete
            {"notam_id": "Event_b", "raw_json": _raw("Event_b", num="300")},         # -> re-key
            {"notam_id": "Event_c", "raw_json": _raw("Event_c", num="300")},         # same canonical -> delete
            {"notam_id": "Event_d", "raw_json": _raw("Event_d", num="", issued="")}]  # no key -> left
    delete, rekey, left = m.plan(rows, {"NZWD/2026/202"})
    assert delete == ["Event_a", "Event_c"]
    assert [(o, n) for o, n, _ in rekey] == [("Event_b", "NZWD/2026/300")]
    assert json.loads(rekey[0][2])["year"] == "2026"
    assert left == 1


def test_cleanup_end_to_end_is_idempotent(capsys):
    m = _dedupe_module()

    def body(db):
        db.upsert_notam("NZWD/2026/202", _raw("NZWD/2026/202", year="2026"), "NZWD", "NOTAM-D", None, None, "T")
        db.upsert_notam("Event_a", _raw("Event_a"), "NZWD", "NOTAM-D", None, None, "T")
        db.upsert_notam("Event_b", _raw("Event_b", num="300"), "NZWD", "NOTAM-D", None, None, "T")
        assert m.main([]) == 0
        assert m.main([]) == 0
        with db.conn() as c:
            ids = {r["notam_id"] for r in c.execute("SELECT notam_id FROM notams").fetchall()}
        assert ids == {"NZWD/2026/202", "NZWD/2026/300"}
    _with_sqlite(body)
    out = capsys.readouterr().out
    assert "Event_" not in out.replace("Event_ rows", "")   # counts only, never identifiers
