"""
tests/web/test_main_parity_audit_20261015.py -- web/main.py defects found by
the 2026-10-15 docs parity audit (no live DB, ntfy or network: db calls are
monkeypatched).

  * /api/v1/feeds hard-coded push_covers to nws + notam, so the amtrak REST
    row read stale while push:amtrak was current (and a fixed 300 s window
    read amtrak's per-poll heartbeat as dead). Coverage now comes from
    ingest.failover, the table /healthz and the poller gate use.
  * Tier-0 /api/v1/aircraft-registry/status returned str(exception).
  * /api/v1/cui/status audited the first 12 raw bearer-token characters.
"""
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


def _feeds(monkeypatch, rows):
    monkeypatch.setattr(db, "get_feed_states", lambda: rows)
    monkeypatch.setattr(db, "get_pull_path_status", lambda: {})
    r = TestClient(web_main.app).get("/api/v1/feeds")
    assert r.status_code == 200
    return {f["feed_name"]: f for f in r.json()["feeds"]}


def test_feeds_amtrak_rest_row_covered_by_current_push(monkeypatch):
    now = time.time()
    # push:amtrak stamps once per ~300 s poll: 400 s old is still current
    # for amtrak (cover_max_age 660), and must cover the stale REST row.
    out = _feeds(monkeypatch, [_feed("amtrak", 5000, "pull error", now),
                               _feed("push:amtrak", 400, None, now)])
    assert out["amtrak"]["push_covered"] is True
    assert out["amtrak"]["error"] is None
    assert out["push:amtrak"]["stale_threshold_seconds"] >= failover.cover_max_age("amtrak")


def test_feeds_push_covers_matches_failover_table(monkeypatch):
    now = time.time()
    rows = []
    for rest, push in failover.push_covers().items():
        rows += [_feed(rest, 99999, "x", now), _feed(push, 10, None, now)]
    out = _feeds(monkeypatch, rows)
    for rest in failover.push_covers():
        assert out[rest]["push_covered"] is True, rest


def test_feeds_stale_push_does_not_cover(monkeypatch):
    now = time.time()
    out = _feeds(monkeypatch, [_feed("amtrak", 5000, "pull error", now),
                               _feed("push:amtrak", 5000, None, now)])
    assert out["amtrak"]["push_covered"] is False
    assert out["amtrak"]["error"] == "pull error"


def test_registry_status_error_is_generic(monkeypatch):
    def boom():
        raise RuntimeError("connection to db-host-internal:5432 failed for user x")
    monkeypatch.setattr(db, "init_db_v11", lambda: None)
    monkeypatch.setattr(db, "faa_registry_count", boom)
    r = TestClient(web_main.app).get("/api/v1/aircraft-registry/status")
    assert r.status_code == 500
    assert "db-host-internal" not in r.text and "5432" not in r.text
    assert r.json() == {"error": "registry status unavailable"}


def test_cui_status_audits_stored_prefix_not_raw_token(monkeypatch):
    secret_part = "Q" * 32                      # synthetic, not a real token
    raw = f"ctdc_op_{secret_part}"
    record = {"tier": "shares", "user_label": "op", "department": None, "token_prefix": "ctdc_op_"}
    monkeypatch.setattr(db, "lookup_token", lambda h: record)
    calls = []
    monkeypatch.setattr(db, "audit", lambda *a, **k: calls.append(k or dict(enumerate(a))))
    r = TestClient(web_main.app).get("/api/v1/cui/status", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    cui = [c for c in calls if c.get("action") == "cui_status_read"]
    assert len(cui) == 1
    assert cui[0]["token_prefix"] == "ctdc_op_"
    assert "Q" not in (cui[0]["token_prefix"] or "")
