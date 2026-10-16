"""2026-10-15: board-signer-ctl.sh liveness-approval -- the offline-lockdown authority lookup for
team-liveness.sh. Only a SIGNED, allowed approval of kind liveness for that exact account and action,
resolved at or after <since>, counts. Synthetic sqlite DB; signatures are stand-in strings (the lookup
checks that a resolution signature was recorded; resolve_signed() verifies it at resolution time)."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CTL = str(REPO / "scripts/board-signer-ctl.sh")


def _env(tmp):
    return {**os.environ, "DISPATCH_DB_BACKEND": "sqlite", "DISPATCH_DB": str(tmp / "t.db"), "PYTHONPATH": str(REPO / "src")}


def _seed(tmp, rows):
    code = ("import sys,time\nfrom common import db, governance\ngovernance._ensure_approvals()\n"
            "with db.conn() as c:\n"
            "    for rid, kind, pat, status, by, sig, at in " + repr(rows) + ":\n"
            "        c.execute(\"INSERT INTO approval_requests (id, command_pattern, command, status, created_at, expires_at, kind, resolved_at, resolved_by, resolution_sig) VALUES (?,?,?,?,?,?,?,?,?,?)\",\n"
            "                  (rid, pat, pat, status, at, at + 600, kind, at, by, sig))\n")
    subprocess.run(["python3", "-c", code], env=_env(tmp), check=True, capture_output=True)


def look(tmp, acct, action, since):
    return subprocess.run([CTL, "liveness-approval", acct, action, str(int(since))], env=_env(tmp),
                          capture_output=True, text=True, timeout=60).stdout.strip()


def test_signed_allowed_matching_approval_counts(tmp_path):
    now = time.time()
    _seed(tmp_path, [("00000000-0000-0000-0000-000000000001", "liveness", "liveness-inert-bot1", "allowed", "op", "sig", now - 60)])
    assert look(tmp_path, "bot1", "inert", now - 600) == "ok 00000000-0000-0000-0000-000000000001"


def test_everything_else_is_none(tmp_path):
    now = time.time()
    _seed(tmp_path, [
        ("00000000-0000-0000-0000-000000000002", "liveness", "liveness-inert-bot1", "allowed", "op", "sig", now - 3600),  # too old
        ("00000000-0000-0000-0000-000000000003", "liveness", "liveness-inert-bot1", "denied", "op", "sig", now - 60),
        ("00000000-0000-0000-0000-000000000004", "liveness", "liveness-inert-bot1", "allowed", None, None, now - 60),   # unsigned
        ("00000000-0000-0000-0000-000000000005", "sudo", "liveness-inert-bot1", "allowed", "op", "sig", now - 60),      # wrong kind
        ("00000000-0000-0000-0000-000000000006", "liveness", "liveness-reactivate-bot1", "allowed", "op", "sig", now - 60),
        ("00000000-0000-0000-0000-000000000007", "liveness", "liveness-inert-bot2", "allowed", "op", "sig", now - 60),
    ])
    assert look(tmp_path, "bot1", "inert", now - 600) == "none"


def test_bad_arguments_refused(tmp_path):
    r = subprocess.run([CTL, "liveness-approval", "bot1", "delete", "1"], env=_env(tmp_path), capture_output=True, text=True)
    assert r.returncode == 64


def test_liveness_request_creates_a_pending_liveness_approval(tmp_path):
    r = subprocess.run([CTL, "liveness-request", "bot1", "reactivate", "lockdown drill"], env=_env(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "liveness-reactivate-bot1" in r.stdout and "approve.sh allow" in r.stdout
    rid = r.stdout.split()[1]
    code = ("import sys\nfrom common import db\nrow = db.get_approval_request(sys.argv[1])\n"
            "print(row['status'], row.get('kind'), row['command_pattern'])\n")
    out = subprocess.run(["python3", "-c", code, rid], env=_env(tmp_path), capture_output=True, text=True).stdout.strip()
    assert out == "pending liveness liveness-reactivate-bot1"
    assert look(tmp_path, "bot1", "reactivate", 0) == "none"        # pending is not authority


def test_liveness_request_refuses_bad_action(tmp_path):
    r = subprocess.run([CTL, "liveness-request", "bot1", "delete", "x"], env=_env(tmp_path), capture_output=True, text=True)
    assert r.returncode == 64
