"""2026-10-18: /console/login answered HTTP 500 (KeyError: 0) on every re-tap while a request was pending,
because a count was read as fetchone()[0]; rows from common.db.conn() are dict-shaped on Postgres.
Standing rule: in src/ (app code, not the one-off SQLite->Postgres migration scripts), never index a
db.conn() row by position."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAT = re.compile(r"\.fetch(one|all)\(\)\s*(\[\d+\]|\[0\]\[\d+\])")


def test_no_positional_row_indexing_in_app_code():
    hits = []
    for f in (ROOT / "src").rglob("*.py"):
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if PAT.search(line) and "sqlite3" not in line:
                hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert hits == [], "positional row indexing on db.conn() rows (KeyError: 0 on Postgres):\n" + "\n".join(hits)
