"""
tests/web/test_approval_gate_keys.py

2026-10-04 adversarial duel, finding X1: the approval gate's resolve route was
Tier 0 and "secured purely by the unguessable id" -- an id the gate prints to
stdout and any admin-token holder can read back. Resolution now needs a
per-request, per-action key minted at creation, returned once to the creator
(which puts it only into the operator's ntfy push), stored only as a SHA-256
hash, and never returned by any read path.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from common import db  # noqa: E402


@pytest.fixture
def req():
    db.init_db_v21()
    rid = str(uuid.uuid4())
    created = db.create_approval_request(rid, "test-pattern", "echo hi", reasoning="t", ttl_seconds=600)
    return rid, created


def test_create_returns_keys_once_and_stores_only_hashes(req):
    rid, created = req
    assert created["allow_key"] and created["deny_key"] and created["allow_key"] != created["deny_key"]
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (rid,)).fetchone())
    assert row["allow_key_hash"] and row["allow_key_hash"] != created["allow_key"]
    assert created["allow_key"] not in str(row) and created["deny_key"] not in str(row)


def test_get_never_returns_keys_or_hashes(req):
    rid, created = req
    got = db.get_approval_request(rid)
    assert got["status"] == "pending"
    for k in ("allow_key", "deny_key", "allow_key_hash", "deny_key_hash"):
        assert k not in got
    assert created["allow_key"] not in str(got)


def test_id_alone_does_not_resolve(req):
    rid, _ = req
    with pytest.raises(db.ApprovalNeedsSignature):
        db.resolve_approval_request(rid, "allow", "")
    with pytest.raises(db.ApprovalKeyError):
        db.resolve_approval_request(rid, "deny", "")
    assert db.get_approval_request(rid)["status"] == "pending"


def test_wrong_key_does_not_resolve(req):
    rid, _ = req
    with pytest.raises(db.ApprovalKeyError):
        db.resolve_approval_request(rid, "deny", "not-the-key")
    assert db.get_approval_request(rid)["status"] == "pending"


def test_deny_link_cannot_be_turned_into_allow(req):
    rid, created = req
    with pytest.raises(db.ApprovalNeedsSignature):
        db.resolve_approval_request(rid, "allow", created["deny_key"])
    assert db.get_approval_request(rid)["status"] == "pending"


def test_no_key_ever_allows_wave2(req):
    """2026-10-04 Wave 2: allow needs a human signature (governance.resolve_signed);
    even the genuine allow key from the push is refused, state unchanged."""
    rid, created = req
    with pytest.raises(db.ApprovalNeedsSignature):
        db.resolve_approval_request(rid, "allow", created["allow_key"])
    assert db.get_approval_request(rid)["status"] == "pending"


def test_correct_deny_key_resolves_once(req):
    rid, created = req
    out = db.resolve_approval_request(rid, "deny", created["deny_key"])
    assert out["status"] == "denied"
    for k in ("allow_key_hash", "deny_key_hash"):
        assert k not in out
    again = db.resolve_approval_request(rid, "deny", created["deny_key"])
    assert again["status"] == "denied"


def test_unkeyed_legacy_row_cannot_be_resolved():
    db.init_db_v21()
    rid = str(uuid.uuid4())
    import time
    with db.conn() as c:
        c.execute(
            "INSERT INTO approval_requests (id, command_pattern, command, reasoning, status, created_at, expires_at)"
            " VALUES (?, 'p', 'c', '', 'pending', ?, ?)", (rid, time.time(), time.time() + 600))
    with pytest.raises(db.ApprovalKeyError):
        db.resolve_approval_request(rid, "deny", "anything")
    assert db.get_approval_request(rid)["status"] == "pending"


def test_route_rejects_missing_and_wrong_key_with_403(req):
    rid, created = req
    for k in ("", "nope", created["deny_key"]):
        with pytest.raises(HTTPException) as e:
            asyncio.run(web_main.resolve_approval_request_route(rid, action="allow", k=k))
        assert e.value.status_code == 403
    assert db.get_approval_request(rid)["status"] == "pending"


def test_route_resolves_with_the_push_key_and_hides_hashes(req):
    rid, created = req
    resp = asyncio.run(web_main.resolve_approval_request_route(rid, action="deny", k=created["deny_key"]))
    body = resp.body.decode()
    assert '"denied"' in body and "key_hash" not in body


def test_route_unknown_id_is_404():
    db.init_db_v21()
    with pytest.raises(HTTPException) as e:
        asyncio.run(web_main.resolve_approval_request_route(str(uuid.uuid4()), action="allow", k="x"))
    assert e.value.status_code == 404


def test_list_route_returns_counts_only(req):
    rid, created = req
    with db.conn() as c:   # allowed via the signed path (tests/web/test_signed_approvals.py)
        c.execute("UPDATE approval_requests SET status = 'allowed' WHERE id = ?", (rid,))
    resp = asyncio.run(web_main.list_approval_requests_route(command_pattern="test-pattern", since_days=7.0, tier=None))
    body = resp.body.decode()
    assert '"allowed_count": 1' in body.replace('"allowed_count":1', '"allowed_count": 1')
    assert rid not in body and "key" not in body.lower().replace("promotion_candidate", "")
