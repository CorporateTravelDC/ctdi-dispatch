"""2026-11-27 (operator): the signed captive-portal release for the DNS leak watch (scripts/captive-release-gate.py).
Kind `captive-portal-release`: anyone may request one, only a human's SSH signature (governance.resolve_signed, via
scripts/approve.sh) makes it count, and `check` re-verifies that signature itself before the root run acts on it."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest

from common import board_sign as bs
from common import db
from common import governance as gov

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("captive_release_gate", ROOT / "scripts/captive-release-gate.py")
gate_mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(gate_mod)


def _key(tmp_path, name):
    k = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{name}@x", "-f", str(k)], check=True)
    return str(k), (tmp_path / f"{name}.pub").read_text().strip()


@pytest.fixture
def gate(tmp_path):
    db.init_db()
    gov._ensure_approvals()
    with db.conn() as c:
        c.execute("DELETE FROM approval_requests"); c.execute("DELETE FROM approval_signers")
    priv, pub = _key(tmp_path, "appr")
    db.board_signer_upsert("op", _key(tmp_path, "op")[1], "op@x", role="admin", kind="human")
    gov.approval_signer_upsert("op", pub)
    bot_priv, bot_pub = _key(tmp_path, "bot")
    db.board_signer_upsert("bot1", bot_pub, "bot1@x", role="member", kind="agent")
    return {"priv": priv, "bot_priv": bot_priv, "bot_pub": bot_pub}


def _row(rid):
    with db.conn() as c:
        return dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (rid,)).fetchone())


def _resolve(g, rid, action="allow", signer="op", now=None):
    sig = bs.sign_with_ssh_keygen(g["priv"], gov.approval_canonical(_row(rid), action), gov.NAMESPACE_APPROVAL)
    return gov.resolve_signed(rid, action, signer, sig, now=now)


def test_registered_kind_with_a_short_request_window():
    assert "captive-portal-release" in gov.APPROVAL_KINDS
    assert gov.approval_ttl("captive-portal-release") == 600
    assert gov.APPROVAL_TTL_POLICY["captive-portal-release"][2] <= 1800
    assert max(gov.MAX_TTL_S.values()) <= 4 * 3600            # the platform-wide cap holds


def test_request_is_pending_and_reused_and_does_not_release(gate):
    since = time.time() - 60
    a = gate_mod.request("laptop1", "hotel Wi-Fi portal")
    b = gate_mod.request("laptop1", "again")
    assert a["new"] and not b["new"] and a["id"] == b["id"]
    row = _row(a["id"])
    assert row["kind"] == "captive-portal-release" and row["command_pattern"] == "captive-portal-release-laptop1"
    assert json.loads(row["command"])["host"] == "laptop1"
    assert gate_mod.check("laptop1", since)[0] is None          # pending is not a release


def test_human_signed_allow_releases_that_host_only(gate):
    since = time.time() - 60
    rid = gate_mod.request("laptop1", "hotel Wi-Fi portal")["id"]
    _resolve(gate, rid)
    row, why = gate_mod.check("laptop1", since)
    assert row and row["id"] == rid and why == ""
    assert gate_mod.check("phone2", since)[0] is None            # wrong host
    assert gate_mod.check("laptop1", time.time() + 5)[0] is None  # signed before this hold began


def test_denied_or_other_kind_never_counts(gate):
    since = time.time() - 60
    rid = gate_mod.request("laptop1", "portal")["id"]
    _resolve(gate, rid, "deny")
    assert gate_mod.check("laptop1", since)[0] is None
    r = gov.create_approval("captive-portal-release-laptop1", "x", kind="liveness", requester="operator:t", ttl_seconds=600)
    _resolve(gate, r["id"])
    assert gate_mod.check("laptop1", since)[0] is None


def test_an_agent_can_request_but_cannot_sign(gate):
    since = time.time() - 60
    rid = gate_mod.request("laptop1", "portal", requester="bot1")["id"]
    sig = bs.sign_with_ssh_keygen(gate["bot_priv"], gov.approval_canonical(_row(rid), "allow"), gov.NAMESPACE_APPROVAL)
    with pytest.raises(gov.GovernanceError):
        gov.resolve_signed(rid, "allow", "bot1", sig)              # no approval key for an agent
    with pytest.raises(gov.GovernanceError):
        gov.approval_signer_upsert("bot1", gate["bot_pub"])       # and it cannot register one
    assert gate_mod.check("laptop1", since)[0] is None
    _resolve(gate, rid)                                            # the human can sign the agent's request
    assert gate_mod.check("laptop1", since)[0]["id"] == rid


def test_check_reverifies_the_signature_not_just_the_status(gate):
    since = time.time() - 60
    rid = gate_mod.request("laptop1", "portal")["id"]
    sig = bs.sign_with_ssh_keygen(gate["bot_priv"], gov.approval_canonical(_row(rid), "allow"), gov.NAMESPACE_APPROVAL)
    with db.conn() as c:   # a row written straight into the table, bypassing resolve_signed
        c.execute("UPDATE approval_requests SET status = 'allowed', resolved_at = ?, resolved_by = 'bot1', "
                  "resolution_sig = ? WHERE id = ?", (time.time(), sig, rid))
    row, why = gate_mod.check("laptop1", since)
    assert row is None and "approval signer" in why
    with db.conn() as c:   # the human's name on it, but a signature by someone else
        c.execute("UPDATE approval_requests SET resolved_by = 'op' WHERE id = ?", (rid,))
    assert gate_mod.check("laptop1", since) == (None, "signature does not verify")


def test_a_tampered_host_or_a_deactivated_signer_fails(gate):
    since = time.time() - 60
    rid = gate_mod.request("laptop1", "portal")["id"]
    _resolve(gate, rid)
    with db.conn() as c:
        c.execute("UPDATE approval_requests SET command = ? WHERE id = ?", (gate_mod.command("laptop1", "other"), rid))
    assert gate_mod.check("laptop1", since) == (None, "signature does not verify")
    rid2 = gate_mod.request("laptop1", "portal 2")["id"]
    _resolve(gate, rid2)
    assert gate_mod.check("laptop1", since)[0]["id"] == rid2
    gov.approval_signer_set_active("op", False)
    assert gate_mod.check("laptop1", since)[0] is None


def test_an_expired_request_cannot_be_signed_and_old_releases_lapse(gate):
    since = time.time() - 60
    rid = gate_mod.request("laptop1", "portal")["id"]
    with pytest.raises(gov.GovernanceError):
        _resolve(gate, rid, now=time.time() + 601)                 # past the 10-minute request window
    assert gate_mod.check("laptop1", since)[0] is None
    rid = gate_mod.request("laptop1", "portal")["id"]
    _resolve(gate, rid)
    assert gate_mod.check("laptop1", 0, now=time.time() + gate_mod.MAX_AGE_S + 120)[0] is None


def test_cli(gate, capsys):
    assert gate_mod.main(["check", "--host", "laptop1", "--since", "0"]) == 1
    assert capsys.readouterr().out.startswith("none: ")
    assert gate_mod.main(["request", "--host", "bad host;x", "--reason", "r"]) == 65
    assert gate_mod.main(["request", "--host", "laptop1", "--reason", "portal"]) == 0
    out = capsys.readouterr().out
    rid = out.split()[1].rstrip(";")
    assert out.startswith("requested ") and f"scripts/approve.sh allow {rid}" in out
    _resolve(gate, rid)
    assert gate_mod.main(["check", "--host", "laptop1", "--since", "0"]) == 0
    assert capsys.readouterr().out.strip() == f"ok {rid}"
