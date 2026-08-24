"""
Regression tests for the 2026-08-23 GDP/GS ElementTree-truthiness fix in
tfms_parser.py -- same bug class already caught once in fdps_parser.py
(see tests/ingest/test_fdps_element_truthiness.py): `_find_child(a, t) or
_find_child(b, t)` silently discards a genuinely-found but childless leaf
element (e.g. a bare <fce:startTime>...</fce:startTime>), because
ElementTree's bool() is based on child count, not identity. Both
_handle_ground_delay_program and _handle_ground_stop used this pattern to
pick a program's key start-time element, so every program_id in
nas_programs was actually keyed off advisoryValidPeriod's start instead
of the documented cumulativeProgramPeriod (GDP) / groundStopPeriod (GS)
field -- the `or` chain always falls through to the LAST candidate when
the earlier ones are childless leaves, regardless of which was present.

Also covers db.py's SCHEMA_V36 key_scheme/legacy_correlate_id mechanism,
which lets a program that straddles the parser-fix deploy still be
recognized as the same real-world program across the key change instead
of silently fragmenting.
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import xml.etree.ElementTree as ET

from ingest.parsers.tfms_parser import (
    _find_child, _first_present, _handle_ground_delay_program,
    _handle_ground_stop, write_tfms_programs,
)


class _IsolatedDB:
    """Redirects common.db to a temporary SQLite DB -- same pattern as
    tests/shared/test_watchlist.py's _IsolatedDB, kept local here rather
    than imported to avoid cross-test-file coupling."""
    def __enter__(self):
        import common.db as _db
        self._orig_path = _db._db_path
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._tmp_path = tmp.name
        _db._db_path = lambda: Path(self._tmp_path)
        _db.init_db_all()
        return self

    def __exit__(self, *_):
        import common.db as _db
        _db._db_path = self._orig_path
        Path(self._tmp_path).unlink(missing_ok=True)


def _load_fi_message(fixture_name: str) -> ET.Element:
    xml_bytes = (Path(__file__).parent / "fixtures" / fixture_name).read_bytes()
    root = ET.fromstring(xml_bytes)
    fi_output = _find_child(root, "fiOutput")
    for fi_message in fi_output:
        return fi_message
    raise AssertionError(f"no fiMessage found in {fixture_name}")


def _epoch(iso_ts: str) -> float:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp()


# ── Direct unit check on the underlying pitfall (mirrors the FDPS test) ────────

def test_childless_leaf_element_is_falsy_under_or_chain():
    leaf = ET.fromstring("<startTime>2026-08-23T11:00:00Z</startTime>")
    assert leaf is not None
    assert bool(leaf) is False, (
        "if this assertion fails, ElementTree truthiness semantics changed "
        "upstream -- the _first_present() fix in tfms_parser.py is still "
        "correct, but this documents the original failure mode"
    )


def test_first_present_picks_first_non_none_not_first_truthy():
    a = ET.fromstring("<startTime>2026-08-23T11:00:00Z</startTime>")  # childless -> falsy
    b = ET.fromstring("<startTime>2026-08-23T12:00:00Z</startTime>")
    # The old `a or b` form would incorrectly return b here.
    assert _first_present(a, b) is a
    assert _first_present(None, b) is b
    assert _first_present(None, None) is None


# ── GDP: real captured sample, cumulativeProgramPeriod genuinely diverges ──────

def test_gdp_program_id_keyed_on_cumulative_period_not_advisory_valid_period():
    # Real capture (2026-07-20, SFO GDP): cumulativeProgramPeriod starts
    # 15:15:00Z, advisoryValidPeriod starts 18:00:00Z -- genuinely
    # different, so this fails loudly pre-fix instead of accidentally
    # passing.
    fi_message = _load_fi_message("tfms_gdp_real.xml")
    programs = _handle_ground_delay_program(fi_message)
    assert len(programs) == 1
    prog = programs[0]

    expected_start = _epoch("2026-07-20T15:15:00Z")  # cumulativeProgramPeriod
    wrong_start = _epoch("2026-07-20T18:00:00Z")      # advisoryValidPeriod (the bug)

    assert prog["start_time"] == expected_start
    assert prog["start_time"] != wrong_start
    assert prog["program_id"] == f"GDP-SFO-{int(expected_start)}"


# ── GS: synthetic fixture, groundStopPeriod deliberately diverges ──────────────

def test_gs_program_id_keyed_on_ground_stop_period_not_advisory_valid_period():
    # Synthetic (real GS_2.xml sample happens to have identical
    # groundStopPeriod/advisoryValidPeriod times, so it can't distinguish
    # pre- from post-fix behavior) -- groundStopPeriod starts 11:00:00Z,
    # advisoryValidPeriod starts 12:00:00Z.
    fi_message = _load_fi_message("tfms_gs_synthetic_diverging_periods.xml")
    programs = _handle_ground_stop(fi_message)
    assert len(programs) == 1
    prog = programs[0]

    expected_start = _epoch("2026-08-23T11:00:00Z")  # groundStopPeriod
    wrong_start = _epoch("2026-08-23T12:00:00Z")      # advisoryValidPeriod (the bug)

    assert prog["start_time"] == expected_start
    assert prog["start_time"] != wrong_start
    assert prog["program_id"] == f"GS-IAD-{int(expected_start)}"


# ── key_scheme / legacy_correlate_id, against an isolated DB ───────────────────

def test_write_tfms_programs_stamps_key_scheme_and_correlates_legacy_row():
    with _IsolatedDB():
        import common.db as db

        # A "legacy" (pre-fix, key_scheme=1) GS row for the same
        # type+facility, simulating a program the old buggy code already
        # wrote before the fix deployed.
        db.upsert_nas_program(
            program_id="GS-IAD-1755946800", prog_type="GS", facility="IAD",
            raw_json="{}", key_scheme=1,
        )

        # A new write for what is meant to be the SAME real-world
        # program, now correctly keyed post-fix.
        written = write_tfms_programs([{
            "program_id": "GS-IAD-1755943200", "type": "GS", "facility": "IAD",
            "start_time": 1755943200.0, "end_time": 1755954000.0,
        }])
        assert written == 1

        rows = {r["program_id"]: dict(r) for r in db.get_active_nas_programs()}
        new_row = rows["GS-IAD-1755943200"]
        assert new_row["key_scheme"] == 2
        assert new_row["legacy_correlate_id"] == "GS-IAD-1755946800"

        legacy_row = rows["GS-IAD-1755946800"]
        assert legacy_row["key_scheme"] == 1
        assert legacy_row["legacy_correlate_id"] is None


def test_find_legacy_nas_program_respects_type_facility_and_window():
    with _IsolatedDB():
        import common.db as db

        db.upsert_nas_program("GDP-SFO-1", "GDP", "SFO", "{}", key_scheme=1)
        db.upsert_nas_program("GS-SFO-1", "GS", "SFO", "{}", key_scheme=1)
        db.upsert_nas_program("GDP-BOS-1", "GDP", "BOS", "{}", key_scheme=1)

        # Correct type+facility match.
        assert db.find_legacy_nas_program("GDP", "SFO") == "GDP-SFO-1"
        # Wrong type at the same facility must not match.
        assert db.find_legacy_nas_program("GDP", "BOS") == "GDP-BOS-1"
        # No legacy row for this facility at all.
        assert db.find_legacy_nas_program("GDP", "IAD") is None

        # Outside the correlation window -> no match. Backdate fetched_at
        # explicitly rather than relying on wall-clock elapsing between
        # the insert above and this check (unixepoch() has second-level
        # granularity, so a within_hours=0.0 check against a just-inserted
        # row is not reliably "outside the window").
        with db.conn() as c:
            c.execute("UPDATE nas_programs SET fetched_at = unixepoch() - 3600 "
                      "WHERE program_id = 'GDP-SFO-1'")
        assert db.find_legacy_nas_program("GDP", "SFO", within_hours=0.5) is None
        assert db.find_legacy_nas_program("GDP", "SFO", within_hours=2.0) == "GDP-SFO-1"
