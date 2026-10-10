"""common/import_ledger.py -- hash-chained private ledger of reference-data imports (2026-10-09).

Every LADD (CUI) filter/remove-file import and every FAA registry download
appends one entry: the source files (basename, bytes, SHA-256) and the
resulting row counts. Hashes, basenames and counts only, never an identifier.
Schema and rationale: pg_schema/0074_reference_import_ledger.sql. The backup
"#" stamp records head() so an off-box archive pins the import history too.

    stamp(kind, sources=[...], counts={...})   append; returns the entry
    file_meta(path) / bytes_meta(name, data)   {"name","bytes","sha256"}
    verify() -> (ok, head_or_reason)
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

KINDS = ("ladd-import", "faa-registry", "baseline")   # baseline: pre-ledger state, counts only

_DDL = """
CREATE TABLE IF NOT EXISTS reference_import_ledger (
    seq         INTEGER PRIMARY KEY,
    at          TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    prev_hash   TEXT    NOT NULL UNIQUE,
    entry_hash  TEXT    NOT NULL UNIQUE
)"""


def _canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _entry_hash(prev: str, seq: int, at: str, kind: str, body: str) -> str:
    payload = _canonical({"seq": seq, "at": at, "kind": kind, "body": body})
    return hashlib.sha256(prev.encode() + b"|" + payload.encode()).hexdigest()


def bytes_meta(name: str, data: bytes) -> dict:
    return {"name": Path(name).name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def file_meta(path) -> dict:
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"name": p.name, "bytes": p.stat().st_size, "sha256": h.hexdigest()}   # basename only


def _ensure(c) -> None:
    c.execute(_DDL)


def stamp(kind: str, sources: list[dict], counts: dict, note: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    from common import db, db_backend
    body = _canonical({"sources": sources, "counts": counts, **({"note": note} if note else {})})
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.conn() as c:
        _ensure(c)
        if db_backend.backend() == "postgres":
            c.execute("SELECT pg_advisory_xact_lock(hashtext('reference_import_ledger'))")
        row = c.execute("SELECT seq, entry_hash FROM reference_import_ledger ORDER BY seq DESC LIMIT 1").fetchone()
        seq = (row["seq"] + 1) if row else 1
        prev = row["entry_hash"] if row else ""
        eh = _entry_hash(prev, seq, at, kind, body)
        c.execute("INSERT INTO reference_import_ledger (seq, at, kind, body, prev_hash, entry_hash) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (seq, at, kind, body, prev, eh))
    return {"seq": seq, "at": at, "kind": kind, "body": json.loads(body), "prev_hash": prev, "entry_hash": eh}


def entries() -> list[dict]:
    from common import db
    with db.conn() as c:
        _ensure(c)
        return [dict(r) for r in c.execute(
            "SELECT seq, at, kind, body, prev_hash, entry_hash FROM reference_import_ledger ORDER BY seq").fetchall()]


def verify() -> tuple[bool, str]:
    prev = ""
    for i, e in enumerate(entries(), 1):
        if e["seq"] != i:
            return False, f"seq {i}: gap"
        if e["prev_hash"] != prev:
            return False, f"seq {i}: prev_hash does not link"
        if _entry_hash(prev, e["seq"], e["at"], e["kind"], e["body"]) != e["entry_hash"]:
            return False, f"seq {i}: entry_hash does not recompute"
        prev = e["entry_hash"]
    return True, prev


def head() -> dict:
    es = entries()
    return {"seq": es[-1]["seq"], "entry_hash": es[-1]["entry_hash"]} if es else {"seq": 0, "entry_hash": ""}


if __name__ == "__main__":
    import sys
    ok, msg = verify()
    print(("import ledger intact; " if ok else "IMPORT LEDGER BROKEN: ") + json.dumps({**head(), "detail": msg}))
    sys.exit(0 if ok else 1)
