"""2026-10-18 (operator, gate batch; Phase A hard gate H1): optional enrichment feeds
(ingest.failover.OPTIONAL_FEEDS -- MWAA FIDS) never change /healthz status, are
listed separately, carry `optional: true` in /api/v1/feeds, and ingest_feed_watch
reads that flag. No live DB, ntfy or network: db calls are monkeypatched."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from common import db  # noqa: E402
from ingest import failover  # noqa: E402


def _feed(name, age, error=None, now=None):
    now = now or time.time()
    return {"feed_name": name, "fetched_at": now - age, "error": error, "consecutive_failures": 0}


def _healthz(monkeypatch, rows):
    monkeypatch.setattr(db, "get_feed_states", lambda: rows)
    monkeypatch.setattr(db, "get_latest_cps", lambda: None)
    monkeypatch.setattr(db, "audit_count_24h", lambda: 0)
    monkeypatch.setattr(db, "active_token_count", lambda: 0)
    r = TestClient(web_main.app).get("/healthz")
    assert r.status_code == 200
    return r.json()


def test_fids_is_the_declared_optional_set():
    assert failover.OPTIONAL_FEEDS == {"dca_fids", "iad_fids"}
    assert failover.is_optional("dca_fids") and not failover.is_optional("metar")


def test_healthz_ok_with_stale_and_failing_fids(monkeypatch):
    now = time.time()
    out = _healthz(monkeypatch, [_feed("metar", 60, now=now),
                                 _feed("dca_fids", 9000, "FIDS fetch failed for DCA", now),
                                 _feed("iad_fids", 9000, None, now)])
    assert out["status"] == "ok" and out["reason"] is None
    assert sorted(out["optional_stale"]) == ["dca_fids", "iad_fids"]


def test_healthz_still_degraded_by_a_stale_core_feed(monkeypatch):
    now = time.time()
    out = _healthz(monkeypatch, [_feed("metar", 5000, now=now), _feed("dca_fids", 60, now=now)])
    assert out["status"] == "degraded" and "metar" in out["reason"] and "fids" not in out["reason"]
    assert out["optional_stale"] == []


def test_feeds_rows_carry_the_optional_flag(monkeypatch):
    monkeypatch.setattr(db, "get_feed_states", lambda: [_feed("metar", 60), _feed("iad_fids", 60)])
    monkeypatch.setattr(db, "get_pull_path_status", lambda: {})
    rows = {f["feed_name"]: f for f in TestClient(web_main.app).get("/api/v1/feeds").json()["feeds"]}
    assert rows["iad_fids"]["optional"] is True and rows["metar"]["optional"] is False


def test_ingest_feed_watch_reads_the_flag(monkeypatch):
    from poller.skills import ingest_feed_watch as ifw
    monkeypatch.setattr(ifw, "_check_ntfy", lambda: True)
    monkeypatch.setattr(ifw, "_check_feeds", lambda: [
        {"feed_name": "zz_enrich", "optional": True, "error": None, "push_covered": False},
        {"feed_name": "metar", "optional": False, "error": None, "push_covered": False}])
    # an older web image still listing an optional feed in the stale reason: not a problem
    monkeypatch.setattr(ifw, "_check_healthz", lambda: {"status": "degraded", "reason": "Stale feeds: zz_enrich"})
    summary, problems = ifw.run_check()
    assert problems == []
    monkeypatch.setattr(ifw, "_check_healthz", lambda: {"status": "degraded", "reason": "Stale feeds: zz_enrich, metar"})
    _s, problems = ifw.run_check()
    assert problems == ["platform health degraded: stale feed(s) metar"]


def test_optional_feed_error_is_still_reported_and_labelled(monkeypatch):
    from poller.skills import ingest_feed_watch as ifw
    monkeypatch.setattr(ifw, "_check_ntfy", lambda: True)
    monkeypatch.setattr(ifw, "_check_healthz", lambda: {"status": "ok"})
    monkeypatch.setattr(ifw, "_check_feeds", lambda: [
        {"feed_name": "dca_fids", "optional": True, "error": "FIDS fetch failed for DCA", "push_covered": False}])
    _s, problems = ifw.run_check()
    assert problems == ["feed 'dca_fids' (optional enrichment feed): FIDS fetch failed for DCA"]
