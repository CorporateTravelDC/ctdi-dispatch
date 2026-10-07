"""common.audit_chain -- verify the audit_log hash chain (2026-10-07).

Migration 0062 makes Postgres compute row_hash = sha256(prev_hash | event_time |
action | tier | token_prefix | remote_addr | detail) in a BEFORE INSERT
trigger. Until this module, nothing ever re-checked it, so "hash-chained" was a
property nobody verified (docs/AGENT_TRUST_MODEL.md §11).

verify() recomputes every chained row IN SQL (same expression, same text
casts, so the bytes match the trigger exactly) and reports:
  * mismatch -- a row whose stored row_hash does not recompute (edited row);
  * broken   -- a row whose prev_hash is not the previous row's row_hash
                (a deleted, inserted or reordered row).
Rows from before 0062 (row_hash NULL) are counted, not judged.

What this does NOT prove: a writer who can rewrite the table can also
recompute the whole chain. Detecting that needs an anchor kept outside the
database -- verify() returns the head hash so it can be recorded elsewhere
(the integrity sweep logs it; signed checkpoints are the planned anchor).

CLI: python3 -m common.audit_chain   (exit 0 intact, 1 broken)
"""
from __future__ import annotations

import json
import sys

_SQL = """
SELECT id, row_hash, prev_hash,
       encode(digest(COALESCE(prev_hash, '') || '|' ||
                     COALESCE(event_time::text, '') || '|' ||
                     action || '|' || tier || '|' ||
                     COALESCE(token_prefix, '') || '|' ||
                     COALESCE(remote_addr, '') || '|' ||
                     COALESCE(detail, ''), 'sha256'), 'hex') AS recomputed,
       LAG(row_hash) OVER (ORDER BY id) AS prev_row_hash
FROM audit_log
WHERE row_hash IS NOT NULL
ORDER BY id
"""


def verify() -> dict:
    from common import db_backend
    mismatch, broken, n, head, first = [], [], 0, None, True
    with db_backend.pg_conn() as c:
        legacy = c.execute("SELECT count(*) AS n FROM audit_log WHERE row_hash IS NULL").fetchone()["n"]
        for r in c.execute(_SQL).fetchall():
            n += 1
            if r["recomputed"] != r["row_hash"]:
                mismatch.append(r["id"])
            if not first and (r["prev_hash"] or "") != (r["prev_row_hash"] or ""):
                broken.append(r["id"])
            first = False
            head = r["row_hash"]
    return {"intact": not mismatch and not broken, "rows_checked": n, "legacy_unchained_rows": legacy,
            "mismatch_ids": mismatch[:50], "broken_link_ids": broken[:50], "head_hash": head}


if __name__ == "__main__":
    res = verify()
    print(json.dumps(res, indent=1))
    sys.exit(0 if res["intact"] else 1)
