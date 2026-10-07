"""
tests/web/test_console.py -- operator phone console (2026-10-05): sign-in only
by a human SSH-signed approval, bound to the browser that asked; one approval
-> one session; CSRF on every POST; tailnet Host only; Executive Standard and
agent-gateway actions; thaw / hold stay signature-gated.
"""
from __future__ import annotations

import re
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
from common import es_invites as es  # noqa: E402
from common import governance as gov  # noqa: E402
from web.routes import console  # noqa: E402

HOST = "corporatetraveldc-dispatch.tailxxxxxxx.ts.net"


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
        es.ensure(c)
        console.ensure(c)
        for t in ("console_sessions", "approval_signers", "board_signers", "agent_connectors", "agent_connections",
                  "agent_gateway_settings", "es_grants", "es_promos", "es_sessions", "es_invite_settings",
                  "es_invite_events", "approval_requests"):
            c.execute(f"DELETE FROM {t}")
    keys = {"op": _key(tmp_path, "op"), "appr": _key(tmp_path, "appr"), "cw": _key(tmp_path, "cw")}
    db.board_signer_upsert("op", keys["op"][1], "op@x", role="admin", kind="human")
    db.board_signer_upsert("ctdc-agent-anthropic-cowork", keys["cw"][1], "cw@x", role="service", kind="service")
    gov.approval_signer_upsert("op", keys["appr"][1])
    gw.connector_add("cowork", "ctdc-agent-anthropic-cowork", "anthropic")
    seen = tmp_path / "seen"
    seen.write_text(f"{int(time.time())}\n")
    monkeypatch.setattr(gw, "OPERATOR_SEEN_FILE", str(seen))
    return {"keys": keys}


def _client():
    return TestClient(web_main.app, base_url=f"https://{HOST}")


def _sign(env, approval_id, action="allow"):
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (approval_id,)).fetchone())
    sig = bs.sign_with_ssh_keygen(env["keys"]["appr"][0], gov.approval_canonical(row, action), gov.NAMESPACE_APPROVAL)
    return gov.resolve_signed(approval_id, action, "op", sig)


def _aid(html):
    return re.search(r"approve\.sh allow ([0-9a-f-]{36})", html).group(1)


def _signed_in(env):
    c = _client()
    r = c.post("/console/login")
    assert r.status_code == 200 and "Waiting for your signature" in r.text   # followed the 303
    _sign(env, _aid(r.text))
    r = c.get("/console")
    assert "Operator console" in r.text and "Sign out" in r.text
    return c, re.search(r"name='csrf' value='([^']+)'", r.text).group(1)


def test_only_the_tailnet_host(env):
    c = TestClient(web_main.app, base_url="https://dispatch.example.com")
    assert c.get("/console").status_code == 404
    assert c.post("/console/login").status_code == 404


def test_no_session_without_a_signature(env):
    c = _client()
    r = c.post("/console/login")
    aid = _aid(r.text)
    assert "Waiting" in c.get("/console").text                          # pending: still no session
    _sign(env, aid, "deny")
    assert "Sign in with your approval key" in c.get("/console").text
    with db.conn() as k:
        assert k.execute("SELECT count(*) FROM console_sessions").fetchone()[0] == 0


def test_the_signature_only_unlocks_the_browser_that_asked(env):
    asker, thief = _client(), _client()
    aid = _aid(asker.post("/console/login").text)
    _sign(env, aid)
    thief.cookies.set(console.PENDING, f"{aid}.guessed-nonce", domain=HOST)
    assert "Sign out" not in thief.get("/console").text
    assert "Sign out" in asker.get("/console").text
    replay = _client()                                                    # the same approval never mints twice
    replay.cookies.set(console.PENDING, asker.cookies.get(console.PENDING) or "", domain=HOST)
    assert "Sign out" not in replay.get("/console").text


def test_inert_operator_loses_the_console(env):
    c, _ = _signed_in(env)
    db.board_signer_set_active("op", False, "inert")
    assert "Sign in with your approval key" in c.get("/console").text


def test_csrf_required(env):
    c, csrf = _signed_in(env)
    r = c.post("/console/act", data={"action": "es-freeze", "csrf": "wrong"})
    assert r.status_code == 403 and not es.frozen()
    c.post("/console/act", data={"action": "es-freeze", "csrf": csrf})
    assert es.frozen()


def test_reader_and_promo_actions(env):
    c, csrf = _signed_in(env)
    r = c.post("/console/act", data={"action": "es-invite", "csrf": csrf, "email": "r@example.com", "days": "", "devices": "3"})
    assert "Permanent until you revoke it" in r.text and es.INVITE_BASE + "/i/" in r.text
    r = c.post("/console/act", data={"action": "es-promo", "csrf": csrf, "label": "booth", "uses": "5",
                                      "code_days": "7", "access_days": "7"})
    assert re.search(r"ES-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{2}", r.text)
    page = c.get("/console").text
    assert "r@example.com" in page and "booth" in page
    gid = es.grants()[0]["id"]
    c.post("/console/act", data={"action": "es-revoke", "csrf": csrf, "ref": gid})
    assert es.grants() == []
    r = c.post("/console/act", data={"action": "es-kill-all", "csrf": csrf})
    assert "I mean it" in r.text and not es.frozen()                      # confirmation required
    c.post("/console/act", data={"action": "es-kill-all", "csrf": csrf, "confirm": "yes"})
    assert es.frozen()
    assert [e["actor"] for e in es.events()][0] == "op@console"


def test_gateway_kill_all_and_signed_thaw(env):
    c, csrf = _signed_in(env)
    c.post("/console/act", data={"action": "gw-kill-all", "csrf": csrf, "confirm": "yes"})
    assert gw.frozen() and gw.connector_list()[0]["disabled_at"]
    r = c.post("/console/act", data={"action": "gw-thaw", "csrf": csrf})
    assert gw.frozen()                                                     # the console alone cannot re-open
    _sign(env, _aid(r.text))
    assert not gw.frozen()
    c.post("/console/act", data={"action": "gw-enable", "csrf": csrf, "ref": "cowork"})
    assert gw.connector_get("cowork")


def test_logout(env):
    c, csrf = _signed_in(env)
    c.post("/console/logout", data={"csrf": csrf})
    assert "Sign in with your approval key" in c.get("/console").text


def test_console_is_installable(env):
    c = _client()
    page = c.get("/console").text
    assert "rel='manifest' href='/console/manifest.webmanifest'" in page and "apple-touch-icon" in page
    m = c.get("/console/manifest.webmanifest").json()
    assert m["display"] == "standalone" and m["start_url"] == "/console"
    for i in m["icons"]:
        r = c.get(i["src"])
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get("/console/icons/../../main.py").status_code == 404
    assert TestClient(web_main.app, base_url="https://dispatch.example.com").get(
        "/console/manifest.webmanifest").status_code == 404


def test_console_batch(env):
    c, csrf = _signed_in(env)
    r = c.post("/console/act", data={"action": "es-batch", "csrf": csrf, "batch": "x@example.com,Xavier\ny@example.com",
                                      "days": "", "campaign": "core"})
    assert "2 invite(s) created" in r.text and r.text.count(es.INVITE_BASE + "/i/") >= 2
    r = c.post("/console/act", data={"action": "es-batch", "csrf": csrf, "batch": "bad line", "days": ""})
    assert "nothing issued" in r.text


def test_dashboard_service_worker_never_swallows_the_console():
    """2026-10-05: the runner dashboard's Workbox navigateFallback served its
    cached shell for /console on the shared tailnet origin."""
    cfg = (Path(__file__).resolve().parents[2] / "src" / "runner" / "frontend" / "vite.config.js").read_text()
    deny = cfg.split("navigateFallbackDenylist:", 1)[1].split("]", 1)[0]
    assert "\\/console" in deny and "\\/api\\/" in deny
