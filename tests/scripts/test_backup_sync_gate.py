"""2026-11-22 (operator): the weekly signed gate on the Borg -> Proton sync (scripts/backup/sync_gate.py).
Same machinery as the liveness switch: a backup-sync approval resolved `allowed` with a human SSH signature
opens the sync for 7 days; nothing else does (not a pending, denied, unsigned or stale one)."""
from __future__ import annotations

import importlib.util
import subprocess
import time
from pathlib import Path

import pytest

from common import board_sign as bs
from common import db
from common import governance as gov

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sync_gate", ROOT / "scripts/backup/sync_gate.py")
sync_gate = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sync_gate)


def _key(tmp_path, name):
    k = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{name}@x", "-f", str(k)], check=True)
    return str(k), (tmp_path / f"{name}.pub").read_text().strip()


@pytest.fixture
def gate(tmp_path, monkeypatch):
    db.init_db()
    gov._ensure_approvals()
    with db.conn() as c:
        c.execute("DELETE FROM approval_requests"); c.execute("DELETE FROM approval_signers")
    priv, pub = _key(tmp_path, "appr")
    db.board_signer_upsert("op", _key(tmp_path, "op")[1], "op@x", role="admin", kind="human")
    gov.approval_signer_upsert("op", pub)
    pushes = []
    import common.ntfy_push as np
    monkeypatch.setattr(np, "send", lambda *a, **k: pushes.append(k))
    return {"priv": priv, "pushes": pushes}


def _resolve(g, rid, action="allow"):
    with db.conn() as c:
        row = dict(c.execute("SELECT * FROM approval_requests WHERE id = ?", (rid,)).fetchone())
    sig = bs.sign_with_ssh_keygen(g["priv"], gov.approval_canonical(row, action), gov.NAMESPACE_APPROVAL)
    return gov.resolve_signed(rid, action, "op", sig)


def test_backup_sync_is_a_registered_approval_kind_with_a_bounded_request_ttl():
    assert "backup-sync" in gov.APPROVAL_KINDS
    assert gov.approval_ttl("backup-sync") == 4 * 3600
    assert max(gov.MAX_TTL_S.values()) <= 4 * 3600            # the platform-wide cap holds


def test_no_signature_means_held_and_one_request_is_pending_at_a_time(gate):
    assert sync_gate.check() is None
    a = sync_gate.request("proton:ctdi-backup")
    b = sync_gate.request("proton:ctdi-backup")
    assert a["new"] and not b["new"] and a["id"] == b["id"]       # no second request while one waits
    assert len(gate["pushes"]) == 1 and "approve.sh allow" in gate["pushes"][0]["message"]
    assert sync_gate.check() is None                               # pending is not allowed


def test_a_signed_allow_opens_the_sync_for_seven_days_only(gate):
    rid = sync_gate.request("proton:ctdi-backup")["id"]
    _resolve(gate, rid, "allow")
    r = sync_gate.check()
    assert r and r["id"] == rid
    assert sync_gate.check(now=time.time() + 6.9 * 86400)
    assert sync_gate.check(now=time.time() + 7.1 * 86400) is None   # lapsed: held again
    assert sync_gate.request()["new"]                               # and a fresh request can be made


def test_a_denied_request_does_not_open_the_sync(gate):
    rid = sync_gate.request()["id"]
    _resolve(gate, rid, "deny")
    assert sync_gate.check() is None


def test_another_kind_or_pattern_never_counts(gate):
    r = gov.create_approval("liveness-reactivate-x", "team-liveness.sh --reactivate x", kind="liveness",
                            requester="operator:test", ttl_seconds=600)
    _resolve(gate, r["id"], "allow")
    assert sync_gate.check() is None


def test_cli_exit_codes(gate, capsys):
    assert sync_gate.main(["check"]) == 1 and "none" in capsys.readouterr().out
    rid = sync_gate.request()["id"]; _resolve(gate, rid)
    assert sync_gate.main(["check"]) == 0 and capsys.readouterr().out.startswith(f"ok {rid}")
