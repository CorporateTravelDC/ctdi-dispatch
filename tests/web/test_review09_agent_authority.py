"""
tests/web/test_review09_agent_authority.py -- security review 09 (2026-10-08),
Test C: agent authority and revocation, against the real gateway, governance
and signature code (real ssh-keygen signatures, the real token tables; the
SQLite test backend stands in for Postgres -- see the limitation notes).

Each test names the review ID it evidences (C1..C10, D8, D9).
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from common import agent_gateway as gw
from common import board_sign as bs
from common import db
from common import governance as gov
from tests.web.test_agent_gateway import _key, _link, _mcp, _pkce, _sign, env  # noqa: F401 -- fixtures

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
COWORK = "ctdc-agent-anthropic-cowork"


def _row(approval_id):
    with db.conn() as c:
        return dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone())


def _sig(env, row, action="allow"):
    return bs.sign_with_ssh_keygen(env["keys"]["appr"][0], gov.approval_canonical(row, action), gov.NAMESPACE_APPROVAL)


def _audit(action):
    with db.conn() as c:
        rows = c.execute("SELECT detail FROM audit_log WHERE action = ?", (action,)).fetchall()
    return [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in rows]


# -- C1 identity isolation --------------------------------------------------------------------

def test_c1_tool_arguments_cannot_change_the_acting_identity(env):
    db.board_signer_upsert("ctdc-agent-openai-codex", _key(env["seen"].parent, "cx")[1], "cx@x", role="member", kind="agent")
    _, tok = _link(env)
    forged = {"to": "dispatch", "thread": "coord", "subject": "c1", "body": "b",
              "from": "op", "sender": "op", "account": "op", "requester": "op"}
    out = _mcp(env, tok["access_token"], "tools/call", {"name": "board_post", "arguments": forged}).json()["result"]
    assert not out.get("isError")
    msgs, _ = db.board_query(thread="coord", limit=50)
    mine = [m for m in msgs if m["subject"] == "c1"]
    assert mine and all(m["from"] == COWORK for m in mine)                 # sender comes from the token only
    r = _mcp(env, tok["access_token"], "tools/call", {"name": "council_request", "arguments": {
        "mode": "council", "subject": "x", "participants": [{"account": "ctdc-agent-openai-codex"}],
        "requester": "op"}}).json()["result"]
    appr = json.loads(r["content"][0]["text"])["approval_id"]
    assert _row(appr)["requester"] == COWORK                               # not the forged "op"
    st = json.loads(_mcp(env, tok["access_token"], "tools/call", {"name": "status", "arguments": {"account": "op"}})
                    .json()["result"]["content"][0]["text"])
    assert st["account"] == COWORK


# -- C2 authorization binding -----------------------------------------------------------------

def test_c2_a_signature_authorizes_exactly_one_request_action_and_content(env):
    a = gov.create_approval("pat", "cmd-a", kind="sudo", requester="r1", reasoning="ra", ttl_seconds=300)
    b = gov.create_approval("pat", "cmd-b", kind="sudo", requester="r1", reasoning="ra", ttl_seconds=300)
    sig_a = _sig(env, _row(a["id"]))
    with pytest.raises(gov.GovernanceError) as e:                          # A's signature on B
        gov.resolve_signed(b["id"], "allow", "op", sig_a)
    assert e.value.status == 403
    deny_a = _sig(env, _row(a["id"]), "deny")
    with pytest.raises(gov.GovernanceError):                               # a deny signature used as allow
        gov.resolve_signed(a["id"], "allow", "op", deny_a)
    for col, val in (("command", "cmd-evil"), ("requester", "r2"), ("reasoning", "other"),
                     ("kind", "council"), ("command_pattern", "pat2")):
        with db.conn() as c:                                               # a database writer edits the row after signing
            orig = c.execute(f"SELECT {col} FROM approval_requests WHERE id = ?", (a["id"],)).fetchone()[0]
            c.execute(f"UPDATE approval_requests SET {col} = ? WHERE id = ?", (val, a["id"]))
        with pytest.raises(gov.GovernanceError):
            gov.resolve_signed(a["id"], "allow", "op", sig_a)
        with db.conn() as c:
            c.execute(f"UPDATE approval_requests SET {col} = ? WHERE id = ?", (orig, a["id"]))
    assert gov.resolve_signed(a["id"], "allow", "op", sig_a)["status"] == "allowed"
    assert _row(b["id"])["status"] == "pending"


# -- C3 replay, C4 expiry, C5 concurrency ----------------------------------------------------------

def test_c3_a_resolved_approval_cannot_be_resolved_again(env):
    a = gov.create_approval("pat", "cmd", kind="sudo", requester="r1", ttl_seconds=300)
    sig = _sig(env, _row(a["id"]))
    gov.resolve_signed(a["id"], "allow", "op", sig)
    with pytest.raises(gov.GovernanceError) as e:
        gov.resolve_signed(a["id"], "allow", "op", sig)
    assert e.value.status == 409


def test_c4_an_expired_approval_cannot_be_resolved(env):
    a = gov.create_approval("pat", "cmd", kind="sudo", requester="r1", ttl_seconds=60)
    sig = _sig(env, _row(a["id"]))
    with pytest.raises(gov.GovernanceError) as e:
        gov.resolve_signed(a["id"], "allow", "op", sig, now=time.time() + 120)
    assert e.value.status == 409
    assert _row(a["id"])["status"] in ("pending", "expired")


def test_c5_concurrent_resolution_lets_exactly_one_through(env):
    a = gov.create_approval("pat", "cmd", kind="sudo", requester="r1", ttl_seconds=300)
    sig = _sig(env, _row(a["id"]))
    results, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        try:
            gov.resolve_signed(a["id"], "allow", "op", sig)
            results.append("ok")
        except gov.GovernanceError as e:
            results.append(e.status)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count("ok") == 1, results
    assert len(_audit("approval.resolved")) == 1


def test_c5_an_oauth_code_is_exchanged_once_under_concurrency(env):
    c = env["client"]
    cid = c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).json()["client_id"]
    verifier, ch = _pkce()
    r = gw.authorize_start("cowork", cid, REDIRECT, ch, "S256", None, None)
    _sign(env, r["approval_id"])
    code = gw.authorize_status(r["req_id"])["location"].split("code=")[1].split("&")[0]
    assert gw.authorize_status(r["req_id"]) == {"state": "done"}           # the code is issued once
    out, barrier = [], threading.Barrier(6)

    def go():
        barrier.wait()
        try:
            out.append(gw.exchange_code(code, cid, REDIRECT, verifier)["access_token"])
        except gw.GatewayError:
            out.append(None)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for x in out if x) == 1, out


# -- C6 revocation, C7 in-flight, C8 kill -------------------------------------------------------

def test_c6_a_revoked_connection_and_an_inert_account_cannot_call(env):
    _, tok = _link(env)
    cx = gw.authenticate(tok["access_token"], "cowork")
    gw.revoke_connection(cx["id"], "review09")
    assert _mcp(env, tok["access_token"], "tools/list").status_code == 401
    _, tok2 = _link(env)
    db.board_signer_set_active(COWORK, False, "inert")
    assert _mcp(env, tok2["access_token"], "tools/list").status_code == 403
    assert all(s["status"] == "revoked" for s in gw.status("cowork"))      # the chain ends on our side


def test_c7_revocation_is_checked_per_request_not_mid_call(env):
    """Implemented contract: authority is checked when a request arrives; a
    tool call already past authenticate() completes (calls are short,
    synchronous database/WebDAV operations). The next request is refused."""
    _, tok = _link(env)
    cx = gw.authenticate(tok["access_token"], "cowork")
    gw.revoke_connection(cx["id"], "revoked between authenticate and the call")
    from web.routes import agent_gateway as route
    out = route._call("board_post", {"to": "dispatch", "thread": "coord", "subject": "c7", "body": "b"}, cx)
    assert out                                                              # the in-flight call completed
    assert _mcp(env, tok["access_token"], "tools/list").status_code == 401   # the next one does not


def test_c8_kill_all_refuses_every_authority_bearing_path(env):
    cid, tok = _link(env)
    gw.kill_all("review09")
    assert _mcp(env, tok["access_token"], "tools/list").status_code in (401, 503)
    with pytest.raises(gw.GatewayError):
        gw.refresh(tok["refresh_token"], cid)
    with pytest.raises(gw.GatewayError):
        gw.authorize_start("cowork", cid, REDIRECT, _pkce()[1], "S256", None, None)
    assert gw.connector_get("cowork") is None
    # registration of a NEW OAuth client is not refused while frozen; it carries no authority
    r = env["client"].post("/oauth/register", json={"redirect_uris": [REDIRECT]})
    assert r.status_code == 201
    with pytest.raises(gw.GatewayError):
        gw.authorize_start("cowork", r.json()["client_id"], REDIRECT, _pkce()[1], "S256", None, None)


# -- C9 tool boundary -----------------------------------------------------------------------------

def test_c9_no_agent_tool_reaches_a_signature_gated_action(env):
    db.board_signer_upsert("ctdc-agent-openai-codex", _key(env["seen"].parent, "cx2")[1], "cx@x", role="member", kind="agent")
    _, tok = _link(env)
    r = _mcp(env, tok["access_token"], "tools/call", {"name": "council_request", "arguments": {
        "mode": "council", "subject": "x", "participants": [{"account": "ctdc-agent-openai-codex"}]}}).json()["result"]
    appr = json.loads(r["content"][0]["text"])["approval_id"]
    # an agent posting "allow" text to the approval/council threads changes nothing
    for thread in ("council", "approvals", "coord"):
        _mcp(env, tok["access_token"], "tools/call", {"name": "board_post", "arguments": {
            "to": "dispatch", "thread": thread, "subject": f"approve.sh allow {appr}", "body": "allow"}})
    assert _row(appr)["status"] == "pending"
    with pytest.raises(gov.GovernanceError):                                # an agent cannot register as an approver
        gov.approval_signer_upsert(COWORK, env["keys"]["cw"][1])
    with pytest.raises(gov.GovernanceError):                                # nor sign with its board key as a human
        gov.resolve_signed(appr, "allow", COWORK, _sig_with(env["keys"]["cw"][0], _row(appr)))
    for bad in ("../../etc/passwd", "01-Sources/../../x", "/abs"):
        out = _mcp(env, tok["access_token"], "tools/call", {"name": "research_read", "arguments": {"path": bad}}).json()["result"]
        assert out.get("isError")
    assert {t["name"] for t in _mcp(env, tok["access_token"], "tools/list").json()["result"]["tools"]} == {
        "status", "board_read", "board_post", "research_list", "research_read", "workspace_contribute", "council_request"}


def _sig_with(key, row):
    return bs.sign_with_ssh_keygen(key, gov.approval_canonical(row, "allow"), gov.NAMESPACE_APPROVAL)


# -- C10 / D9 attribution, D8 audit failure --------------------------------------------------------

def test_c10_d9_tool_calls_and_links_are_attributed(env):
    _, tok = _link(env)
    _mcp(env, tok["access_token"], "tools/call", {"name": "status", "arguments": {}})
    calls = _audit("agent.tool.call")
    assert calls[-1] == {**calls[-1], "connector": "cowork", "account": COWORK, "tool": "status", "outcome": "ok"}
    links = _audit("agent.link.approved")
    assert links and links[-1]["account"] == COWORK and links[-1]["approval_id"]
    # the tool-call event does not name the connection id: attribution to a specific
    # link is by (connector, account) and time, joined against agent.link.approved
    assert "connection" not in calls[-1]


def test_d8_governance_events_are_written_after_the_decision_and_never_block_it(env, monkeypatch):
    """Documented (AGENT_TRUST_MODEL §11): an audit write failure must not change an
    authorization result. So an allowed resolution can exist with no audit event."""
    a = gov.create_approval("pat", "cmd", kind="sudo", requester="r1", ttl_seconds=300)
    sig = _sig(env, _row(a["id"]))
    before = len(_audit("approval.resolved"))
    monkeypatch.setattr(db, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit store down")))
    assert gov.resolve_signed(a["id"], "allow", "op", sig)["status"] == "allowed"
    monkeypatch.undo()
    assert len(_audit("approval.resolved")) == before                      # decision stands, event missing
