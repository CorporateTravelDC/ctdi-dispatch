#!/usr/bin/env python3
"""scripts/backup/backup_freshness.py -- fail if backups are stale (2026-10-11).

Reads the private backup ledger. Exits 1 (so the unit fails and pages via
OnFailure) when:
  * the last backup entry is older than BACKUP_MAX_AGE_H (default 36 h);
  * BACKUP_TARGET is set and the last successful sync is older than that;
  * BACKUP_TARGET is NOT set while BACKUP_REQUIRE_OFFBOX is 1 (default): on-box only
    is a standing failure, not a quiet state (2026-10-12 docs-parity audit);
  * any agent's home snapshot is older than that (default: every ctdc-agent-*
    account, minus CTDC_SNAPSHOT_EXCLUDE -- the same set the snapshot unit takes);
  * the last restore-test is older than BACKUP_MAX_RESTORE_TEST_AGE_D (default 8 days) or failed;
  * the ledger chain does not verify.
"""
from __future__ import annotations

import calendar
import importlib.util
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = Path("/etc/corporatetraveldc/dispatch.env")


def setting(key: str, default: str) -> str:
    v = os.environ.get(key)
    if v:
        return v
    try:
        for line in ENV.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip() or default
    except OSError:
        pass
    return default


def _ledger():
    spec = importlib.util.spec_from_file_location("bl", HERE / "backup_ledger.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def age_h(entry: dict | None) -> float:
    if not entry:
        return float("inf")
    return (time.time() - calendar.timegm(time.strptime(entry["at"], "%Y-%m-%dT%H:%M:%SZ"))) / 3600


def _snapshot_agents() -> list[str]:
    explicit = setting("CTDC_SNAPSHOT_AGENTS", "").split()
    if explicit:
        return explicit
    import pwd
    skip = set(setting("CTDC_SNAPSHOT_EXCLUDE", "").split())
    return sorted(u.pw_name for u in pwd.getpwall() if u.pw_name.startswith("ctdc-agent-") and u.pw_name not in skip)


def check(now_entries=None) -> list[str]:
    bl = _ledger()
    es = bl.entries() if now_entries is None else now_entries
    last = lambda kind, pred=lambda e: True: next((e for e in reversed(es) if e["kind"] == kind and pred(e)), None)
    problems = []
    max_h = float(setting("BACKUP_MAX_AGE_H", "36"))
    if age_h(last("backup")) > max_h:
        problems.append(f"no backup in {max_h:.0f} h")
    if setting("BACKUP_TARGET", "") and age_h(last("sync", lambda e: e["data"].get("ok"))) > max_h:
        problems.append(f"no successful off-box sync in {max_h:.0f} h")
    elif not setting("BACKUP_TARGET", "") and setting("BACKUP_REQUIRE_OFFBOX", "1") == "1":
        problems.append("no off-box target configured: backups are on-box only (set BACKUP_TARGET, "
                        "or BACKUP_REQUIRE_OFFBOX=0 to accept that deliberately)")
    rt = last("restore-test")
    if age_h(rt) > 24 * float(setting("BACKUP_MAX_RESTORE_TEST_AGE_D", "8")) or (rt and not rt["data"].get("ok")):
        problems.append("restore test stale or failed")
    # 2026-10-11: agent home snapshots (root-run, 03:20 UTC) must be fresh too.
    snap = Path("/var/lib/corporatetraveldc/agent-snapshots")
    # 2026-10-12: was a one-name default (codex) -- 1 of 6 accounts checked. Now every
    # ctdc-agent-* account unless CTDC_SNAPSHOT_AGENTS names an explicit list.
    for a in _snapshot_agents():
        newest = max((p.stat().st_mtime for p in (snap / a).glob(f"{a}-*.tar.zst")), default=0) if (snap / a).is_dir() else 0
        if now_entries is None and (time.time() - newest) / 3600 > max_h:
            problems.append(f"agent snapshot for {a} older than {max_h:.0f} h")
    if now_entries is None:
        ok, msg = bl.verify()
        if not ok:
            problems.append("ledger chain broken: " + msg)
    return problems


if __name__ == "__main__":
    p = check()
    print("backup-freshness: " + ("OK" if not p else "STALE -- " + "; ".join(p)))
    sys.exit(1 if p else 0)
