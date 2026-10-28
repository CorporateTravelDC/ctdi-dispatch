#!/usr/bin/env python3
"""scripts/pg-ro-query.py -- an agent's read-only query against production (2026-10-26).

Connects as the read-only role (scripts/pg-ro-role.py) with the names from the
agent secrets subset, inside a READ ONLY transaction with a 30 s statement
timeout, and prints tab-separated rows with a header (at most --limit rows).
The role can only SELECT from tables the deny list leaves readable; this
wrapper adds the read-only transaction and refuses anything that is not a
single SELECT / WITH / EXPLAIN / SHOW / TABLE / VALUES statement.

    scripts/with-dispatch-env.sh scripts/pg-ro-query.py "SELECT count(*) FROM metar_history"
    echo "SELECT ..." | scripts/with-dispatch-env.sh scripts/pg-ro-query.py -

Environment: DISPATCH_PG_RO_USER, DISPATCH_PG_RO_PASSWORD, DISPATCH_PG_DB,
DISPATCH_PG_PORT (default 5432); the host is 127.0.0.1 (the container's
published port) unless DISPATCH_PG_RO_HOST says otherwise.
Needs psycopg 3 (Fedora: python3-psycopg3).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

FIRST_WORD = re.compile(r"^\s*(select|with|explain|show|table|values)\b", re.I)


def statement_ok(sql: str) -> bool:
    body = sql.strip().rstrip(";").strip()
    return bool(body) and ";" not in body and bool(FIRST_WORD.match(body))


def cell(v) -> str:
    return "\\N" if v is None else str(v).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="read-only query as dispatch_ro")
    ap.add_argument("sql", help="the statement, or - to read it from stdin")
    ap.add_argument("--limit", type=int, default=1000)
    a = ap.parse_args(argv)
    sql = sys.stdin.read() if a.sql == "-" else a.sql
    if not statement_ok(sql):
        print("refused: one SELECT / WITH / EXPLAIN / SHOW / TABLE / VALUES statement only", file=sys.stderr)
        return 2
    missing = [k for k in ("DISPATCH_PG_RO_USER", "DISPATCH_PG_RO_PASSWORD", "DISPATCH_PG_DB") if not os.environ.get(k)]
    if missing:
        print(f"missing {', '.join(missing)} (run through scripts/with-dispatch-env.sh; the read-only role "
              "may not exist yet: the operator runs scripts/pg-ro-role.py)", file=sys.stderr)
        return 2
    try:
        import psycopg
    except ImportError:
        print("psycopg 3 is not installed (operator: sudo dnf install python3-psycopg3)", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(host=os.environ.get("DISPATCH_PG_RO_HOST", "127.0.0.1"),
                             port=os.environ.get("DISPATCH_PG_PORT", "5432"),
                             dbname=os.environ["DISPATCH_PG_DB"], user=os.environ["DISPATCH_PG_RO_USER"],
                             password=os.environ["DISPATCH_PG_RO_PASSWORD"], connect_timeout=10,
                             application_name="pg-ro-query") as conn:
            conn.read_only = True
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '30s'")
                cur = conn.execute(sql.strip().rstrip(";"))
                if cur.description is None:
                    return 0
                print("\t".join(d.name for d in cur.description))
                rows = cur.fetchmany(a.limit + 1)
                for r in rows[: a.limit]:
                    print("\t".join(cell(v) for v in r))
                if len(rows) > a.limit:
                    print(f"(truncated at {a.limit} rows; use --limit or narrow the query)", file=sys.stderr)
    except psycopg.Error as e:
        print(f"query failed: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"[:300], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
