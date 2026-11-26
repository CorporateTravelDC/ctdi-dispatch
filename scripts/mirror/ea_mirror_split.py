#!/usr/bin/env python3
"""scripts/mirror/ea_mirror_split.py -- split a `journalctl -o json` stream into one rolling log per unit.

Reads journal JSON lines on stdin (only the fields the caller asked for), appends `<iso time> <message>` to
DIR/<unit>.log, and keeps the newest KEEP lines per file. Used by scripts/mirror/ea-mirror.sh; the files it
writes are redacted and leak-scanned before they are published.

  journalctl --user -o json --output-fields=_SYSTEMD_USER_UNIT,MESSAGE ... | ea_mirror_split.py DIR [KEEP]
"""
from __future__ import annotations

import collections
import datetime
import json
import re
import sys
from pathlib import Path

SAFE = re.compile(r"[^A-Za-z0-9@._-]")


def main(argv: list[str]) -> int:
    out = Path(argv[1])
    keep = int(argv[2]) if len(argv) > 2 else 3000
    out.mkdir(parents=True, exist_ok=True)
    new: dict[str, list[str]] = collections.defaultdict(list)
    for line in sys.stdin:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        unit = e.get("_SYSTEMD_USER_UNIT") or e.get("_SYSTEMD_UNIT") or e.get("SYSLOG_IDENTIFIER") or "other"
        msg = e.get("MESSAGE")
        if isinstance(msg, list):                     # journald gives non-UTF-8 messages as byte lists
            msg = bytes(x & 0xFF for x in msg).decode("utf-8", "replace")
        if not isinstance(msg, str):
            continue
        try:
            ts = datetime.datetime.fromtimestamp(int(e["__REALTIME_TIMESTAMP"]) / 1e6, datetime.timezone.utc)
            stamp = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            stamp = "?"
        new[SAFE.sub("_", unit)].append(f"{stamp} {msg.rstrip()}\n")
    for unit, lines in new.items():
        p = out / f"{unit}.log"
        old = p.read_text(errors="replace").splitlines(keepends=True) if p.exists() else []
        p.write_text("".join((old + lines)[-keep:]))
    print(f"ea-mirror: {sum(len(v) for v in new.values())} new journal line(s) in {len(new)} unit file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
