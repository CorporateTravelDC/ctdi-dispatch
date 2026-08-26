"""
Regression test for the 2026-08-26 C-32 fix (Opus blind review):
board_consume_nonce() used to SELECT-then-check-then-write, so two
concurrent requests against the same single-use nonce could both pass the
Python-side check before either write landed, minting two valid
board-write tokens from one nonce. This locks in the corrected contract:
a single atomic conditional UPDATE decides the winner, verified with a
real concurrency test (not just sequential calls).
"""
import tempfile
import threading
from pathlib import Path

import common.db as db


def _isolated_db(tmp_path):
    orig_db_path = db._db_path
    db._db_path = lambda: Path(tmp_path)
    db.init_db_all()
    return orig_db_path


def test_sequential_reuse_is_rejected():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = _isolated_db(tmp.name)
    try:
        minted = db.board_mint_nonce(ttl_s=600, label="test")
        first = db.board_consume_nonce(minted["nonce"])
        second = db.board_consume_nonce(minted["nonce"])
        assert first["status"] == "ok"
        assert second["status"] == "consumed"
        assert first["token"] != second.get("token")
    finally:
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)


def test_concurrent_consume_mints_exactly_one_token():
    """The real regression: fire the same nonce from many threads at once
    and confirm exactly one wins, regardless of timing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = _isolated_db(tmp.name)
    try:
        minted = db.board_mint_nonce(ttl_s=600, label="test")
        results = []
        lock = threading.Lock()

        def worker():
            r = db.board_consume_nonce(minted["nonce"])
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok_results = [r for r in results if r["status"] == "ok"]
        consumed_results = [r for r in results if r["status"] == "consumed"]
        assert len(ok_results) == 1, (
            f"expected exactly 1 winning token from a single-use nonce under "
            f"concurrent access, got {len(ok_results)} -- this is the exact "
            f"C-32 race"
        )
        assert len(consumed_results) == 19
    finally:
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)


def test_invalid_nonce_reports_invalid():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = _isolated_db(tmp.name)
    try:
        result = db.board_consume_nonce("bnc_this-was-never-minted")
        assert result["status"] == "invalid"
    finally:
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)


def test_expired_nonce_reports_expired():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = _isolated_db(tmp.name)
    try:
        minted = db.board_mint_nonce(ttl_s=-1, label="test")  # already expired
        result = db.board_consume_nonce(minted["nonce"])
        assert result["status"] == "expired"
    finally:
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)
