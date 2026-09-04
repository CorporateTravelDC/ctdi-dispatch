#!/usr/bin/env python3
"""scripts/scrub-demo-source.py

The one component that both touches live data and writes to the demo side
(docs/DEMO_DATA_ISOLATION_PLAN_2026-08-13.md, Phase 1). Reads staged
snapshot payloads from demo.db (private live-side staging archive) and
brief_archive rows directly from the live production DB (read-only both),
passes every payload/row through src/demo/scrub_rules.py's substitute +
verify pass, and appends survivors into the sovereign demo-source file --
a new SQLite file in a new top-level directory, not a subdirectory of the
live tree.

Copy semantics are extract-allowlist-only: exactly two content tables
(snapshots, brief_archive) plus a meta table this script maintains itself.
Never copies-then-deletes -- a missed DELETE ships data, a missed INSERT
ships nothing; append-only is the fail-safe direction.

Fail-closed: any row that fails verify_scrubbed() is DROPPED, not
promoted with a warning. Never guess; a gap in demo history is fine, a
leak is not.

Usage:
    scripts/scrub-demo-source.py --backfill [--days N]
    scripts/scrub-demo-source.py --refresh
"""
import argparse
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR / "src"))

from demo.scrub_rules import scrub_text, verify_scrubbed, find_ladd_violations  # noqa: E402

LIVE_DB = "/var/lib/corporatetraveldc/corporatetraveldc.db"
STAGING_DB = "/var/lib/corporatetraveldc/demo.db"
SOVEREIGN_DIR = pathlib.Path("/var/lib/corporatetraveldc-demo-source")
SOVEREIGN_DB = SOVEREIGN_DIR / "demo-source.db"

BACKFILL_DEFAULT_DAYS = 28


def verify_self() -> None:
    """Self-verifying pattern _verify_before_inference() already applies
    in src/common/llm.py before any inference -- a privileged live-DB
    reader that feeds a public surface is precisely the kind of process
    that discipline exists for. Refuses to proceed on failure."""
    result = subprocess.run(
        [str(REPO_DIR / "scripts" / "verify-manifest.sh")],
        cwd=str(REPO_DIR), capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("XX INTEGRITY CHECK FAILED -- refusing to run scrub-demo-source.py", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(5)


def init_sovereign_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint     TEXT    NOT NULL,
            captured_at  TEXT    NOT NULL,
            payload      BLOB    NOT NULL,
            payload_hash TEXT,
            compressed   INTEGER NOT NULL DEFAULT 1,
            synthetic    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ep_time ON snapshots(endpoint, captured_at);

        CREATE TABLE IF NOT EXISTS brief_archive (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            brief_type   TEXT NOT NULL DEFAULT 'ops',
            content      TEXT NOT NULL,
            source       TEXT NOT NULL DEFAULT 'skill'
        );
        CREATE INDEX IF NOT EXISTS idx_brief_archive_ts ON brief_archive (generated_at DESC);

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # Non-destructive upgrade for a sovereign file created before the
    # 2026-08-14 replay-fill mechanism existed.
    try:
        conn.execute("ALTER TABLE snapshots ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already present
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def load_ladd_idents(live: sqlite3.Connection) -> frozenset[str]:
    """2026-08-31 (operator directive): LADD-listed tails/regs/idents can
    never reach a demo surface. Loaded once per run from the live DB
    (faa_ladd_aircraft, populated by scripts/import-ladd-filter.py) --
    never hardcoded here, since the list itself is CUI (SP-PRVCY)."""
    try:
        rows = live.execute("SELECT n_number FROM faa_ladd_aircraft").fetchall()
    except sqlite3.OperationalError:
        return frozenset()  # table doesn't exist yet -- nothing to check against
    return frozenset(r[0] for r in rows if r[0])


def scrub_payload_bytes(payload: bytes, compressed: bool,
                         ladd_idents: frozenset[str]) -> tuple[bytes | None, list[str]]:
    """Decompress if needed, decode, scrub, verify. Returns
    (recompressed_scrubbed_bytes_or_None, violations). None means DROP."""
    try:
        raw = zlib.decompress(payload) if compressed else payload
        text = raw.decode("utf-8")
    except Exception as e:
        return None, [f"decode failure: {e}"]

    scrubbed = scrub_text(text)
    violations = verify_scrubbed(scrubbed) + find_ladd_violations(scrubbed, ladd_idents)
    if violations:
        return None, violations
    return zlib.compress(scrubbed.encode("utf-8"), 6), []


def promote_snapshots(live_staging: sqlite3.Connection, sovereign: sqlite3.Connection,
                       since_iso: str, ladd_idents: frozenset[str],
                       ) -> tuple[int, int, Counter, str | None]:
    rows = live_staging.execute(
        "SELECT endpoint, captured_at, payload, payload_hash, compressed "
        "FROM snapshots WHERE captured_at > ? ORDER BY captured_at",
        (since_iso,),
    ).fetchall()

    promoted = 0
    dropped = 0
    drop_reasons: Counter = Counter()
    max_captured_at = None

    for endpoint, captured_at, payload, payload_hash, compressed in rows:
        clean, violations = scrub_payload_bytes(payload, bool(compressed), ladd_idents)
        if clean is None:
            dropped += 1
            for v in violations:
                # class only, never content -- e.g. "unrecognized email '...'"
                # -> "unrecognized email"
                drop_reasons[v.split(" '")[0].split(": ")[0]] += 1
            continue
        sovereign.execute(
            "INSERT INTO snapshots (endpoint, captured_at, payload, payload_hash, compressed) "
            "VALUES (?, ?, ?, ?, 1)",
            (endpoint, captured_at, clean, payload_hash),
        )
        promoted += 1
        if max_captured_at is None or captured_at > max_captured_at:
            max_captured_at = captured_at

    return promoted, dropped, drop_reasons, max_captured_at


def promote_briefs(live: sqlite3.Connection, sovereign: sqlite3.Connection,
                    since_iso: str, ladd_idents: frozenset[str],
                    ) -> tuple[int, int, Counter, str | None]:
    rows = live.execute(
        "SELECT generated_at, brief_type, content, source FROM brief_archive "
        "WHERE generated_at > ? ORDER BY generated_at",
        (since_iso,),
    ).fetchall()

    promoted = 0
    dropped = 0
    drop_reasons: Counter = Counter()
    max_generated_at = None

    for generated_at, brief_type, content, source in rows:
        scrubbed = scrub_text(content)
        violations = verify_scrubbed(scrubbed) + find_ladd_violations(scrubbed, ladd_idents)
        if violations:
            dropped += 1
            for v in violations:
                drop_reasons[v.split(" '")[0].split(": ")[0]] += 1
            continue
        sovereign.execute(
            "INSERT INTO brief_archive (generated_at, brief_type, content, source) "
            "VALUES (?, ?, ?, ?)",
            (generated_at, brief_type, scrubbed, source),
        )
        promoted += 1
        if max_generated_at is None or generated_at > max_generated_at:
            max_generated_at = generated_at

    return promoted, dropped, drop_reasons, max_generated_at


def replay_fill_gaps(sovereign: sqlite3.Connection, target_days: int = 14) -> Counter:
    """2026-08-14: fill playback gaps for endpoints without target_days of
    real (synthetic=0) coverage inside the trailing [window_end-target_days,
    window_end) window -- the window demo_api's SEED_TARGET-gated playback
    actually replays. A brand-new capture (e.g. knowledge-graph/osint/board,
    added the same day as this function) has real rows only from "now"
    forward, which is OUTSIDE that window until target_days pass; without
    this, demo_api._load_snapshot() returns nothing for that endpoint for
    the entire replay loop.

    Never fabricates content: every inserted row is a byte-for-byte copy of
    an already-scrubbed real payload (the oldest real row for that
    endpoint), re-dated into a missing calendar day and tagged synthetic=1.
    Real rows always win once they exist for a given day -- this only fills
    days with zero real coverage, never overwrites, and a later --refresh
    that promotes a genuine row for that day simply out-dates the
    synthetic one in _load_snapshot()'s ORDER BY captured_at DESC LIMIT 1
    (real and synthetic rows share the same table/query path; nothing
    downstream needs to know which is which except the X-Demo-Synthetic
    response header, for transparency)."""
    window_end_row = sovereign.execute(
        "SELECT value FROM meta WHERE key='window_end'"
    ).fetchone()
    if not window_end_row or not window_end_row[0]:
        return Counter()
    window_end = datetime.fromisoformat(window_end_row[0])
    window_start = window_end - timedelta(days=target_days)

    endpoints = [
        r[0] for r in sovereign.execute("SELECT DISTINCT endpoint FROM snapshots").fetchall()
    ]
    filled: Counter = Counter()

    for ep in endpoints:
        real_days = {
            r[0] for r in sovereign.execute(
                "SELECT DISTINCT DATE(captured_at) FROM snapshots "
                "WHERE endpoint=? AND synthetic=0 AND captured_at>=? AND captured_at<?",
                (ep, window_start.isoformat(), window_end.isoformat()),
            ).fetchall()
        }
        if len(real_days) >= target_days:
            continue

        src = sovereign.execute(
            "SELECT payload, payload_hash, compressed FROM snapshots "
            "WHERE endpoint=? AND synthetic=0 ORDER BY captured_at ASC LIMIT 1",
            (ep,),
        ).fetchone()
        if src is None:
            continue  # no real data at all yet for this endpoint -- nothing to replay from
        payload, payload_hash, compressed = src

        cursor = window_start
        while cursor < window_end:
            day_str = cursor.date().isoformat()
            if day_str not in real_days:
                ts = cursor.replace(hour=6, minute=0, second=0, microsecond=0).isoformat()
                sovereign.execute(
                    "INSERT INTO snapshots "
                    "(endpoint, captured_at, payload, payload_hash, compressed, synthetic) "
                    "VALUES (?, ?, ?, ?, ?, 1)",
                    (ep, ts, payload, payload_hash, compressed),
                )
                filled[ep] += 1
            cursor += timedelta(days=1)

    sovereign.commit()
    return filled


def main() -> None:
    # 2026-08-14: self-renice -- this is a long-running, CPU-heavy
    # background job (a --backfill run holds ~90%+ of a core sustained
    # for an hour-plus). Found live: an untended backfill run stacked
    # with a post-reboot SWIM catch-up burst starved a production
    # ops-brief generation into a 500/timeout and visibly slowed DNS, at
    # a load average of ~6 on this 4-core box. Raising niceness never
    # needs privilege and costs nothing when the box is otherwise idle --
    # it only yields sooner when something else actually wants the core.
    os.nice(15)

    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    parser.add_argument("--days", type=int, default=BACKFILL_DEFAULT_DAYS,
                         help=f"backfill window in days (default {BACKFILL_DEFAULT_DAYS})")
    args = parser.parse_args()

    verify_self()

    SOVEREIGN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    sovereign = sqlite3.connect(str(SOVEREIGN_DB))
    init_sovereign_schema(sovereign)

    if args.backfill:
        since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
        since_iso_snap = since_dt.strftime("%Y-%m-%dT%H:%M:%S")
        since_iso_brief = since_dt.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"[scrub-demo-source] BACKFILL mode -- trailing {args.days} days "
              f"(since {since_iso_snap})")
    else:
        window_end = get_meta(sovereign, "window_end")
        if not window_end:
            print("XX --refresh requested but no prior window_end in meta -- "
                  "run --backfill first.", file=sys.stderr)
            sys.exit(2)
        since_iso_snap = window_end
        since_iso_brief = window_end
        print(f"[scrub-demo-source] REFRESH mode -- since last window_end={window_end}")

    staging = sqlite3.connect(f"file:{STAGING_DB}?mode=ro", uri=True)
    live = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)

    ladd_idents = load_ladd_idents(live)
    print(f"[scrub-demo-source] LADD blocklist: {len(ladd_idents)} identifiers loaded")

    snap_promoted, snap_dropped, snap_reasons, snap_max = promote_snapshots(
        staging, sovereign, since_iso_snap, ladd_idents
    )
    brief_promoted, brief_dropped, brief_reasons, brief_max = promote_briefs(
        live, sovereign, since_iso_brief, ladd_idents
    )

    new_window_end = max(x for x in (snap_max, brief_max) if x is not None) \
        if (snap_max or brief_max) else since_iso_snap
    set_meta(sovereign, "window_end", new_window_end)
    set_meta(sovereign, "promoted_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    sovereign.commit()

    filled = replay_fill_gaps(sovereign, target_days=14)

    staging.close()
    live.close()
    sovereign.close()

    print("")
    print("[scrub-demo-source] === REPORT ===")
    print(f"  snapshots:     {snap_promoted} promoted, {snap_dropped} dropped")
    if snap_reasons:
        for reason, count in snap_reasons.most_common():
            print(f"    dropped ({reason}): {count}")
    print(f"  brief_archive: {brief_promoted} promoted, {brief_dropped} dropped")
    if brief_reasons:
        for reason, count in brief_reasons.most_common():
            print(f"    dropped ({reason}): {count}")
    if filled:
        print("  replay-fill (synthetic, real-data-derived, 14d target):")
        for ep, count in filled.most_common():
            print(f"    {ep}: {count} day(s) filled")
    print(f"  new window_end: {new_window_end}")
    print(f"  sovereign file: {SOVEREIGN_DB}")


if __name__ == "__main__":
    main()
