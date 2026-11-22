"""2026-11-22 (operator): a connector renewal whose reply is lost must not kill the chain.

A refresh rotates the token. If the client never receives the new pair (tunnel 530s, a WiFi stall, an uplink failover),
it retries with the rotated token. That retry is accepted only while nothing has used the successor pair: no tool call
since the rotation and the successor refresh never redeemed. The unused pair is then revoked and a fresh one issued."""
from __future__ import annotations

import time

import pytest

from common import agent_gateway as gw
from common import db

CLIENT = "https://client.example/meta.json"


@pytest.fixture
def cx(monkeypatch):
    db.init_db()
    with db.conn() as c:
        gw.ensure(c)
        for t in ("agent_connections", "oauth_tokens", "agent_gateway_settings"):
            c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(gw, "account_live", lambda account: (True, ""))
    monkeypatch.setattr(gw, "operator_seen_ok", lambda now=None: (True, ""))
    now = time.time() - 7200
    gw._q("INSERT INTO agent_connections (id, slug, account, client_id, status, created_at, last_renewal_at) "
          "VALUES ('cx_test', 'cowork', 'ctdc-agent-anthropic-cowork', ?, 'active', ?, ?)", (CLIENT, now, now))
    return {"pair": gw._issue("cx_test", now, now + gw.REFRESH_IDLE_S), "t0": now}


def _call(access, now):
    return gw.authenticate(access, "cowork", now=now)


def test_a_lost_reply_retry_succeeds_and_retires_the_unused_pair(cx):
    first = cx["pair"]
    lost = gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 60)          # this reply never arrives
    retry = gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 120)        # client retries with the old token
    assert retry["refresh_token"] != lost["refresh_token"]
    with pytest.raises(gw.GatewayError):
        gw.refresh(lost["refresh_token"], CLIENT, now=cx["t0"] + 130)            # the lost pair was revoked
    with pytest.raises(gw.GatewayError):
        _call(lost["access_token"], cx["t0"] + 130)
    assert _call(retry["access_token"], cx["t0"] + 140)["id"] == "cx_test"       # the fresh pair works
    nxt = gw.refresh(retry["refresh_token"], CLIENT, now=cx["t0"] + 200)          # and the chain continues
    assert nxt["access_token"]


def test_once_the_successor_made_a_call_the_old_token_is_refused(cx):
    first = cx["pair"]
    new = gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 60)
    _call(new["access_token"], cx["t0"] + 90)                                     # the client got it and used it
    with pytest.raises(gw.GatewayError, match="already-rotated"):
        gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 120)


def test_once_the_successor_refresh_was_redeemed_the_old_token_is_refused(cx):
    first = cx["pair"]
    new = gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 60)
    gw.refresh(new["refresh_token"], CLIENT, now=cx["t0"] + 90)
    with pytest.raises(gw.GatewayError, match="already-rotated"):
        gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 120)


def test_the_retry_window_is_bounded(cx):
    first = cx["pair"]
    gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 60)
    with pytest.raises(gw.GatewayError, match="already-rotated"):
        gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 60 + gw.LOST_REPLY_GRACE_S + 1)


def test_revoked_tokens_and_other_clients_stay_refused(cx):
    first = cx["pair"]
    gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 60)
    with pytest.raises(gw.GatewayError):
        gw.refresh(first["refresh_token"], "https://other.example/meta.json", now=cx["t0"] + 90)
    gw.revoke_connection("cx_test", "test", now=cx["t0"] + 100)
    with pytest.raises(gw.GatewayError):
        gw.refresh(first["refresh_token"], CLIENT, now=cx["t0"] + 120)
