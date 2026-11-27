#!/usr/bin/env python3
"""scripts/captive-release-gate.py -- the signed captive-portal release for the DNS leak watch (2026-11-27, operator).

config/dns-leak-watch holds a device that came back from an off-tunnel episode (a hotel or airport Wi-Fi login page,
docs/DNS_LEAK_WATCH.md "Off-tunnel and captive portals"). The hold lifts on its own once the device passes the
connection check for CAPTIVE_MIN_HOLD_MIN minutes, or sooner with the operator's SIGNED release: an approval of kind
`captive-portal-release`, pattern captive-portal-release-<host>, resolved `allowed` with a human SSH signature
(scripts/approve.sh). The root run calls `check` through RELEASE_APPROVAL_CMD as RELEASE_APPROVAL_USER (never root)
and acts only on an exact "ok <id>" line.

`check` does not trust the stored status alone: it re-verifies the resolution signature the way
governance.resolve_signed() did (active approval signer, active HUMAN board signer, not the requester, approval key
separate from every board key, signature over approval_canonical(row, "allow")), and that the signed command names
this host. An agent may REQUEST a release; only a human's signature makes it count.

  captive-release-gate.py request --host H --reason R     a pending request (reused while one waits); prints the sign command
  captive-release-gate.py check   --host H --since EPOCH  exit 0 + "ok <id>" | exit 1 + "none: <why>"
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import time

KIND = "captive-portal-release"
PREFIX = "captive-portal-release-"
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
MAX_AGE_S = 24 * 3600   # a release signed longer ago than this never counts, whatever --since says


def pattern(host: str) -> str:
    return PREFIX + host


def command(host: str, reason: str) -> str:
    """Canonical JSON: this IS the approval's command, so the signature covers the host and the reason."""
    return json.dumps({"type": KIND, "host": host, "reason": reason}, sort_keys=True, separators=(",", ":"))


def _host_ok(host: str) -> bool:
    return bool(HOST_RE.fullmatch(host or ""))


def pending(host: str) -> dict | None:
    from common import db, governance
    governance._ensure_approvals()
    with db.conn() as c:
        ids = [x["id"] for x in c.execute("SELECT id FROM approval_requests WHERE kind = ? AND command_pattern = ? "
                                          "AND status = 'pending' ORDER BY created_at DESC", (KIND, pattern(host))).fetchall()]
    for i in ids:
        row = governance.approval_view(i)          # applies expiry-on-read
        if row and row["status"] == "pending":
            return row
    return None


def request(host: str, reason: str, requester: str | None = None) -> dict:
    """Create the release request unless one for this host is still pending. Returns {"id", "new"}."""
    from common import governance
    if not _host_ok(host):
        raise ValueError("bad host name")
    reason = " ".join((reason or "").split())[:300]
    if not reason:
        raise ValueError("a reason is required")
    p = pending(host)
    if p:
        return {"id": p["id"], "new": False}
    r = governance.create_approval(pattern(host), command(host, reason), kind=KIND,
                                   requester=requester or f"operator:{getpass.getuser()}",
                                   reasoning=f"captive-portal release for {host}: {reason}",
                                   ttl_seconds=governance.approval_ttl(KIND))
    return {"id": r["id"], "new": True}


def verify(row: dict, host: str) -> str:
    """'' when ROW is a human-signed allow of a release for HOST, else the reason it is not."""
    from common import board_sign, db, governance
    if row.get("kind") != KIND or row.get("command_pattern") != pattern(host) or row.get("status") != "allowed":
        return "not an allowed release for this host"
    try:
        if json.loads(row.get("command") or "").get("host") != host:
            return "signed command names another host"
    except (ValueError, AttributeError):
        return "signed command is not a release"
    signer, sig = row.get("resolved_by"), row.get("resolution_sig")
    if not signer or not sig:
        return "unsigned"
    appr = governance.approval_signer_get(signer)
    if not appr or not appr["active"]:
        return "unknown or inactive approval signer"
    board = db.board_signer_get(signer)
    if not board or not board.get("active") or board.get("kind") != "human":
        return "signer is not an active human account"
    if row.get("requester") and row["requester"] == signer:
        return "the requester signed its own request"
    try:
        _, ablob, _ = board_sign.pubkey_line_to_blob(appr["pubkey"])
        if ablob in governance._board_pubkey_blobs():
            return "approval key equals a board key"
        ok = board_sign.verify(appr["pubkey"], sig, governance.approval_canonical(row, "allow"), signer,
                               namespace=governance.NAMESPACE_APPROVAL)
    except board_sign.BoardSignError:
        return "signature rejected"
    return "" if ok else "signature does not verify"


def check(host: str, since: float, now: float | None = None) -> tuple[dict | None, str]:
    """(row, '') for the newest verified release of HOST resolved at or after SINCE (and within MAX_AGE_S), else
    (None, why)."""
    from common import db, governance
    if not _host_ok(host):
        return None, "bad host name"
    now = time.time() if now is None else now
    governance._ensure_approvals()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM approval_requests WHERE kind = ? AND command_pattern = ? AND status = 'allowed' "
            "AND resolved_by IS NOT NULL AND resolution_sig IS NOT NULL AND resolved_at >= ? AND resolved_at <= ? "
            "ORDER BY resolved_at DESC LIMIT 5", (KIND, pattern(host), max(float(since), now - MAX_AGE_S), now + 60)).fetchall()]
    why = "no signed release since the hold began"
    for row in rows:
        why = verify(row, host)
        if not why:
            return row, ""
    return None, why


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("request"); q.add_argument("--host", required=True); q.add_argument("--reason", required=True)
    c = sub.add_parser("check"); c.add_argument("--host", required=True); c.add_argument("--since", type=int, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "check":
        try:
            row, why = check(a.host, a.since)
        except Exception as e:  # noqa: BLE001 -- the root run reads stdout only; any failure is "no release"
            row, why = None, f"lookup failed ({type(e).__name__})"
        if not row:
            print(f"none: {why}")
            return 1
        print(f"ok {row['id']}")
        return 0
    try:
        r = request(a.host, a.reason)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 65
    print(f"{'requested' if r['new'] else 'already pending'} {r['id']}; sign with: scripts/approve.sh allow {r['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
