"""Demo archive recorder — polls live API, writes to demo.db, prunes >56 days."""
import sqlite3, time, json, logging, requests
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('demo.recorder')

DB = '/var/lib/corporatetraveldc/demo.db'
API = 'http://127.0.0.1:8000/api/v1'
INTERVAL = 300
RETENTION = 56

ENDPOINTS = ['tfr','weather','alerts','cps','notams','amtrak','opsplan','route','brief','runsheet']

def init_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        payload TEXT NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_endpoint_time ON snapshots(endpoint, captured_at)')
    conn.commit()

def record(conn):
    ts = datetime.now(timezone.utc).isoformat()
    for ep in ENDPOINTS:
        try:
            r = requests.get(f'{API}/{ep}', timeout=15)
            if r.ok:
                conn.execute('INSERT INTO snapshots(endpoint,captured_at,payload) VALUES(?,?,?)',
                             (ep, ts, r.text))
                log.info('recorded %s', ep)
        except Exception as e:
            log.warning('skip %s: %s', ep, e)
    conn.commit()

def prune(conn):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION)).isoformat()
    n = conn.execute('DELETE FROM snapshots WHERE captured_at < ?', (cutoff,)).rowcount
    conn.commit()
    if n:
        log.info('pruned %d old records', n)

def main():
    conn = sqlite3.connect(DB, check_same_thread=False)
    init_db(conn)
    log.info('recorder started interval=%ds retention=%dd', INTERVAL, RETENTION)
    while True:
        record(conn)
        prune(conn)
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
