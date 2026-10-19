"""CPS SR-2 gate hashes only ITWS content, never last_seen (lean-out L2, 2026-10-19).

cps_recompute.build_inputs() carried each ITWS alert's last_seen into the gate inputs, breaking
the standing rule "dedup never hashes timestamps"; with the L2 heartbeat change (itws last_seen
touched at most every 300 s) the CPS gate would have re-fired on a heartbeat schedule. Rows now
carry content fields only and freshness is resolved at build time (a stale alert drops out)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from common import sr2_gate  # noqa: E402
from poller.skills import cps_recompute as cps  # noqa: E402

TIME_KEYS = {"last_seen", "valid_time", "expires_time", "received_at", "updated_at", "fetched_at"}


def _iso(offset_s=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _alert(**kw):
    row = {"airport": "KDCA", "product_type": sorted(cps.ITWS_SEVERE_PRODUCTS)[0], "severity": 5,
           "detail": "wind shear alert", "last_seen": _iso(-10), "valid_time": _iso(-10),
           "expires_time": None}
    row.update(kw)
    return row


def _hash(monkeypatch, itws_rows):
    monkeypatch.setattr(cps.db, "get_metar_snapshot", lambda: [])
    monkeypatch.setattr(cps.db, "get_active_nas_programs", lambda: [])
    monkeypatch.setattr(cps.db, "get_active_itws_alerts", lambda: [dict(r) for r in itws_rows])
    inputs = cps.build_inputs()
    _, h = sr2_gate.check_gate("cps-test", inputs, force=True)   # force: read-only, no state
    return inputs, h


def test_rows_differing_only_in_last_seen_hash_the_same(monkeypatch):
    _, h1 = _hash(monkeypatch, [_alert(last_seen=_iso(-10), valid_time=_iso(-10))])
    _, h2 = _hash(monkeypatch, [_alert(last_seen=_iso(-250), valid_time=_iso(-250))])
    assert h1 == h2


def test_a_content_change_hashes_differently(monkeypatch):
    _, base = _hash(monkeypatch, [_alert()])
    _, sev = _hash(monkeypatch, [_alert(severity=4)])
    _, det = _hash(monkeypatch, [_alert(detail="microburst alert")])
    assert len({base, sev, det}) == 3


def test_no_time_field_reaches_the_gate_inputs(monkeypatch):
    inputs, _ = _hash(monkeypatch, [_alert()])
    assert inputs["itws"] and all(not (TIME_KEYS & set(r)) for r in inputs["itws"])


def test_stale_alert_drops_out_and_changes_the_hash(monkeypatch):
    fresh, h_fresh = _hash(monkeypatch, [_alert()])
    stale, h_stale = _hash(monkeypatch, [_alert(last_seen=_iso(-(cps.ITWS_STALE_SECONDS + 60)))])
    assert fresh["itws"] and stale["itws"] == [] and h_fresh != h_stale


def test_scoring_still_uses_the_built_rows(monkeypatch):
    inputs, _ = _hash(monkeypatch, [_alert()])
    inputs["metar"] = [{"station": "KDCA", "ceiling_ft": 5000, "visibility_sm": 10.0,
                        "wind_kt": 5, "precip": None}]
    data = cps._compute_cps(inputs)
    assert data["factors"]["wind"] == "violated"
