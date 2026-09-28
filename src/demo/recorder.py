"""
Demo archive recorder v3 -- Postgres-backed

Polling interval : INTERVAL seconds (default 300 = 5 min)
Compression      : zlib level 6 — ~95% savings on NOTAM JSON
Deduplication    : skip write when payload hash unchanged (DB-persisted,
                   survives recorder restarts)
Retention        : RETENTION days rolling window (default 364 = 52 weeks)
Seed target      : SEED_TARGET days before demo site reports "ready"
                   (default 14 = 2 weeks)

Retention tiers (for marketing / QBR snapshots):
  2w  =  14 days  — minimum seed / always-ready buffer
  8w  =  56 days  — bi-monthly
  12w =  84 days  — quarterly (3 months)
  24w = 168 days  — semi-annual (6 months)
  36w = 252 days  — 9 months
  52w = 364 days  — annual (12 months)

2026-09-20 Postgres cutover (operator directive -- see src/demo/db.py's
module docstring for the full rationale): this used to open
/var/lib/corporatetraveldc/demo.db directly via sqlite3. All storage now
goes through src/demo/db.py (demo_snapshots, pg_schema/0057_demo.sql).
Two things this drops rather than ports, both explained in db.py's own
docstring: VACUUM (Postgres autovacuum replaces it) and migrate_legacy()
(a one-time SQLite-only-format upgrade already a confirmed permanent
no-op -- 0 of 67,331 rows had compressed=0 at cutover time).

Demo site playback: check `compressed` column; if true, zlib.decompress(payload).
"""
import hashlib
import logging
import os
import time
import zlib
from datetime import datetime, timezone

import requests

from demo import db as demo_db

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('demo.recorder')

API         = os.environ.get('DEMO_RECORDER_API_BASE', 'http://100.x.x.x:8000/api/v1')
INTERVAL    = int(os.environ.get('DEMO_RECORDER_INTERVAL',    '300'))
RETENTION   = int(os.environ.get('DEMO_RECORDER_RETENTION',   '364'))
SEED_TARGET = int(os.environ.get('DEMO_RECORDER_SEED_TARGET', '14'))
# 2026-08-14: cert-tier bearer token so this recorder can also reach
# Tier-1-gated routes (knowledge-graph, osint/scopes) -- the original 9
# endpoints below are all Tier-0 public, so this was never needed until the
# graph/osint/board capture was added. Sent on every request (harmless for
# the Tier-0 endpoints too, since a higher tier still satisfies a Tier-0
# check). Minted via src/ctdc_token/cli.py, stored in
# /etc/corporatetraveldc/dispatch-secrets.env as DEMO_RECORDER_API_TOKEN.
API_TOKEN   = os.environ.get('DEMO_RECORDER_API_TOKEN', '')

# Kept here for backward-compat imports (demo_api.py imports this name from
# demo.recorder) -- source of truth is now demo.db.RETENTION_TIERS.
RETENTION_TIERS = demo_db.RETENTION_TIERS

ENDPOINTS = [
    'tfr', 'weather', 'alerts', 'cps', 'notams',
    'amtrak', 'opsplan', 'route', 'brief',
    # 2026-08-14: knowledge-graph, OSINT feed, and board -- added so the
    # demo can show these features live instead of them being permanently
    # absent (they were never in this list). knowledge_graph_meta is
    # summary-only (generated_at/node_count/edge_count) -- the live
    # /knowledge-graph/html canvas render (actual node/edge labels) is
    # deliberately NOT captured, since scrubbing an arbitrary rendered graph
    # reliably is a much bigger, unreviewed problem than scrubbing prose/
    # JSON text. osint/scopes (the config of what's being watched) is also
    # deliberately excluded -- that's a monitoring-target list, not output,
    # and is sensitive independent of any literal string it contains.
    'knowledge_graph_meta', 'osint_feed', 'board', 'board_threads',
]

# Storage identifier -> live URL path, for entries above whose live route
# has more than one path segment (the flat identifiers used as SQL
# `endpoint` values can't contain slashes). Keep in sync with
# demo_api.ENDPOINT_PATHS.
ENDPOINT_PATHS: dict[str, str] = {
    'knowledge_graph_meta': 'knowledge-graph/meta',
    'osint_feed':           'osint/feed',
    'board_threads':        'board/threads',
}


# ── Record cycle ──────────────────────────────────────────────────

def record() -> None:
    ts = datetime.now(timezone.utc).isoformat()
    headers = {'Authorization': f'Bearer {API_TOKEN}'} if API_TOKEN else {}
    for ep in ENDPOINTS:
        path = ENDPOINT_PATHS.get(ep, ep)
        try:
            r = requests.get(f'{API}/{path}', headers=headers, timeout=15)
            if not r.ok:
                continue
            text = r.text
            h    = hashlib.sha256(text.encode()).hexdigest()
            if demo_db.last_hash(ep) == h:
                log.debug('skip %s — content unchanged', ep)
                continue
            blob = zlib.compress(text.encode(), level=6)
            demo_db.insert_snapshot(ep, ts, blob, h, compressed=True)
            log.info('recorded %-10s  raw=%5d KB  stored=%4d KB',
                     ep, len(text) // 1024, len(blob) // 1024)
        except Exception as e:
            log.warning('skip %s: %s', ep, e)


# ── Retention ──────────────────────────────────────────────────────

def prune() -> None:
    n = demo_db.prune(RETENTION)
    if n:
        log.info('pruned %d snapshots older than %d days', n, RETENTION)


# ── Main ──────────────────────────────────────────────────────────

def main() -> None:
    demo_db.ensure_schema()  # no-op on Postgres; kept for startup-shape parity
    st = demo_db.seed_status(SEED_TARGET, RETENTION)
    log.info(
        'recorder v3 ready — interval=%ds  retention=%dd  '
        'seed=%d/%d days  ready=%s  db=%.1f MB',
        INTERVAL, RETENTION,
        st['seed_days'], SEED_TARGET, st['ready'], st['db_size_mb']
    )
    while True:
        record()
        prune()
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()
