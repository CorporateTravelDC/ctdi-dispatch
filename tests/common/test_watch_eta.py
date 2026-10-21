"""common.watch_eta -- the watch ETA is SWIM only, TBFM > TFMS > FDPS, and a
fix time is always labelled with its fix (operator decision 2026-10-16)."""
import ast
from pathlib import Path

import pytest

from common import watch_eta as we

NOW = 1_790_000_000.0          # fixed clock
T = lambda dt_s: NOW + dt_s   # noqa: E731
ISO = lambda e: we._iso(e)    # noqa: E731
FIX = {"CAVLR": (38.9, -77.6), "SWANN": (38.95, -76.4)}


def _seq(fix, kind, eta, fac="ZDC"):
    return {"meter_fix": fix, "eta_kind": kind, "eta": ISO(eta), "facility": fac}


def _tfms(eta, typ="ESTIMATED", age=60):
    return {"tfms_eta": ISO(eta), "tfms_eta_type": typ, "tfms_eta_at": NOW - age}


def _go(**kw):
    kw.setdefault("now", NOW)
    kw.setdefault("lookup", False)
    kw.setdefault("fix_resolver", FIX.get)
    return we.resolve("UAL1742", kw.pop("dest", "KIAD"), **kw)


def test_tbfm_runway_beats_tfms_and_fdps():
    w = _go(tbfm_seqs=[_seq("CAVLR", "rwy", T(3600))], tfms_row=_tfms(T(4000)),
            fdps_row={"arrival_time": T(4200), "updated_at": NOW})
    assert (w.source, w.tier, w.point) == ("tbfm", 1, "runway")
    assert w.epoch == T(3600) and "runway ETA" in w.label and "CAVLR" in w.label


def test_rwy_preferred_over_fresher_mfx():
    w = _go(tbfm_seqs=[_seq("CAVLR", "mfx", T(3000)), _seq("CAVLR", "rwy", T(3600))])
    assert w.point == "runway"


def test_meter_fix_time_is_labelled_with_the_fix_never_bare():
    w = _go(tbfm_seqs=[_seq("ZDC", "mfx", T(1800)), _seq("CAVLR", "mfx", T(2000))])
    assert w.point == "meter_fix" and w.fix == "CAVLR"          # real fix beats pseudo-label
    assert "at meter fix CAVLR" in w.label
    if w.runway_epoch:
        assert w.runway_from_fix and "estimated from meter fix CAVLR" in w.label


@pytest.mark.parametrize("kind,word", [("dfx", "descent fix"), ("sfx", "speed fix")])
def test_descent_and_speed_fix_never_read_as_arrival(kind, word):
    w = _go(tbfm_seqs=[_seq("CAVLR", kind, T(1200))])
    assert w.point == word.replace(" ", "_") and w.runway_epoch is None
    assert word in w.label and "not a runway time" in w.label and "CAVLR" in w.label


def test_no_usable_tbfm_falls_through_to_tfms():
    w = _go(tbfm_seqs=[{"meter_fix": "CAVLR", "eta_kind": None, "eta": ISO(T(900))}],
            tfms_row=_tfms(T(4000)))
    assert (w.source, w.tier) == ("tfms", 2) and "TFMS estimated" in w.label


def test_empty_tbfm_falls_through_to_tfms():
    assert _go(tbfm_seqs=[], tfms_row=_tfms(T(4000))).source == "tfms"


def test_tfms_scheduled_or_stale_falls_through_to_fdps():
    fd = {"arrival_time": T(4200), "updated_at": NOW - 60}
    assert _go(tbfm_seqs=[], tfms_row=_tfms(T(4000), "SCHEDULED"), fdps_row=fd).source == "fdps"
    w = _go(tbfm_seqs=[], tfms_row=_tfms(T(4000), age=we.TFMS_MAX_AGE_S + 1), fdps_row=fd)
    assert (w.source, w.tier) == ("fdps", 3) and "derived from TBFM" in w.label


def test_stale_fdps_gives_none():
    assert _go(tbfm_seqs=[], tfms_row=None,
               fdps_row={"arrival_time": T(100), "updated_at": NOW - we.FDPS_MAX_AGE_S - 1}) is None


def test_tbfm_not_used_for_a_destination_it_does_not_cover():
    w = _go(dest="KORD", tbfm_seqs=[_seq("CAVLR", "rwy", T(3600))], tfms_row=_tfms(T(4000)))
    assert w.source == "tfms"


def test_labels_are_utc_with_et_beside_never_et_alone():
    w = _go(tbfm_seqs=[_seq("CAVLR", "rwy", T(3600))])
    assert "Z (" in w.label and " ET)" in w.label
    d = w.as_dict()
    assert d["eta_utc"].endswith("Z") and d["source"] == "tbfm" and d["fix"] == "CAVLR"


def test_module_imports_no_non_swim_time_source():
    src = Path(we.__file__).read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    banned = ("fids", "aeroapi", "airplanes", "adsb", "acars", "opensky", "jumpseat", "airframes")
    assert not [n for n in names if any(b in n.lower() for b in banned)], names


def test_watchlist_api_entry_carries_labelled_eta(monkeypatch):
    import asyncio
    from common import db
    from web.routes import watchlist as route
    monkeypatch.setattr(db, "get_watchlist_entries", lambda: [
        {"id": "wl-1", "entry_type": "flight", "identifier": "UA1742", "destination": "IAD"},
        {"id": "wl-2", "entry_type": "train", "identifier": "66"}])
    fake = we.WatchEta(T(1800), "meter_fix", "tbfm", 1, "ETA x at meter fix CAVLR (TBFM ZDC)", "CAVLR", "ZDC")
    monkeypatch.setattr(we, "resolve_for_watch", lambda e: fake)
    body = asyncio.run(route.list_watchlist_entries(tier=None, include="eta")).body
    import json
    entries = {e["id"]: e for e in json.loads(body)["entries"]}
    assert entries["wl-1"]["eta"]["fix"] == "CAVLR" and "meter fix CAVLR" in entries["wl-1"]["eta"]["label"]
    assert "eta" not in entries["wl-2"]


def test_watchlist_api_carries_no_eta_unless_asked(monkeypatch):
    """Operator 2026-10-20: an ETA goes out only in answer to a request for it.
    The runner panel's 60 s poll (and the public Ops view it proxies) sends no
    include=, so it never carries one -- and never pays the resolve cost."""
    import asyncio, json
    from common import db
    from web.routes import watchlist as route
    monkeypatch.setattr(db, "get_watchlist_entries", lambda: [
        {"id": "wl-1", "entry_type": "flight", "identifier": "UA1742", "destination": "IAD"}])
    def _boom(e):
        raise AssertionError("resolve_for_watch called without include=eta")
    monkeypatch.setattr(we, "resolve_for_watch", _boom)
    for inc in (None, "", "history", "etas"):
        body = asyncio.run(route.list_watchlist_entries(tier=None, include=inc)).body
        assert "eta" not in json.loads(body)["entries"][0], inc


def _route_entries(monkeypatch, include, listed_ids=(), listed_hex=(), fail=False):
    import asyncio, json
    from common import db
    from web.routes import watchlist as route
    monkeypatch.setattr(db, "get_watchlist_entries", lambda: [
        {"id": "wl-1", "entry_type": "flight", "identifier": "UA1742"},
        {"id": "wl-2", "entry_type": "flight", "identifier": "QTST01", "registration": "QTST01"},
        {"id": "wl-3", "entry_type": "flight", "identifier": "DAL22", "hex_id": "ABC123"}])
    def ids(xs):
        if fail:
            raise RuntimeError("db down")
        return {str(x).upper() for x in xs} & set(listed_ids)
    monkeypatch.setattr(db, "faa_ladd_listed_batch", ids)
    monkeypatch.setattr(db, "faa_ladd_hex_batch", lambda hs: {h.lower() for h in hs} & set(listed_hex))
    fake = we.WatchEta(T(1800), "runway", "tfms", 2, "ETA x", None, None)
    monkeypatch.setattr(we, "resolve_for_watch", lambda e: fake)
    body = asyncio.run(route.list_watchlist_entries(tier=None, include=include)).body
    return {e["id"]: e for e in json.loads(body)["entries"]}


def test_public_eta_drops_ladd_listed_flights(monkeypatch):
    """Public (non-tailnet) callers: ETAs for everyone except LADD-listed
    flights, matched by identifier/registration or by hex (operator 2026-10-20)."""
    got = _route_entries(monkeypatch, "eta_public", listed_ids={"QTST01"}, listed_hex={"abc123"})
    assert got["wl-1"]["eta"] is not None
    assert got["wl-2"]["eta"] is None and got["wl-3"]["eta"] is None


def test_public_eta_fails_closed_on_ladd_lookup_error(monkeypatch):
    got = _route_entries(monkeypatch, "eta_public", fail=True)
    assert all(e["eta"] is None for e in got.values())


def test_internal_eta_is_not_ladd_restricted(monkeypatch):
    got = _route_entries(monkeypatch, "eta", listed_ids={"QTST01"}, listed_hex={"abc123"})
    assert all(e["eta"] is not None for e in got.values())


def test_runner_rewrites_eta_for_public_callers():
    import importlib.util, pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "src/runner/main.py").read_text()
    ns: dict = {}
    import ast, textwrap
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_public_include")
    exec(compile(ast.Module([fn], []), "runner_public_include", "exec"), ns)
    f = ns["_public_include"]
    assert f("eta") == "eta_public" and f("eta, history") == "eta_public,history" and f("") == ""
    assert '"include" in params and not _is_trusted(request)' in src


def test_fdps_lookup_uses_indexed_columns():
    """2026-10-21: "airline || flight_num = ?" scanned flight_events past the
    30 s statement timeout on every call and stalled the poller's loop. The
    lookup must filter airline and flight_num separately (the indexed pair)."""
    import inspect
    src = inspect.getsource(we)
    sql = [ln for ln in src.splitlines() if "FROM flight_events" in ln]
    assert sql and all("||" not in ln for ln in sql)
    assert "airline = ? AND flight_num = ?" in src
    assert we._ICAO_CALLSIGN_RE.match("UAL2386").groups() == ("UAL", "2386")
