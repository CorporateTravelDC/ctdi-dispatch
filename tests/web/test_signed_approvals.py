"""
tests/web/test_signed_approvals.py -- Wave 2 (2026-10-04): human-signed
approvals, council/arena convenes and the shared ghostwriting workspace
(common.governance + the web routes).

Operator: "An agent can request it, but it has to be signed off by a human,
preferably with a clear signed message, so that any future approval gate to
the phone can't be directly bypassed." / "all agents can read ... restrict
write ... per-agent, per-task, or flat-out ... could not erase each other's
findings."
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from common import board_sign as bs  # noqa: E402
from common import db  # noqa: E402
from common import governance as gov  # noqa: E402


def _key(tmp_path, name, comment):
    k = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(k)], check=True)
    return str(k), (tmp_path / f"{name}.pub").read_text().strip()


@pytest.fixture
def team(tmp_path):
    """operator (human admin: board key + separate approval key), two agents."""
    db.init_db()
    gov._ensure_approvals()
    with db.conn() as c:
        for t in ("approval_signers", "council_sessions", "board_signers"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM workspace_grants WHERE id != 'wg-default-all'")
    keys = {}
    for acct, kind in (("op", "human"), ("agent-a", "agent"), ("agent-b", "agent")):
        keys[acct] = _key(tmp_path, acct, f"{acct}@corporatetraveldc-dispatch")
        db.board_signer_upsert(acct, keys[acct][1], f"{acct}@x", role="admin" if kind == "human" else "member", kind=kind)
    keys["op-approver"] = _key(tmp_path, "op_approver", "op-approver@x")
    gov.approval_signer_upsert("op", keys["op-approver"][1])
    web_main._BOARD_REPLAY._seen.clear()
    return keys


def _approve(keys, rid, action="allow", signer="op", keyname="op-approver"):
    row = db.get_approval_request(rid)
    with db.conn() as c:
        full = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (rid,)).fetchone())
    assert row is not None
    sig = bs.sign_with_ssh_keygen(keys[keyname][0], gov.approval_canonical(full, action), gov.NAMESPACE_APPROVAL)
    return gov.resolve_signed(rid, action, signer, sig)


# -- approval keys --------------------------------------------------------------

def test_approval_key_must_not_be_a_board_key(team):
    with pytest.raises(gov.GovernanceError) as e:
        gov.approval_signer_upsert("op", team["op"][1])
    assert "board signing key" in e.value.detail


def test_approval_key_only_for_human_accounts(team, tmp_path):
    _, pub = _key(tmp_path, "x", "x@x")
    with pytest.raises(gov.GovernanceError):
        gov.approval_signer_upsert("agent-a", pub)


# -- sudo-kind approvals ---------------------------------------------------------

def test_signed_allow_resolves_and_records_signer(team):
    r = gov.create_approval("p", "sudo systemctl restart ollama.service", requester="admin-token")
    out = _approve(team, r["id"])
    assert out["status"] == "allowed" and out["resolved_by"] == "op"
    with pytest.raises(gov.GovernanceError) as e:      # resolves once
        _approve(team, r["id"])
    assert e.value.status == 409


def test_board_key_signature_never_approves(team):
    """The operator's passphrase-less board key is usable by every process
    running as the operator -- it must not approve."""
    r = gov.create_approval("p", "echo hi", requester="admin-token")
    with pytest.raises(gov.GovernanceError) as e:
        _approve(team, r["id"], keyname="op")
    assert e.value.status == 403
    assert db.get_approval_request(r["id"])["status"] == "pending"


def test_signature_is_bound_to_the_exact_request(team):
    a = gov.create_approval("p", "echo A", requester="admin-token")
    b = gov.create_approval("p", "echo B", requester="admin-token")
    with db.conn() as c:
        ra = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (a["id"],)).fetchone())
    sig = bs.sign_with_ssh_keygen(team["op-approver"][0], gov.approval_canonical(ra, "allow"), gov.NAMESPACE_APPROVAL)
    with pytest.raises(gov.GovernanceError) as e:
        gov.resolve_signed(b["id"], "allow", "op", sig)
    assert e.value.status == 403
    with pytest.raises(gov.GovernanceError):                     # allow-sig cannot deny either
        gov.resolve_signed(a["id"], "deny", "op", sig)
    with db.conn() as c:                                         # tampered command
        c.execute("UPDATE approval_requests SET command = 'rm -rf /' WHERE id = ?", (a["id"],))
    with pytest.raises(gov.GovernanceError):
        gov.resolve_signed(a["id"], "allow", "op", sig)


def test_requester_cannot_approve_itself(team):
    r = gov.create_approval("p", "echo", requester="op")
    with pytest.raises(gov.GovernanceError) as e:
        _approve(team, r["id"])
    assert "requester" in e.value.detail


def test_inert_human_cannot_approve(team):
    r = gov.create_approval("p", "echo", requester="admin-token")
    db.board_signer_set_active("op", False, "inert")
    with pytest.raises(gov.GovernanceError) as e:
        _approve(team, r["id"])
    assert "liveness" in e.value.detail


def test_tap_route_can_deny_but_never_allow(team):
    r = gov.create_approval("p", "echo", requester="admin-token")
    with pytest.raises(HTTPException) as e:
        asyncio.run(web_main.resolve_approval_request_route(r["id"], action="allow", k=r["allow_key"]))
    assert e.value.status_code == 403 and "human-signed" in e.value.detail
    resp = asyncio.run(web_main.resolve_approval_request_route(r["id"], action="deny", k=r["deny_key"]))
    assert json.loads(resp.body)["status"] == "denied"


# -- council / arena ---------------------------------------------------------------

def _convene(mode="arena"):
    return gov.council_create("agent-a", mode, "Series/Uber Series/draft-7.md", "fact-check the numbers",
                              [{"account": "agent-a", "required": True}, {"account": "agent-b"}], 24)


def test_agent_request_does_nothing_until_a_human_signs(team):
    cs = _convene()
    assert gov.council_get(cs["id"])["status"] == "requested"
    with pytest.raises(gov.GovernanceError):
        gov.check_contribution("agent-a", f"council-{cs['id']}", "x")
    _approve(team, cs["approval_id"])
    got = gov.council_get(cs["id"])
    assert got["status"] == "active"
    gov.check_contribution("agent-a", f"council-{cs['id']}", "finding")
    msgs, _ = db.board_query(thread="council", limit=50)
    assert {m["to"] for m in msgs} >= {"agent-a", "agent-b"}


def test_council_spec_is_what_gets_signed(team):
    cs = _convene()
    with db.conn() as c:
        cmd = c.execute("SELECT command FROM approval_requests WHERE id = ?", (cs["approval_id"],)).fetchone()["command"]
    spec = json.loads(cmd)
    assert spec["participants"] == [{"account": "agent-a", "required": True}, {"account": "agent-b", "required": False}]
    assert spec["deadline"] == cs["deadline"] and spec["requester"] == "agent-a"


def test_non_participant_cannot_contribute_to_a_convene(team, tmp_path):
    _, pub = _key(tmp_path, "c", "agent-c@x")
    db.board_signer_upsert("agent-c", pub, "agent-c@x")
    cs = _convene()
    _approve(team, cs["approval_id"])
    with pytest.raises(gov.GovernanceError) as e:
        gov.check_contribution("agent-c", f"council-{cs['id']}", "x")
    assert "not a participant" in e.value.detail


def test_arena_is_blind_until_a_human_closes_it(team):
    cs = _convene("arena")
    _approve(team, cs["approval_id"])
    base = f"{gov.CONTRIB_ROOT}/council-{cs['id']}"
    assert gov.arena_read_allowed(f"{base}/agent-a/x.md", "agent-a")[0]
    assert not gov.arena_read_allowed(f"{base}/agent-b/x.md", "agent-a")[0]
    assert not gov.arena_read_allowed(f"{base}/agent-b/x.md", None)[0]          # key-only reader
    close = gov.council_request_close(cs["id"], "agent-b")
    _approve(team, close["close_approval_id"])
    assert gov.council_get(cs["id"])["status"] == "closed"
    assert gov.arena_read_allowed(f"{base}/agent-b/x.md", "agent-a")[0]
    assert not any(g["task"] == f"council-{cs['id']}" for g in gov.grant_list())


def test_council_mode_is_not_blind(team):
    cs = _convene("council")
    _approve(team, cs["approval_id"])
    assert gov.arena_read_allowed(f"{gov.CONTRIB_ROOT}/council-{cs['id']}/agent-b/x.md", "agent-a")[0]


def test_denied_convene_is_marked_denied(team):
    cs = _convene()
    _approve(team, cs["approval_id"], action="deny")
    assert gov.council_get(cs["id"])["status"] == "denied"


def test_missed_deadline_reports_required_only_once(team):
    cs = _convene()
    _approve(team, cs["approval_id"])
    later = cs["deadline"] + 10
    out = gov.council_missed(lambda p: [], now=later)
    assert out and out[0]["missing"] == ["agent-a"]          # agent-b was only invited
    assert gov.council_missed(lambda p: [], now=later + 60) == []


# -- workspace grants ---------------------------------------------------------------

def test_default_grant_deny_wins_lock_and_expiry(team):
    assert gov.write_allowed("agent-a", "general")[0]
    gid = gov.grant_add("deny", "agent-a", "factcheck-7", note="clawback")
    assert not gov.write_allowed("agent-a", "factcheck-7")[0]
    assert gov.write_allowed("agent-a", "general")[0]
    assert gov.write_allowed("agent-b", "factcheck-7")[0]
    gov.grant_remove(gid)
    gov.grant_add("deny", "agent-b", "*", until=time.time() - 1)                   # expired deny
    assert gov.write_allowed("agent-b", "general")[0]
    gov.grant_add("deny", "*", "*", gid="wg-lock", note="lock")
    assert not gov.write_allowed("agent-a", "general")[0]
    gov.grant_remove("wg-lock")


def test_no_grant_means_no_write(team):
    gov.grant_remove("wg-default-all")
    try:
        assert not gov.write_allowed("agent-a", "general")[0]
        gov.grant_add("grant", "agent-a", "factcheck-7")
        assert gov.write_allowed("agent-a", "factcheck-7")[0] and not gov.write_allowed("agent-a", "general")[0]
    finally:
        gov.grant_add("grant", "*", "*", gid="wg-default-all", note="default")


# -- the contribute route --------------------------------------------------------------

def _req(method, path, body: bytes, **headers):
    async def _body():
        return body
    p, _, q = path.partition("?")
    return SimpleNamespace(method=method, url=SimpleNamespace(path=p, query=q), headers=headers, body=_body,
                           client=SimpleNamespace(host="127.0.0.1"))


def _signed(priv, method, path, body, signer):
    ts = int(time.time())
    msg = bs.canonical_message(method, path, ts, body)
    return {"X-Board-Signer": signer, "X-Board-Timestamp": str(ts), "X-Board-Signature": bs.sign_with_ssh_keygen(priv, msg)}


def test_contribute_is_signed_attributed_and_create_only(team):
    body = json.dumps({"task": "general", "title": "Fare check", "content": "the 2019 numbers hold"}).encode()
    req = _req("POST", "/api/v1/workspace/contribute", body, **_signed(team["agent-a"][0], "POST", "/api/v1/workspace/contribute", body, "agent-a"))
    model = web_main.ContributionIn(**json.loads(body))
    with patch("second_brain.webdav_client.put_create_only") as put, \
            patch.object(web_main.db, "board_token_valid", return_value=False):
        resp = asyncio.run(web_main.workspace_contribute_route(model, req))
    out = json.loads(resp.body)
    assert out["account"] == "agent-a" and out["path"].startswith(f"{gov.CONTRIB_ROOT}/general/agent-a/")
    written = put.call_args[0][1]
    assert "account: agent-a" in written and "agents never publish" in written


def test_contribute_refuses_the_shared_key_alone(team):
    body = json.dumps({"task": "general", "content": "x"}).encode()
    req = _req("POST", "/api/v1/workspace/contribute", body, **{"X-Board-Key": "anything"})
    with patch.object(web_main.db, "board_token_valid", return_value=True), pytest.raises(HTTPException) as e:
        asyncio.run(web_main.workspace_contribute_route(web_main.ContributionIn(**json.loads(body)), req))
    assert e.value.status_code == 401


def test_contribute_respects_a_deny(team):
    gov.grant_add("deny", "agent-a", "*", gid="wg-test-deny")
    try:
        body = json.dumps({"task": "general", "content": "x"}).encode()
        req = _req("POST", "/api/v1/workspace/contribute", body, **_signed(team["agent-a"][0], "POST", "/api/v1/workspace/contribute", body, "agent-a"))
        with patch("second_brain.webdav_client.put_create_only") as put, pytest.raises(HTTPException) as e:
            asyncio.run(web_main.workspace_contribute_route(web_main.ContributionIn(**json.loads(body)), req))
        assert e.value.status_code == 403 and not put.called
    finally:
        gov.grant_remove("wg-test-deny")


def test_signed_resolve_route(team):
    r = gov.create_approval("p", "echo", requester="admin-token")
    with db.conn() as c:
        full = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (r["id"],)).fetchone())
    sig = bs.sign_with_ssh_keygen(team["op-approver"][0], gov.approval_canonical(full, "allow"), gov.NAMESPACE_APPROVAL)
    web_main._signed_resolve_hits.clear()
    resp = asyncio.run(web_main.approval_signed_resolve_route(r["id"], web_main.SignedResolveIn(action="allow", signer="op", signature=sig)))
    assert json.loads(resp.body)["status"] == "allowed"
