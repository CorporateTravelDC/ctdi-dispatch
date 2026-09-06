"""
Regression tests for the 2026-08-26 C-33 fix (Opus blind review): a
batch of tables had no prune path at all -- append-only or upsert-forever
growth with nothing ever deleting old rows. Confirms each new prune_*
function actually deletes rows older than its cutoff and leaves newer
rows untouched. nas_programs is deliberately not covered here -- it's
explicitly required to retain long-term, not a gap this fix addresses.
"""
import tempfile
import time
from pathlib import Path

import common.db as db


def _isolated_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    db._db_path = lambda: Path(tmp.name)
    db.init_db_all()
    return tmp.name, orig


def _iso(days_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))


def test_prune_train_events():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO train_events (train_number, fetched_at) VALUES (?, ?)",
                ("OLD1", time.time() - 40 * 86400),
            )
            c.execute(
                "INSERT INTO train_events (train_number, fetched_at) VALUES (?, ?)",
                ("NEW1", time.time()),
            )
        deleted = db.prune_train_events(days=30)
        assert deleted == 1
        with db.conn() as c:
            remaining = [r[0] for r in c.execute("SELECT train_number FROM train_events")]
        assert remaining == ["NEW1"]
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_board_messages():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            db._ensure_board(c)
            c.execute(
                "INSERT INTO board_messages (id, ts, thread, from_side, to_side) VALUES (?, ?, ?, ?, ?)",
                ("old-1", _iso(200), "coord", "dispatch", "cowork"),
            )
            c.execute(
                "INSERT INTO board_messages (id, ts, thread, from_side, to_side) VALUES (?, ?, ?, ?, ?)",
                ("new-1", _iso(1), "coord", "dispatch", "cowork"),
            )
        deleted = db.prune_board_messages(days=180)
        assert deleted == 1
        with db.conn() as c:
            remaining = [r[0] for r in c.execute("SELECT id FROM board_messages")]
        assert remaining == ["new-1"]
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_webhook_events():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO webhook_events (source, event_type, payload, received_at) VALUES (?, ?, ?, ?)",
                ("limoanywhere", "test", "{}", time.time() - 100 * 86400),
            )
            c.execute(
                "INSERT INTO webhook_events (source, event_type, payload, received_at) VALUES (?, ?, ?, ?)",
                ("limoanywhere", "test", "{}", time.time()),
            )
        deleted = db.prune_webhook_events(days=90)
        assert deleted == 1
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_flight_ooooi_times():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO flight_ooooi_times (gufi, updated_at) VALUES (?, ?)",
                ("old-gufi", time.time() - 100 * 86400),
            )
            c.execute(
                "INSERT INTO flight_ooooi_times (gufi, updated_at) VALUES (?, ?)",
                ("new-gufi", time.time()),
            )
        deleted = db.prune_flight_ooooi_times(days=90)
        assert deleted == 1
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_stdds_safety_status_history():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO stdds_safety_status_history (airport, new_bitmask, changed_at) VALUES (?, ?, ?)",
                ("DCA", "1010", _iso(200)),
            )
            c.execute(
                "INSERT INTO stdds_safety_status_history (airport, new_bitmask, changed_at) VALUES (?, ?, ?)",
                ("DCA", "1011", _iso(1)),
            )
        deleted = db.prune_stdds_safety_status_history(days=180)
        assert deleted == 1
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_local_airspace_alerts():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO local_airspace_alerts (fired_at, alert_type) VALUES (?, ?)",
                (_iso(100), "test"),
            )
            c.execute(
                "INSERT INTO local_airspace_alerts (fired_at, alert_type) VALUES (?, ?)",
                (_iso(1), "test"),
            )
        deleted = db.prune_local_airspace_alerts(days=90)
        assert deleted == 1
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_international_aviation_feed():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO international_aviation_feed (source, record_type, raw_json, fetched_at) VALUES (?, ?, ?, ?)",
                ("eurocontrol", "notam", "{}", time.time() - 40 * 86400),
            )
            c.execute(
                "INSERT INTO international_aviation_feed (source, record_type, raw_json, fetched_at) VALUES (?, ?, ?, ?)",
                ("eurocontrol", "notam", "{}", time.time()),
            )
        deleted = db.prune_international_aviation_feed(days=30)
        assert deleted == 1
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_expired_session_grants():
    path, orig = _isolated_db()
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO session_grants (id, command_pattern, granted_at, expires_at) VALUES (?, ?, ?, ?)",
                ("old-grant", "sign-manifest:*", time.time() - 40 * 86400, time.time() - 35 * 86400),
            )
            c.execute(
                "INSERT INTO session_grants (id, command_pattern, granted_at, expires_at) VALUES (?, ?, ?, ?)",
                ("recent-grant", "sign-manifest:*", time.time() - 1 * 86400, time.time() + 3600),
            )
        deleted = db.prune_expired_session_grants(grace_days=30)
        assert deleted == 1
        with db.conn() as c:
            remaining = [r[0] for r in c.execute("SELECT id FROM session_grants")]
        assert remaining == ["recent-grant"]
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)


def test_prune_expired_board_auth():
    path, orig = _isolated_db()
    try:
        minted_old = db.board_mint_nonce(ttl_s=-100, label="old")
        minted_new = db.board_mint_nonce(ttl_s=3600, label="new")
        deleted = db.prune_expired_board_auth(grace_days=0)
        assert deleted == 1
        with db.conn() as c:
            remaining = [r[0] for r in c.execute("SELECT label FROM board_enroll_nonces")]
        assert remaining == ["new"]
    finally:
        db._db_path = orig
        Path(path).unlink(missing_ok=True)
