#!/usr/bin/env python3
"""scripts/backup/sync_gate.py -- the weekly signed gate on the off-box backup sync (2026-11-22, operator).

Operator: "Let's do the liveness and encrypted rclone": the Borg repository lives on the USB stick and syncs to
Proton, but the sync only runs while the operator has SIGNED a `backup-sync` approval within the last
GATE_DAYS (7). Same machinery as the liveness switch (scripts/team-liveness.sh, kind `liveness`): an
approval_requests row resolved `allowed` with a human SSH signature (scripts/approve.sh), looked up by
pattern and resolution time. A lapsed gate HOLDS the sync -- nothing on Proton is touched or deleted, and
the nightly Borg archive on the stick still runs -- and asks for a new signature (one pending request at
a time).

  sync_gate.py check   [--days N]          exit 0 + "ok <id> <resolved_at>" | exit 1 + "none"
  sync_gate.py request [--target T]        a new request unless one is already pending; prints the sign command
"""
from __future__ import annotations

import argparse
import getpass
import sys
import time

PATTERN = "backup-sync-week"
GATE_DAYS = 7


def check(days: float = GATE_DAYS, now: float | None = None) -> dict | None:
    """The newest signed, allowed backup-sync approval resolved within `days`, else None."""
    from common import db, governance
    now = time.time() if now is None else now
    governance._ensure_approvals()
    with db.conn() as c:
        r = c.execute("SELECT id, resolved_at, resolved_by FROM approval_requests WHERE kind = 'backup-sync'"
                      " AND command_pattern = ? AND status = 'allowed' AND resolved_by IS NOT NULL"
                      " AND resolution_sig IS NOT NULL AND resolved_at >= ? ORDER BY resolved_at DESC LIMIT 1",
                      (PATTERN, now - float(days) * 86400)).fetchone()
    return dict(r) if r else None


def pending(now: float | None = None) -> dict | None:
    from common import db, governance
    now = time.time() if now is None else now
    governance._ensure_approvals()
    with db.conn() as c:
        ids = [x["id"] for x in c.execute("SELECT id FROM approval_requests WHERE kind = 'backup-sync' AND "
                                          "command_pattern = ? AND status = 'pending' ORDER BY created_at DESC",
                                          (PATTERN,)).fetchall()]
    for i in ids:
        row = governance.approval_view(i)          # applies expiry-on-read
        if row and row["status"] == "pending":
            return row
    return None


def request(target: str = "", requester: str | None = None) -> dict:
    """Create the weekly request unless one is still pending. Returns {"id", "new"}."""
    from common import governance
    p = pending()
    if p:
        return {"id": p["id"], "new": False}
    cmd = (f"ctdi-backup.sh: sync the encrypted Borg repository on the USB stick to {target or 'BACKUP_TARGET'} "
           f"for the next {GATE_DAYS} days")
    r = governance.create_approval(PATTERN, cmd, kind="backup-sync",
                                   requester=requester or f"operator:{getpass.getuser()}",
                                   reasoning="weekly off-box sync gate (lapse = sync held, nothing deleted)",
                                   ttl_seconds=governance.approval_ttl("backup-sync"))
    try:   # the phone hears about it; a push failure never blocks the request
        from common import ntfy_push
        ntfy_push.send(topic="ops-health", title="Backup sync: weekly signature needed",
                       message=f"Nightly Borg archive is on the stick; the Proton sync is held until you sign: "
                               f"scripts/approve.sh allow {r['id']}"[:200], priority=4)
    except Exception:  # noqa: BLE001
        pass
    return {"id": r["id"], "new": True}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--days", type=float, default=GATE_DAYS)
    q = sub.add_parser("request"); q.add_argument("--target", default="")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        r = check(a.days)
        if not r:
            print("none")
            return 1
        print(f"ok {r['id']} {int(r['resolved_at'])}")
        return 0
    r = request(a.target)
    print(f"{'requested' if r['new'] else 'already pending'} {r['id']}; sign with: scripts/approve.sh allow {r['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
