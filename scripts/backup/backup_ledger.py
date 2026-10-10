#!/usr/bin/env python3
"""scripts/backup/backup_ledger.py -- the backup PRIVATE LEDGER (2026-10-09).

One JSON line per event (backup, cui-stamp, sync, restore-test), hash-chained
like the audit archive: entry_hash = sha256(prev_hash | canonical entry without
entry_hash). The ledger file lives on the box (default
~/.local/state/ctdc-backup/ledger.jsonl, 0600) and a copy rides inside every
encrypted backup archive, so an off-box copy can be checked against the on-box
head and vice versa.

CUI stamp (#): the LADD table (CUI) never leaves the box by default. Each backup
still records it with a "#" stamp -- the SHA-256 of its local-only dump, its row
count and where that local copy is -- so the ledger proves what LADD state
existed at backup time without shipping the data.

  append  KIND --json '{...}'      add an entry, print its entry_hash
  file    PATH ...                 print {"path","bytes","sha256"} per file as JSON
  verify                           check the whole chain; exit 1 on a break
  head                             print the last entry_hash
  last    KIND                     print the last entry of KIND as JSON
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

LEDGER = Path(os.environ.get("BACKUP_LEDGER", str(Path.home() / ".local/state/ctdc-backup/ledger.jsonl")))
KINDS = ("backup", "cui-stamp", "sync", "restore-test", "note")


def canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def entries(path: Path = LEDGER) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def entry_hash(prev: str, body: dict) -> str:
    return hashlib.sha256(prev.encode() + b"|" + canonical(body)).hexdigest()


def append(kind: str, data: dict, path: Path = LEDGER, now: float | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    prior = entries(path)
    prev = prior[-1]["entry_hash"] if prior else ""
    body = {"kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
            "seq": len(prior) + 1, "prev_hash": prev, "data": data}
    e = {**body, "entry_hash": entry_hash(prev, body)}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def verify(path: Path = LEDGER) -> tuple[bool, str]:
    prev = ""
    for i, e in enumerate(entries(path), 1):
        body = {k: v for k, v in e.items() if k != "entry_hash"}
        if e.get("prev_hash") != prev:
            return False, f"entry {i}: prev_hash does not link"
        if entry_hash(prev, body) != e.get("entry_hash"):
            return False, f"entry {i}: entry_hash does not recompute"
        if e.get("seq") != i:
            return False, f"entry {i}: sequence gap"
        prev = e["entry_hash"]
    return True, prev


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append"); a.add_argument("kind"); a.add_argument("--json", required=True)
    f = sub.add_parser("file"); f.add_argument("paths", nargs="+")
    sub.add_parser("verify"); sub.add_parser("head")
    l = sub.add_parser("last"); l.add_argument("kind")
    x = ap.parse_args(argv)
    if x.cmd == "append":
        print(append(x.kind, json.loads(x.json))["entry_hash"])
    elif x.cmd == "file":
        print(json.dumps([{"path": p, "bytes": Path(p).stat().st_size, "sha256": sha256_file(Path(p))} for p in x.paths]))
    elif x.cmd == "verify":
        ok, msg = verify()
        print(("ledger intact; head " if ok else "LEDGER BROKEN: ") + msg)
        return 0 if ok else 1
    elif x.cmd == "head":
        es = entries()
        print(es[-1]["entry_hash"] if es else "")
    elif x.cmd == "last":
        es = [e for e in entries() if e["kind"] == x.kind]
        print(json.dumps(es[-1]) if es else "{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
