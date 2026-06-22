#!/usr/bin/env python3
"""
apply_wpc_patch.py -- Apply WPC national forecast discussion support.

Run from the root of the ctdi-dispatch repo:
    python3 apply_wpc_patch.py

What it does:
    1. Backs up the three files it modifies (.bak suffix)
    2. Checks idempotency -- skips any step already applied
    3. Applies each change with a clear [OK] / [SKIP] / [FAIL] status line
    4. Rolls back all changes on any failure

Files modified:
    src/common/db.py        -- SCHEMA_V12 + wpc_discussion helpers
    src/ingest/nwws.py      -- _WPC_PRODUCTS dict, WPC parser, _on_msg branch
    src/web/main.py         -- init_db_v12() in startup, two new route handlers
"""

import os
import shutil
import sys

# ---------------------------------------------------------------------------
# Repo-relative paths
# ---------------------------------------------------------------------------
DB_PATH     = os.path.join("src", "common", "db.py")
NWWS_PATH   = os.path.join("src", "ingest", "nwws.py")
MAIN_PATH   = os.path.join("src", "web", "main.py")

TARGETS = [DB_PATH, NWWS_PATH, MAIN_PATH]

# ---------------------------------------------------------------------------
# Idempotency sentinels -- if these strings are already in the file, skip
# ---------------------------------------------------------------------------
DB_SENTINEL   = "SCHEMA_V12"
NWWS_SENTINEL = "_WPC_PRODUCTS"
MAIN_SENTINEL = "/api/v1/wx/discussion"

# ===========================================================================
# Patch content
# ===========================================================================

DB_ADDITION = '''

# -- Schema V12 -- WPC national forecast discussions --------------------------

SCHEMA_V12 = """
CREATE TABLE IF NOT EXISTS wpc_discussions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    awips_id        TEXT NOT NULL,
    product_label   TEXT NOT NULL,
    issued_at       REAL NOT NULL,
    fetched_at      REAL DEFAULT (unixepoch()),
    body            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wpc_discussions_awips
    ON wpc_discussions(awips_id, issued_at DESC);
"""


def init_db_v12() -> None:
    """Apply v12 schema -- WPC national forecast discussions."""
    with conn() as c:
        c.executescript(SCHEMA_V12)


def upsert_wpc_discussion(awips_id: str, product_label: str,
                           issued_at: float, body: str) -> None:
    with conn() as c:
        c.execute("""
            INSERT INTO wpc_discussions (awips_id, product_label, issued_at, body)
            VALUES (?, ?, ?, ?)
        """, (awips_id, product_label, issued_at, body))


def get_latest_wpc_discussion(awips_id: str = "FXUS02") -> dict | None:
    with conn() as c:
        row = c.execute("""
            SELECT * FROM wpc_discussions
            WHERE awips_id = ?
            ORDER BY issued_at DESC LIMIT 1
        """, (awips_id,)).fetchone()
        return dict(row) if row else None


def get_latest_wpc_discussions() -> list[dict]:
    with conn() as c:
        rows = c.execute("""
            SELECT w.*
            FROM wpc_discussions w
            INNER JOIN (
                SELECT awips_id, MAX(issued_at) AS max_issued
                FROM wpc_discussions
                GROUP BY awips_id
            ) latest ON w.awips_id = latest.awips_id
                     AND w.issued_at = latest.max_issued
            ORDER BY w.awips_id
        """).fetchall()
        return [dict(r) for r in rows]


def prune_wpc_discussions(keep_per_product: int = 10) -> int:
    with conn() as c:
        rows = c.execute(
            "SELECT DISTINCT awips_id FROM wpc_discussions"
        ).fetchall()
        deleted = 0
        for row in rows:
            awips = row[0]
            cur = c.execute("""
                DELETE FROM wpc_discussions
                WHERE awips_id = ?
                  AND id NOT IN (
                      SELECT id FROM wpc_discussions
                      WHERE awips_id = ?
                      ORDER BY issued_at DESC
                      LIMIT ?
                  )
            """, (awips, awips, keep_per_product))
            deleted += cur.rowcount
        return deleted
'''

# ---------------------------------------------------------------------------

NWWS_WPC_PRODUCTS = '''

# WPC national discussion products -- handled via KWNO branch in _on_msg()
_WPC_PRODUCTS: dict[str, str] = {
    "FXUS02": "Short Range Forecast Discussion",
    "FXUS06": "Medium Range Forecast Discussion",
    "FXUS07": "Extended Forecast Discussion",
    "FXUS05": "Short Range QPF Discussion",
}

_WPC_TIME_RE = re.compile(
    r"(\\d{3,4})\\s+(AM|PM)\\s+([A-Z]{2,4})\\s+\\w+\\s+(\\w+)\\s+(\\d{1,2})\\s+(\\d{4})",
    re.MULTILINE,
)

_WPC_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5,  "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_TZ_OFFSETS: dict[str, int] = {
    "EST": -5, "EDT": -4,
    "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7,
    "UTC":  0, "Z":    0,
}

'''

NWWS_PARSE_WPC = '''

def _parse_wpc_issuance(body: str) -> float:
    """Parse WPC product header issuance time to unix epoch. Falls back to now."""
    m = _WPC_TIME_RE.search(body[:500])
    if not m:
        return time.time()
    try:
        hhmm_raw, ampm, tz_str, mon_str, day_str, year_str = m.groups()
        hhmm = hhmm_raw.zfill(4)
        hour = int(hhmm[:2])
        minute = int(hhmm[2:])
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        month = _WPC_MONTH_MAP.get(mon_str.upper(), 0)
        day   = int(day_str)
        year  = int(year_str)
        if month == 0:
            return time.time()
        import calendar
        utc_offset = _TZ_OFFSETS.get(tz_str.upper(), -4)
        local_epoch = calendar.timegm((year, month, day, hour, minute, 0, 0, 0, 0))
        return float(local_epoch - utc_offset * 3600)
    except Exception:
        return time.time()


def parse_wpc_product(awips_id: str, body: str) -> dict | None:
    """
    Parse a WPC national discussion product into upsert_wpc_discussion kwargs.
    Returns None for unknown AWIPS IDs.
    """
    label = _WPC_PRODUCTS.get(awips_id.upper())
    if not label or not body:
        return None
    return {
        "awips_id":      awips_id.upper(),
        "product_label": label,
        "issued_at":     _parse_wpc_issuance(body),
        "body":          body[:8000],
    }

'''

# Exact existing _on_msg text to replace (must match the file exactly)
NWWS_OLD_ON_MSG = \
'''        def _on_msg(self, msg):
            if msg["mucnick"] == cfg.nick:
                return
            x = msg.xml.find("{nwws-oi}x")
            if x is None:
                return
            awips = x.get("awipsid", "") or x.get("ttaaii", "")
            wfo = x.get("cccc", "")
            if cfg.wfo_filter and wfo not in cfg.wfo_filter:
                return
            body = (x.text or "").strip()
            try:
                for kw in parse_product(awips, wfo, body):
                    db.upsert_nws_alert(**kw)
            except Exception as e:
                log.error("NWWS product handler error (%s %s): %s", awips, wfo, e)'''

NWWS_NEW_ON_MSG = \
'''        def _on_msg(self, msg):
            if msg["mucnick"] == cfg.nick:
                return
            x = msg.xml.find("{nwws-oi}x")
            if x is None:
                return
            awips = x.get("awipsid", "") or x.get("ttaaii", "")
            wfo   = x.get("cccc", "")
            body  = (x.text or "").strip()

            # WPC national products (source KWNO) -- bypass local WFO filter
            if wfo == "KWNO":
                kw = parse_wpc_product(awips, body)
                if kw:
                    try:
                        db.upsert_wpc_discussion(**kw)
                        log.info("NWWS WPC: %s (%s) issued %.0f",
                                 kw["awips_id"], kw["product_label"], kw["issued_at"])
                    except Exception as e:
                        log.error("NWWS WPC handler error (%s): %s", awips, e)
                return

            # Local WFO products
            if cfg.wfo_filter and wfo not in cfg.wfo_filter:
                return
            try:
                for kw in parse_product(awips, wfo, body):
                    db.upsert_nws_alert(**kw)
            except Exception as e:
                log.error("NWWS product handler error (%s %s): %s", awips, wfo, e)'''

# ---------------------------------------------------------------------------

MAIN_INIT_OLD = "    db.init_db_v11()\n"
MAIN_INIT_NEW = "    db.init_db_v11()\n    db.init_db_v12()\n"

# Anchor: insert new routes after the closing of get_alerts()
MAIN_ALERTS_ANCHOR = \
'''    return JSONResponse({"alerts": result, "count": len(result)})'''

MAIN_WPC_ROUTES = '''
    return JSONResponse({"alerts": result, "count": len(result)})


@app.get("/api/v1/wx/discussion")
async def get_wx_discussion(
    product: Optional[str] = Query(
        default=None,
        description="AWIPS ID: FXUS02 (short-range default), FXUS06 (medium), "
                    "FXUS07 (extended), FXUS05 (QPF). Omit for all products."
    )
) -> JSONResponse:
    """Latest WPC national forecast discussion(s) -- Tier 0."""
    if product:
        awips_id = product.upper()
        row = db.get_latest_wpc_discussion(awips_id)
        if not row:
            return JSONResponse({
                "awips_id": awips_id, "product_label": None,
                "issued_at": None, "fetched_at": None,
                "body": None, "body_excerpt": None, "available": False,
            })
        return JSONResponse({
            "awips_id":      row["awips_id"],
            "product_label": row["product_label"],
            "issued_at":     row["issued_at"],
            "fetched_at":    row["fetched_at"],
            "body":          row["body"],
            "body_excerpt":  (row["body"] or "")[:300],
            "available":     True,
        })
    else:
        rows = db.get_latest_wpc_discussions()
        if not rows:
            return JSONResponse({"discussions": [], "available": False})
        return JSONResponse({
            "discussions": [
                {
                    "awips_id":      r["awips_id"],
                    "product_label": r["product_label"],
                    "issued_at":     r["issued_at"],
                    "fetched_at":    r["fetched_at"],
                    "body_excerpt":  (r["body"] or "")[:300],
                }
                for r in rows
            ],
            "available": True,
        })


@app.get("/api/v1/wx/discussion/{awips_id}")
async def get_wx_discussion_by_id(awips_id: str) -> JSONResponse:
    """Path-form convenience: /api/v1/wx/discussion/FXUS02 -- Tier 0."""
    row = db.get_latest_wpc_discussion(awips_id.upper())
    if not row:
        raise HTTPException(status_code=404,
                            detail=f"No discussion found for {awips_id.upper()}")
    return JSONResponse({
        "awips_id":      row["awips_id"],
        "product_label": row["product_label"],
        "issued_at":     row["issued_at"],
        "fetched_at":    row["fetched_at"],
        "body":          row["body"],
        "body_excerpt":  (row["body"] or "")[:300],
        "available":     True,
    })'''


# ===========================================================================
# Helpers
# ===========================================================================

def status(tag, msg):
    print(f"[{tag}] {msg}")


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def backup(path):
    bak = path + ".bak"
    shutil.copy2(path, bak)
    status("OK", f"Backup: {bak}")


def check_repo_root():
    for p in TARGETS:
        if not os.path.exists(p):
            print(f"[FAIL] Not found: {p}")
            print("       Run this script from the root of the ctdi-dispatch repo.")
            sys.exit(1)


# ===========================================================================
# Apply steps
# ===========================================================================

def patch_db(content):
    if DB_SENTINEL in content:
        status("SKIP", "db.py -- SCHEMA_V12 already present")
        return content, False
    content = content + DB_ADDITION
    status("OK", "db.py -- SCHEMA_V12 + helpers appended")
    return content, True


def patch_nwws_wpc_products(content):
    if NWWS_SENTINEL in content:
        status("SKIP", "nwws.py -- _WPC_PRODUCTS already present")
        return content, False
    # Insert after _TEXT_PRODUCTS block, before "# VTEC regex:"
    anchor = "# VTEC regex:"
    if anchor not in content:
        raise RuntimeError("nwws.py -- anchor '# VTEC regex:' not found")
    content = content.replace(anchor, NWWS_WPC_PRODUCTS + anchor, 1)
    status("OK", "nwws.py -- _WPC_PRODUCTS dict + time helpers inserted")
    return content, True


def patch_nwws_parse_wpc(content):
    if "def parse_wpc_product(" in content:
        status("SKIP", "nwws.py -- parse_wpc_product() already present")
        return content, False
    # Insert before "async def run("
    anchor = "\nasync def run("
    if anchor not in content:
        raise RuntimeError("nwws.py -- anchor 'async def run(' not found")
    content = content.replace(anchor, NWWS_PARSE_WPC + anchor, 1)
    status("OK", "nwws.py -- _parse_wpc_issuance() + parse_wpc_product() inserted")
    return content, True


def patch_nwws_on_msg(content):
    if "wfo == \"KWNO\"" in content:
        status("SKIP", "nwws.py -- _on_msg KWNO branch already present")
        return content, False
    if NWWS_OLD_ON_MSG not in content:
        raise RuntimeError(
            "nwws.py -- original _on_msg() text not found; "
            "file may have changed since patches were generated"
        )
    content = content.replace(NWWS_OLD_ON_MSG, NWWS_NEW_ON_MSG, 1)
    status("OK", "nwws.py -- _on_msg() replaced with KWNO branch")
    return content, True


def patch_main_init(content):
    if "init_db_v12" in content:
        status("SKIP", "main.py -- init_db_v12() already present")
        return content, False
    if MAIN_INIT_OLD not in content:
        raise RuntimeError("main.py -- 'db.init_db_v11()' line not found in startup()")
    content = content.replace(MAIN_INIT_OLD, MAIN_INIT_NEW, 1)
    status("OK", "main.py -- db.init_db_v12() added to startup()")
    return content, True


def patch_main_routes(content):
    if MAIN_SENTINEL in content:
        status("SKIP", "main.py -- WPC routes already present")
        return content, False
    if MAIN_ALERTS_ANCHOR not in content:
        raise RuntimeError(
            "main.py -- get_alerts() closing line not found; "
            "file may have changed since patches were generated"
        )
    # Replace the anchor with anchor + new routes
    content = content.replace(MAIN_ALERTS_ANCHOR, MAIN_WPC_ROUTES, 1)
    status("OK", "main.py -- /api/v1/wx/discussion routes added")
    return content, True


# ===========================================================================
# Main
# ===========================================================================

def main():
    print("=" * 60)
    print("apply_wpc_patch.py -- WPC discussion support")
    print("=" * 60)

    check_repo_root()

    # Read all three files up front
    db_src   = read(DB_PATH)
    nwws_src = read(NWWS_PATH)
    main_src = read(MAIN_PATH)

    originals = {
        DB_PATH:   db_src,
        NWWS_PATH: nwws_src,
        MAIN_PATH: main_src,
    }

    try:
        # -- Back up --------------------------------------------------------
        for path in TARGETS:
            backup(path)

        # -- db.py ----------------------------------------------------------
        print()
        print("-- src/common/db.py")
        db_src, _ = patch_db(db_src)

        # -- nwws.py --------------------------------------------------------
        print()
        print("-- src/ingest/nwws.py")
        nwws_src, _ = patch_nwws_wpc_products(nwws_src)
        nwws_src, _ = patch_nwws_parse_wpc(nwws_src)
        nwws_src, _ = patch_nwws_on_msg(nwws_src)

        # -- main.py --------------------------------------------------------
        print()
        print("-- src/web/main.py")
        main_src, _ = patch_main_init(main_src)
        main_src, _ = patch_main_routes(main_src)

        # -- Write ----------------------------------------------------------
        print()
        write(DB_PATH,   db_src)
        status("OK", f"Written: {DB_PATH}")
        write(NWWS_PATH, nwws_src)
        status("OK", f"Written: {NWWS_PATH}")
        write(MAIN_PATH, main_src)
        status("OK", f"Written: {MAIN_PATH}")

    except Exception as e:
        print()
        status("FAIL", str(e))
        print()
        print("Rolling back all changes...")
        for path, original in originals.items():
            write(path, original)
            status("OK", f"Restored: {path}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("[OK] All patches applied.")
    print()
    print("Next steps:")
    print("  1. Review .bak files if you want to diff before/after")
    print("  2. Run: bash build-images.sh")
    print("  3. Restart containers: systemctl --user restart")
    print("     corporatetraveldc-web.service")
    print("     corporatetraveldc-ingest.service")
    print("=" * 60)


if __name__ == "__main__":
    main()
