"""2026-11-27 (operator): an operator-signed hold lasts its full term. A renewal used to clear it, so a 90-day hold
vanished at the vendor's first hourly refresh and the next quiet week made the session dormant again."""
from __future__ import annotations

import json
import time

import pytest

from common import agent_gateway as gw
from common import db

CLIENT = "https://client.example/meta.json"
DAY = 86400


@pytest.fixture
def cx(monkeypatch):
    db.init_db()
    with db.conn() as c:
        gw.ensure(c)
        for t in ("agent_connections", "oauth_tokens", "agent_gateway_settings"):
            c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(gw, "account_live", lambda account: (True, ""))
    monkeypatch.setattr(gw, "operator_seen_ok", lambda now=None: (True, ""))
    monkeypatch.setattr(gw, "_notify", lambda *a, **k: None)
    now = time.time() - 60
    gw._q("INSERT INTO agent_connections (id, slug, account, client_id, status, created_at, last_renewal_at) "
          "VALUES ('cx_test', 'cowork', 'ctdc-agent-anthropic-cowork', ?, 'active', ?, ?)", (CLIENT, now, now))
    return {"pair": gw._issue("cx_test", now, now + gw.REFRESH_IDLE_S), "t0": now}


def hold(days, now):
    until = now + days * DAY
    spec = {"type": "connector-hold", "connection": "cx_test", "connector": "cowork",
            "account": "ctdc-agent-anthropic-cowork", "hold_until": until}
    gw.on_approval_resolved({"kind": "connector-hold", "status": "allowed", "id": "appr_test", "command": json.dumps(spec)})
    return until


def st():
    return gw.status("cowork")[0]


def test_a_hold_survives_renewals_and_its_grant_reaches_the_end(cx):
    until = hold(90, cx["t0"])
    assert st()["status"] == "held"
    p = gw.refresh(cx["pair"]["refresh_token"], CLIENT, now=cx["t0"] + 3600)     # the vendor's next renewal
    assert st()["status"] == "active" and st()["hold_until"] == until               # the hold is still there
    assert gw.refresh_grant_until("cx_test") >= until
    p = gw.refresh(p["refresh_token"], CLIENT, now=cx["t0"] + 7200)
    assert st()["hold_until"] == until and gw.refresh_grant_until("cx_test") >= until


def test_quiet_under_a_live_hold_is_held_not_dormant_and_expiry_restores_the_rules(cx):
    until = hold(90, cx["t0"])
    gw.refresh(cx["pair"]["refresh_token"], CLIENT, now=cx["t0"] + 3600)
    assert gw.sweep(cx["t0"] + 20 * DAY) == []                                     # no dormant notice
    assert st()["status"] == "held"
    out = gw.sweep(until + 1)
    assert out and out[0]["why"] == "hold expired" and st()["status"] == "dormant"


def test_without_a_hold_quiet_is_still_dormant_and_release_ends_a_hold(cx):
    assert gw.sweep(cx["t0"] + 8 * DAY)[0]["why"] == "vendor quiet"
    gw._q("UPDATE agent_connections SET status = 'active' WHERE id = 'cx_test'")
    hold(90, cx["t0"])
    gw.release_hold("cowork")
    gw.refresh(cx["pair"]["refresh_token"], CLIENT, now=cx["t0"] + 3600)
    assert st()["hold_until"] is None


def test_revocation_still_wins_over_a_hold(cx):
    hold(90, cx["t0"])
    gw.revoke_connection("cx_test", "test", now=cx["t0"] + 10)
    with pytest.raises(gw.GatewayError):
        gw.refresh(cx["pair"]["refresh_token"], CLIENT, now=cx["t0"] + 20)
