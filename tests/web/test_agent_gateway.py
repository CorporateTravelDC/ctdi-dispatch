"""
tests/web/test_agent_gateway.py -- OAuth + MCP gateway for cloud agents
(2026-10-05): signed consent, vendor-driven refresh, OUR side authoritative
(signer deactivation / revocation end the chain; vendor silence only makes
the session dormant; an operator-signed hold keeps our side of the link open
and the vendor's next refresh resumes it), operator dead-man.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main  # noqa: E402
from common import agent_gateway as gw  # noqa: E402
from common import board_sign as bs  # noqa: E402
from common import db  # noqa: E402
from common import governance as gov  # noqa: E402

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _key(tmp_path, name):
    k = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{name}@x", "-f", str(k)], check=True)
    return str(k), (tmp_path / f"{name}.pub").read_text().strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    db.init_db()
    gov._ensure_approvals()
    with db.conn() as c:
        gw.ensure(c)
        for t in ("agent_connectors", "oauth_clients", "oauth_pending", "agent_connections", "oauth_tokens",
                  "approval_signers", "board_signers", "agent_gateway_settings"):
            c.execute(f"DELETE FROM {t}")
    seen = tmp_path / "operator-last-login"
    seen.write_text(f"{int(time.time())}\n")
    monkeypatch.setattr(gw, "OPERATOR_SEEN_FILE", str(seen))
    keys = {"op": _key(tmp_path, "op"), "appr": _key(tmp_path, "appr"), "cw": _key(tmp_path, "cw")}
    db.board_signer_upsert("op", keys["op"][1], "op@x", role="admin", kind="human")
    db.board_signer_upsert("ctdc-agent-anthropic-cowork", keys["cw"][1], "cw@x", role="service", kind="service")
    gov.approval_signer_upsert("op", keys["appr"][1])
    gw.connector_add("cowork", "ctdc-agent-anthropic-cowork", "anthropic")
    return {"keys": keys, "seen": seen, "client": TestClient(web_main.app)}


def _sign(env, approval_id, action="allow"):
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone())
    sig = bs.sign_with_ssh_keygen(env["keys"]["appr"][0], gov.approval_canonical(row, action), gov.NAMESPACE_APPROVAL)
    return gov.resolve_signed(approval_id, action, "op", sig)


def _pkce():
    verifier = "v" * 64
    ch = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, ch


def _link(env):
    c = env["client"]
    cid = c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [REDIRECT]}).json()["client_id"]
    verifier, ch = _pkce()
    r = gw.authorize_start("cowork", cid, REDIRECT, ch, "S256", "st8", "dispatch")
    assert gw.authorize_status(r["req_id"])["state"] == "pending"          # nothing without a signature
    _sign(env, r["approval_id"])
    loc = gw.authorize_status(r["req_id"])["location"]
    code = loc.split("code=")[1].split("&")[0]
    tok = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                       "redirect_uri": REDIRECT, "code_verifier": verifier}).json()
    return cid, tok


def _mcp(env, tok, method, params=None, slug="cowork"):
    return env["client"].post(f"/mcp/{slug}", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                              headers={"Authorization": f"Bearer {tok}"})


def test_consent_requires_a_human_signature_and_codes_are_single_use(env):
    c = env["client"]
    cid = c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).json()["client_id"]
    verifier, ch = _pkce()
    r = gw.authorize_start("cowork", cid, REDIRECT, ch, "S256", None, None)
    _sign(env, r["approval_id"], "deny")
    assert "error=access_denied" in gw.authorize_status(r["req_id"])["location"]
    _, tok = _link(env)
    assert tok["access_token"].startswith("dga_") and tok["refresh_token"].startswith("dgr_")


def test_pkce_and_redirect_are_enforced(env):
    c = env["client"]
    cid = c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).json()["client_id"]
    with pytest.raises(gw.GatewayError):
        gw.authorize_start("cowork", cid, "https://evil.example/cb", _pkce()[1], "S256", None, None)
    with pytest.raises(gw.GatewayError):
        gw.authorize_start("cowork", cid, REDIRECT, "short", "plain", None, None)


def test_mcp_tools_are_attributed_to_the_account(env):
    _, tok = _link(env)
    assert _mcp(env, tok["access_token"], "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in _mcp(env, tok["access_token"], "tools/list").json()["result"]["tools"]}
    assert {"board_read", "board_post", "research_read", "workspace_contribute", "council_request"} <= names
    out = _mcp(env, tok["access_token"], "tools/call", {"name": "board_post", "arguments": {
        "to": "dispatch", "thread": "coord", "subject": "hi", "body": "check-in"}}).json()["result"]
    assert not out.get("isError")
    msgs, _ = db.board_query(thread="coord", limit=50)
    assert any(m["from"] == "ctdc-agent-anthropic-cowork" and m["subject"] == "hi" for m in msgs)
    assert _mcp(env, "nope", "tools/list").status_code == 401
    assert _mcp(env, tok["access_token"], "tools/list", slug="other").status_code == 401   # token bound to its connector


def test_vendor_refresh_rotates_and_old_refresh_dies(env):
    cid, tok = _link(env)
    new = gw.refresh(tok["refresh_token"], cid)
    assert new["refresh_token"] != tok["refresh_token"]
    with pytest.raises(gw.GatewayError):
        gw.refresh(tok["refresh_token"], cid)


def test_our_side_kills_the_chain(env):
    cid, tok = _link(env)
    db.board_signer_set_active("ctdc-agent-anthropic-cowork", False, "inert")
    with pytest.raises(gw.GatewayError):
        gw.refresh(tok["refresh_token"], cid)
    db.board_signer_set_active("ctdc-agent-anthropic-cowork", True)
    assert gw.status("cowork")[0]["status"] == "revoked"                   # stays dead: re-link needed
    with pytest.raises(gw.GatewayError):
        gw.refresh(tok["refresh_token"], cid)


def test_vendor_silence_is_dormant_not_dead_and_resumes(env):
    cid, tok = _link(env)
    assert gw.sweep(time.time() + 3 * 3600) == []                       # quiet for hours is normal
    later = time.time() + 8 * 86400
    env["seen"].write_text(f"{int(later)}\n")
    assert gw.sweep(later)[0]["why"] == "vendor quiet"
    assert gw.status("cowork")[0]["status"] == "dormant"
    assert db.board_signer_get("ctdc-agent-anthropic-cowork")["active"]   # account untouched
    gw.refresh(tok["refresh_token"], cid, now=later)
    assert gw.status("cowork")[0]["status"] == "active"


def test_operator_hold_keeps_the_link_past_idle_expiry(env):
    cid, tok = _link(env)
    r = gw.request_hold("cowork", 60)
    _sign(env, r["approval_id"])
    assert gw.status("cowork")[0]["status"] == "held"
    forty_days = time.time() + 40 * 86400                               # past the 30-day idle grant
    env["seen"].write_text(f"{int(forty_days)}\n")
    out = gw.refresh(tok["refresh_token"], cid, now=forty_days)
    assert out["access_token"] and gw.status("cowork")[0]["status"] == "active"


def test_without_a_hold_the_grant_idles_out(env):
    cid, tok = _link(env)
    forty_days = time.time() + 40 * 86400
    env["seen"].write_text(f"{int(forty_days)}\n")
    with pytest.raises(gw.GatewayError) as e:
        gw.refresh(tok["refresh_token"], cid, now=forty_days)
    assert "idled out" in e.value.detail


def test_operator_dead_man_refuses_but_does_not_revoke(env):
    cid, tok = _link(env)
    env["seen"].write_text(f"{int(time.time() - 20 * 86400)}\n")
    with pytest.raises(gw.GatewayError) as e:
        gw.refresh(tok["refresh_token"], cid)
    assert "dead-man" in e.value.detail and gw.status("cowork")[0]["status"] != "revoked"
    env["seen"].write_text(f"{int(time.time())}\n")
    assert gw.refresh(tok["refresh_token"], cid)["access_token"]


def test_revoke_account_hook(env):
    _link(env)
    assert gw.revoke_account("ctdc-agent-anthropic-cowork", "revoke-tokens") == 1
    assert gw.status("cowork")[0]["status"] == "revoked"


def test_cimd_client_metadata(env):
    url = "https://claude.ai/oauth/claude-client.json"
    doc = {"client_id": url, "client_name": "Claude", "redirect_uris": [REDIRECT]}
    r = gw.authorize_start("cowork", url, REDIRECT, _pkce()[1], "S256", None, None, fetch=lambda u: doc)
    assert r["client_name"] == "Claude"
    with pytest.raises(gw.GatewayError):
        gw.resolve_client("https://evil.example/c.json", fetch=lambda u: {"client_id": "https://other", "redirect_uris": [REDIRECT]})


def test_kill_all_freezes_everything_and_thaw_needs_a_signature(env):
    cid, tok = _link(env)
    assert gw.kill_all("op") == 1
    assert _mcp(env, tok["access_token"], "tools/list").status_code in (401, 503)
    with pytest.raises(gw.GatewayError) as e:
        gw.refresh(tok["refresh_token"], cid)
    assert e.value.status == 503
    with pytest.raises(gw.GatewayError):                                    # no new links either
        gw.authorize_start("cowork", cid, REDIRECT, _pkce()[1], "S256", None, None)
    assert gw.connector_list()[0]["disabled_at"]
    t = gw.request_thaw()
    assert gw.frozen()                                                       # unsigned request changes nothing
    _sign(env, t["approval_id"], "deny")
    assert gw.frozen()
    _sign(env, gw.request_thaw()["approval_id"])
    assert not gw.frozen()
    assert gw.connector_get("cowork") is None                                # connectors stay off until enabled
    gw.connector_set_disabled("cowork", False)
    assert gw.status("cowork")[0]["status"] == "revoked"                     # old link stays dead: re-link
    _, tok2 = _link(env)
    assert _mcp(env, tok2["access_token"], "tools/list").status_code == 200


def test_disable_one_connector_revokes_it(env):
    _, tok = _link(env)
    gw.connector_set_disabled("cowork", True)
    assert gw.status("cowork")[0]["status"] == "revoked"
    assert _mcp(env, tok["access_token"], "tools/list").status_code == 401


def test_cli_loopback_redirects_rfc8252(env):
    """2026-10-05: Claude Code's CLI finishes OAuth on http://localhost:<port>/callback."""
    c = env["client"]
    for bad in (["http://evil.example/cb"], ["http://localhost.evil.example/cb"], ["http://user@localhost/cb"],
                ["http://localhost:99999/cb"], ["ftp://localhost/cb"], ["http://localhost/cb#x"]):
        assert c.post("/oauth/register", json={"redirect_uris": bad}).status_code == 400, bad
    cid = c.post("/oauth/register", json={"client_name": "Claude Code",
                                          "redirect_uris": ["http://localhost:53682/callback"]}).json()["client_id"]
    verifier, ch = _pkce()
    r = gw.authorize_start("cowork", cid, "http://localhost:61234/callback", ch, "S256", "s", None)   # port may differ
    _sign(env, r["approval_id"])
    loc = gw.authorize_status(r["req_id"])["location"]
    assert loc.startswith("http://localhost:61234/callback?code=")
    code = loc.split("code=")[1].split("&")[0]
    tok = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": cid,
                                       "redirect_uri": "http://localhost:61234/callback", "code_verifier": verifier}).json()
    assert tok["access_token"].startswith("dga_")
    for wrong in ("http://localhost:61234/other", "http://127.0.0.1:61234/callback", "https://localhost/callback"):
        with pytest.raises(gw.GatewayError):
            gw.authorize_start("cowork", cid, wrong, ch, "S256", None, None)


def test_consent_page_shows_loopback_callback_instead_of_navigating(env):
    page = web_main.app and TestClient(web_main.app)
    cid = page.post("/oauth/register", json={"redirect_uris": ["http://localhost/callback"]}).json()["client_id"]
    r = page.get("/oauth/authorize", params={"response_type": "code", "client_id": cid,
                                            "redirect_uri": "http://localhost:50000/callback",
                                            "code_challenge": _pkce()[1], "code_challenge_method": "S256",
                                            "resource": "https://agents.example.com/mcp/cowork"})
    assert r.status_code == 200
    assert "function loopback(u)" in r.text and "paste it at the prompt" in r.text
    assert "if(j.state==='redirect'){location.href=j.location;return}" in r.text      # https clients still redirect
