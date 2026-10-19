"""2026-10-19 (operator, Gate-1b): the forward arrivals list is SWIM-timed only.

  - the FIDS published time is not used for forward-window selection at all
    (the resolver no longer imports common.airport_fids);
  - FIDS-only arrivals drop out of the forward list;
  - SWIM-timed arrivals stay, with their SWIM times;
  - scripts/hub_arrivals_lookup.py reads the resolver, never the FIDS board.
Synthetic flights only; the DB connection is stubbed."""
from __future__ import annotations

import ast
import contextlib
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from common import flight_resolver as fr

REPO = Path(__file__).resolve().parents[2]


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.params = None

    def execute(self, sql, params):
        self.params = params
        return self

    def fetchall(self):
        lo, hi = self.params[1], self.params[2]
        return [r for r in self._rows if lo <= r["arrival_time"] <= hi]


@pytest.fixture
def swim(monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    rows = [
        {"flight_id": "ZZZ901", "airline": "ZZZ", "flight_num": "901", "origin": "KBOS",
         "destination": "KDCA", "aircraft_type": "A320", "arrival_time": now + 1800, "status": "active"},
        {"flight_id": "YYY5", "airline": "YYY", "flight_num": "5", "origin": "KORD",
         "destination": "KDCA", "aircraft_type": "B738", "arrival_time": now + 3600, "status": "active"},
    ]
    holder = {}

    @contextlib.contextmanager
    def fake_conn():
        c = _Conn(rows)
        holder["c"] = c
        yield c

    monkeypatch.setattr(fr._db, "conn", fake_conn)
    return rows


def test_resolver_does_not_import_or_read_fids():
    tree = ast.parse((REPO / "src/common/flight_resolver.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "common.airport_fids" not in mods
    assert not hasattr(fr, "_query_website")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "_published_epoch" not in names and "publishedTime" not in consts


def test_swim_timed_arrivals_keep_swim_times(swim):
    out = fr.resolve_arrivals("DCA", None, 90)
    assert out["source_used"] == "swim"
    by_id = {r["flight_id"]: r for r in out["results"]}
    assert set(by_id) == {"ZZZ901", "YYY5"}
    want = datetime.fromtimestamp(swim[0]["arrival_time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    assert by_id["ZZZ901"]["scheduled"] == want and by_id["ZZZ901"]["source"] == "swim"
    # window honoured on the SWIM time
    assert {r["flight_id"] for r in fr.resolve_arrivals("DCA", None, 45)["results"]} == {"ZZZ901"}


def test_no_swim_rows_means_none_even_if_fids_has_rows(monkeypatch):
    import common.airport_fids as af
    called = []
    monkeypatch.setattr(af, "get_data", lambda *a, **k: called.append(a) or {"arrivals": [
        {"IATA": "ZZ", "flightnumber": "901", "publishedTime": "2099-01-01 00:00:00"}]})
    monkeypatch.setattr(fr, "_query_swim", lambda *a, **k: [])
    for apt in ("DCA", "IAD", "BWI"):
        out = fr.resolve_arrivals(apt, ["ZZ"], 720)
        assert out["source_used"] == "none" and out["results"] == []
    assert called == []


def _load_hub():
    spec = importlib.util.spec_from_file_location("hub_arrivals_lookup", REPO / "scripts/hub_arrivals_lookup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hub_lookup_reads_the_resolver_not_the_fids_board(swim, monkeypatch):
    import common.airport_fids as af
    monkeypatch.setattr(af, "get_data", lambda *a, **k: pytest.fail("FIDS board read"))
    hub = _load_hub()
    results, errors = hub.lookup(["DCA"], [], 90, "arrivals")
    assert [r["flight"] for r in results] == ["ZZZ901", "YYY5"]
    assert all(r["source"] == "swim" and r["arrival_utc"] for r in results)
    assert errors == []
    results, errors = hub.lookup(["DCA"], [], 90, "departures")
    assert results == [] and "departures" in errors[0]
