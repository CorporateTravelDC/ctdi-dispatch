"""
Demo archive recorder v2

Polling interval : INTERVAL seconds (default 300 = 5 min)
Compression      : zlib level 6 — ~95% savings on NOTAM JSON
Deduplication    : skip write when payload hash unchanged (DB-persisted,
                   survives recorder restarts)
Retention        : RETENTION days rolling window (default 56 = 8 weeks)
Seed target      : SEED_TARGET days before demo site reports "ready"
                   (default 14 = 2 weeks)

On first run after upgrade the migrate_legacy() function compresses all
existing uncompressed rows in-place and vacuums the DB. This is a one-time
cost (~60 s on a 2.8 GB legacy archive) and produces ~94% disk reduction.

Demo site playback: check `compressed` column; if 1, zlib.decompress(payload).
"""
import hashlib
import logging
import os
import sqlite3
import time
import zlib
from datetime import datetime, timezone, timedelta

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('demo.recorder')

DB          = '/var/lib/corporatetraveldc/demo.db'
API         = 'http://127.0.0.1:8000/api/v1'
INTERVAL    = int(os.environ.get('DEMO_RECORDER_INTERVAL',    '300'))
RETENTION   = int(os.environ.get('DEMO_RECORDER_RETENTION',   '56'))
SEED_TARGET = int(os.environ.get('DEMO_RECORDER_SEED_TARGET', '14'))

ENDPOINTS = [
    'tfr', 'weather', 'alerts', 'cps', 'notams',
    'amtrak', 'opsplan', 'route', 'brief',
]

_last_vacuum: float = 0.0  # epoch timestamp of last VACUUM


# ── Schema ────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint     TEXT    NOT NULL,
            captured_at  TEXT    NOT NULL,
            payload      BLOB    NOT NULL,
            payload_hash TEXT,
            compressed   INTEGER NOT NULL DEFAULT 1
        )''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ep_time ON snapshots(endpoint, captured_at)'
    )
    # Non-destructive upgrade: add columns if coming from v1 schema
    for col, defn in [
        ('payload_hash', 'TEXT'),
        ('compressed',   'INTEGER NOT NULL DEFAULT 0'),
    ]:
        try:
            conn.execute(f'ALTER TABLE snapshots ADD COLUMN {col} {defn}')
            log.info('schema upgrade: added column %s', col)
        except sqlite3.OperationalError:
            pass  # column already present
    conn.commit()


# ── One-time legacy migration ─────────────────────────────────────

def migrate_legacy(conn: sqlite3.Connection) -> None:
    """
    Compress all rows that were written uncompressed by v1.
    Runs at startup and is a no-op once all rows are compressed.
    On 2.8 GB of raw NOTAM text this typically takes ~60 seconds and
    shrinks the DB by ~94%.
    """
    global _last_vacuum
    n_total = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE compressed=0"
    ).fetchone()[0]
    if not n_total:
        return

    log.info('migrating %d legacy uncompressed rows — one-time cost, please wait…', n_total)
    done = 0
    rows = conn.execute(
        "SELECT id, payload FROM snapshots WHERE compressed=0"
    ).fetchall()
    for row_id, payload in rows:
        try:
            text = payload if isinstance(payload, str) else payload.decode('utf-8', errors='replace')
            h    = hashlib.sha256(text.encode()).hexdigest()
            blob = zlib.compress(text.encode(), level=6)
            conn.execute(
                'UPDATE snapshots SET payload=?, payload_hash=?, compressed=1 WHERE id=?',
                (blob, h, row_id)
            )
            done += 1
            if done % 500 == 0:
                conn.commit()
                log.info('  migration progress: %d / %d', done, n_total)
        except Exception as e:
            log.warning('compress legacy row %d: %s', row_id, e)

    conn.commit()
    conn.execute('VACUUM')
    _last_vacuum = time.time()
    size_mb = os.path.getsize(DB) / 1e6
    log.info('migration complete — %d rows compressed, vacuumed, DB now %.1f MB', done, size_mb)


# ── Deduplication ─────────────────────────────────────────────────

def last_hash(conn: sqlite3.Connection, ep: str) -> str | None:
    """Return sha256 of the most recent stored payload for this endpoint."""
    row = conn.execute(
        "SELECT payload_hash FROM snapshots "
        "WHERE endpoint=? AND payload_hash IS NOT NULL "
        "ORDER BY captured_at DESC LIMIT 1",
        (ep,)
    ).fetchone()
    return row[0] if row else None


# ── Record cycle ──────────────────────────────────────────────────

def record(conn: sqlite3.Connection) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    for ep in ENDPOINTS:
        try:
            r = requests.get(f'{API}/{ep}', timeout=15)
            if not r.ok:
                continue
            text = r.text
            h    = hashlib.sha256(text.encode()).hexdigest()
            if last_hash(conn, ep) == h:
                log.debug('skip %s — content unchanged', ep)
                continue
            blob = zlib.compress(text.encode(), level=6)
            conn.execute(
                'INSERT INTO snapshots(endpoint, captured_at, payload, payload_hash, compressed)'
                ' VALUES (?, ?, ?, ?, 1)',
                (ep, ts, blob, h)
            )
            log.info('recorded %-10s  raw=%5d KB  stored=%4d KB',
                     ep, len(text) // 1024, len(blob) // 1024)
        except Exception as e:
            log.warning('skip %s: %s', ep, e)
    conn.commit()


# ── Retention + VACUUM ────────────────────────────────────────────

def prune(conn: sqlite3.Connection) -> None:
    global _last_vacuum
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION)).isoformat()
    n = conn.execute(
        'DELETE FROM snapshots WHERE captured_at < ?', (cutoff,)
    ).rowcount
    conn.commit()
    if n:
        log.info('pruned %d snapshots older than %d days', n, RETENTION)

    # Vacuum at most once per day — reclaims pages freed by DELETE
    if time.time() - _last_vacuum > 86_400:
        conn.execute('VACUUM')
        _last_vacuum = time.time()
        log.info('vacuumed db — %.1f MB', os.path.getsize(DB) / 1e6)


# ── Seed status ───────────────────────────────────────────────────

def seed_status(conn: sqlite3.Connection) -> dict:
    """Return seed readiness dict (also used by /api/v1/demo/readiness)."""
    days  = conn.execute(
        "SELECT COUNT(DISTINCT DATE(captured_at)) FROM snapshots"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    oldest = (conn.execute("SELECT MIN(captured_at) FROM snapshots").fetchone()[0] or '')[:10]
    newest = (conn.execute("SELECT MAX(captured_at) FROM snapshots").fetchone()[0] or '')[:10]
    return {
        "seed_days":        days,
        "seed_target":      SEED_TARGET,
        "ready":            days >= SEED_TARGET,
        "total_snapshots":  total,
        "oldest":           oldest or None,
        "newest":           newest or None,
        "db_size_mb":       round(os.path.getsize(DB) / 1e6, 1),
    }


# ── Main ──────────────────────────────────────────────────────────

def main() -> None:
    conn = sqlite3.connect(DB, check_same_thread=False)
    init_db(conn)
    migrate_legacy(conn)          # no-op after first run
    st = seed_status(conn)
    log.info(
        'recorder v2 ready — interval=%ds  retention=%dd  '
        'seed=%d/%d days  ready=%s  db=%.1f MB',
        INTERVAL, RETENTION,
        st['seed_days'], SEED_TARGET, st['ready'], st['db_size_mb']
    )
    while True:
        record(conn)
        prune(conn)
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
