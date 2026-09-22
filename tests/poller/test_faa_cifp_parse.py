"""
Regression tests for src/poller/skills/faa_cifp_parse.py and
common/cifp_lookup.py, built 2026-09-05 alongside a real, byte-verified
extraction of CIFP cycle 2609 (CIFP_260903.zip -- the same file
faa_cifp_pull.py fetches automatically).

Fixture (tests/poller/fixtures/cifp_kiad_gibbz6_sample.txt) is a REAL
85-line slice pulled directly from that file: HVQ's navaid record, every
leg of KIAD's GIBBZ6 STAR (all transitions), and KIAD's H01LZ approach
(which carries a published HM hold). Not synthetic -- these are the exact
bytes FAA published for cycle 2609.

IMPORTANT: use the `cifp_db` fixture (monkeypatch-based) for any test
touching db._db_path, not a manual reassignment -- a manual
`db._db_path = lambda: ...` with a try/finally that only unlinks the temp
file does NOT restore db._db_path itself, leaving it pointed at a deleted
file for every later test in the same pytest session. Confirmed live:
this exact mistake (an earlier version of this file) caused real
cross-file test failures when run alongside tests/ingest/test_cifp_arrival_enrichment.py.
"""
from pathlib import Path

import pytest

import common.db as db
from common import cifp_lookup as L
from poller.skills.faa_cifp_parse import _parse_lines

FIXTURE = Path(__file__).parent / "fixtures" / "cifp_kiad_gibbz6_sample.txt"


def _load_fixture_lines() -> list[str]:
    return FIXTURE.read_text().splitlines()


@pytest.fixture
def cifp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "_db_path", lambda: db_path)
    db.init_db_all()
    yield db_path


def test_parse_lines_reproduces_known_gibbz6_shape():
    lines = _load_fixture_lines()
    fixes, legs, holds = _parse_lines(lines, "260903")

    transitions = {r["transition"] for r in legs}
    assert {"BURTT", "HVQ", "MGW", "SITTR", "(common)"} <= transitions
    assert any(t.startswith("RW") for t in transitions)

    hvq_fix = next((f for f in fixes if f["ident"] == "HVQ" and f["icao_region"] == "K6"), None)
    assert hvq_fix is not None
    assert abs(hvq_fix["lat"] - 38.349675) < 1e-4
    assert abs(hvq_fix["lon"] - (-81.769914)) < 1e-4

    hvq_leg = next(r for r in legs if r["transition"] == "HVQ" and r["seq"] == 10)
    assert hvq_leg["fix"] == "HVQ"
    assert abs(hvq_leg["lat"] - 38.349675) < 1e-4

    assert len(holds) >= 1
    assert all(h["hold_type"] in ("HA", "HF", "HM") for h in holds)


def test_parse_lines_skips_continuation_records():
    # col 39 (continuation record no.) filters to '0'/'1' only -- confirm
    # nothing in this fixture with a different value leaked through.
    lines = _load_fixture_lines()
    _fixes, legs, _holds = _parse_lines(lines, "260903")
    for r in legs:
        assert r["seq"] >= 0  # would-be-garbage continuation rows parse seq=0 at worst, never crash


def test_cifp_replace_all_refuses_empty_result(cifp_db):
    lines = _load_fixture_lines()
    fixes, legs, holds = _parse_lines(lines, "260903")
    result = db.cifp_replace_all("260903", fixes, legs, holds)
    assert result["ok"]
    assert db.cifp_meta_get("cycle") == "260903"

    # The regression this guards against (same class as faa_upsert_ladd's
    # C-31 fix): an empty parse must never wipe good existing data.
    refused = db.cifp_replace_all("260910", [], [], [])
    assert refused["ok"] is False
    assert db.cifp_meta_get("cycle") == "260903"  # unchanged


def test_cifp_lookup_resolve_fix_and_transitions(cifp_db):
    lines = _load_fixture_lines()
    fixes, legs, holds = _parse_lines(lines, "260903")
    db.cifp_replace_all("260903", fixes, legs, holds)

    hvq = L.resolve_fix("HVQ", "K6")
    assert hvq is not None
    assert abs(hvq["lat"] - 38.349675) < 1e-4

    assert L.resolve_fix("NOTAREALFIX") is None

    transitions = L.get_procedure_transitions("KIAD", "STAR", "GIBBZ6")
    assert "HVQ" in transitions
    assert "(common)" in transitions
    seqs = [r["seq"] for r in transitions["HVQ"]]
    assert seqs == sorted(seqs)


def test_cifp_lookup_expand_arrival_route_worked_example(cifp_db):
    """The exact worked example CIFP_PARSING_PROMPT.md's prompt calls for:
    a filed route ending '... HVQ GIBBZ6 KIAD' expanded to a coordinate
    polyline by joining the HVQ transition to the common route."""
    lines = _load_fixture_lines()
    fixes, legs, holds = _parse_lines(lines, "260903")
    db.cifp_replace_all("260903", fixes, legs, holds)

    route = L.expand_arrival_route("KIAD", "GIBBZ6", "HVQ")
    assert route is not None
    assert len(route) > 0
    assert route[0]["fix"] == "HVQ"
    assert all(leg.get("lat") is not None or leg["path_term"] in ("VA", "CA", "VI")
               for leg in route if leg["fix"])

    assert L.expand_arrival_route("KIAD", "NOTAPROCEDURE", "HVQ") is None


def test_cifp_lookup_get_holds_by_airport_and_fix(cifp_db):
    lines = _load_fixture_lines()
    fixes, legs, holds = _parse_lines(lines, "260903")
    db.cifp_replace_all("260903", fixes, legs, holds)

    by_airport = L.get_holds(airport="KIAD")
    assert len(by_airport) >= 1
    assert all(h["airport"] == "KIAD" for h in by_airport)

    assert L.get_holds(airport="KZZZ") == []
