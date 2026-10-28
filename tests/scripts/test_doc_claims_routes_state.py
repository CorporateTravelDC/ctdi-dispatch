"""2026-10-26 (operator): the doc claims check also catches API routes a doc names that the code
does not serve, and units documented enabled/disabled against their live state; an agent account
runs it from the published status snapshot (paths and routes only)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dcc", ROOT / "scripts" / "doc-claims-check.py")
dcc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dcc)


def _facts(tmp_path, data):
    f = tmp_path / "facts.json"
    f.write_text(json.dumps(data))
    return dcc.LiveFacts(tmp_path, f)


def test_routes_match_parameters_prefixes_proxy_and_shorthand(tmp_path):
    lf = _facts(tmp_path, {"routes": ["/api/v1/fids/{airport}/{flight}", "/api/v1/swim/positions", "/api/adsb/local"]})
    assert lf.route_ok("/api/v1/fids/DCA/X1") and lf.route_ok("/api/v1/swim") and lf.route_ok("/api/v1/swim/positions.")
    assert lf.route_ok("/api/adsb/{local,all}") and lf.route_ok("/api/dispatch/api/v1/swim/positions")
    assert not lf.route_ok("/api/v1/approvals/withdraw") and not lf.route_ok("/api/data/metar")


def test_state_claims_and_route_claims_are_extracted(tmp_path):
    doc = tmp_path / "docs" / "X.md"
    doc.parent.mkdir()
    doc.write_text("`corporatetraveldc-a.timer` is disabled\nGET /api/v1/x/y returns 200\n~~`corporatetraveldc-b.timer` disabled~~\n")
    kinds = {(k, v) for k, _, v, _ in dcc.extract_claims(doc, tmp_path)}
    assert ("state", "disabled") in kinds and ("route", "/api/v1/x/y") in kinds
    assert not any(v == "corporatetraveldc-b.timer" for _, v in kinds)          # struck text is history


def test_check_reports_state_and_route_drift(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "X.md").write_text("`corporatetraveldc-a.timer` is disabled\nGET /api/v1/gone\n")
    lf = _facts(tmp_path, {"tracked": [], "units": ["corporatetraveldc-a.timer"], "routes": ["/api/v1/here"],
                           "states": {"corporatetraveldc-a.timer": "enabled"}})
    drift, _, _ = dcc.check(tmp_path, lf)
    assert any("documented disabled, live enabled" in d for d in drift)
    assert any("route not in src/: /api/v1/gone" in d for d in drift)


def test_snapshot_mode_skips_what_the_snapshot_cannot_know(tmp_path):
    snap = tmp_path / "status.json"
    snap.write_text(json.dumps({"units": [], "timers": []}))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "X.md").write_text("`corporatetraveldc-a.timer` is disabled, OnCalendar=daily\n")
    lf = dcc.LiveFacts(tmp_path, snap)
    assert lf.partial
    drift, _, _ = dcc.check(tmp_path, lf)
    assert drift == []
