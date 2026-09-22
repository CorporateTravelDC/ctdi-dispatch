"""
Regression tests for the record_feed_bytes batching fix (2026-07-19,
mitigation 3 of the ingest memory-leak investigation).

Bug: swim_client.py's receive loop called
    _db_pool.submit(_db.record_feed_bytes, feed_name, raw_bytes, 1, ...)
on EVERY received message, across all 6 feed threads (100+ msgs/sec
combined). record_feed_bytes opens a brand-new sqlite3.connect() every
call (see common/db.py's conn()), and _db_pool only has 2 worker threads --
submit() never blocks, so a lagging pool just piles up pending Future
objects in memory, a plausible contributor to the ingest OOM leak.

Fix: record_feed_bytes is now a pure in-memory counter bump
(_accumulate_feed_bytes), with a single background thread flushing the
accumulated totals to the DB every _COUNTER_FLUSH_INTERVAL seconds
(_flush_feed_counters_once), using one call to _db.record_feed_bytes per
feed per flush instead of one call per message.

These tests assert: (1) accumulation is purely in-memory (no DB calls),
(2) a flush sends the correctly-summed totals and clears pending state,
(3) concurrent accumulation from multiple threads doesn't lose updates,
(4) an empty flush is a no-op (no DB call at all).
"""
import sys
import threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import ingest.swim_client as swim_client


def _reset_counters():
    with swim_client._counter_lock:
        swim_client._pending_counts.clear()


def test_accumulate_is_pure_in_memory_no_db_call():
    _reset_counters()
    with mock.patch("common.db.record_feed_bytes") as mock_record:
        swim_client._accumulate_feed_bytes("fdps", 100, 1, 1)
        swim_client._accumulate_feed_bytes("fdps", 200, 1, 0)
    mock_record.assert_not_called()
    assert swim_client._pending_counts["fdps"] == [300, 2, 1]
    _reset_counters()


def test_flush_sends_summed_totals_and_clears_pending():
    _reset_counters()
    swim_client._accumulate_feed_bytes("stdds", 50, 1, 1)
    swim_client._accumulate_feed_bytes("stdds", 75, 1, 1)
    swim_client._accumulate_feed_bytes("tfms", 10, 1, 0)

    with mock.patch("ingest.swim_client._db.record_feed_bytes") as mock_record:
        swim_client._flush_feed_counters_once()

    calls = {c.args[0]: c.args[1:] for c in mock_record.call_args_list}
    assert calls["stdds"] == (125, 2, 2)
    assert calls["tfms"] == (10, 1, 0)
    assert mock_record.call_count == 2, "one call per feed per flush, not per message"
    assert swim_client._pending_counts == {}, "pending counts must be cleared after flush"


def test_empty_flush_is_a_noop():
    _reset_counters()
    with mock.patch("ingest.swim_client._db.record_feed_bytes") as mock_record:
        swim_client._flush_feed_counters_once()
    mock_record.assert_not_called()


def test_flush_failure_for_one_feed_does_not_lose_or_requeue_others():
    _reset_counters()
    swim_client._accumulate_feed_bytes("itws", 10, 1, 1)
    swim_client._accumulate_feed_bytes("tbfm", 20, 1, 1)

    def _raise_for_itws(feed_name, *_args):
        if feed_name == "itws":
            raise RuntimeError("simulated DB error")

    with mock.patch("ingest.swim_client._db.record_feed_bytes", side_effect=_raise_for_itws) as mock_record:
        swim_client._flush_feed_counters_once()

    assert mock_record.call_count == 2
    # By design (see _flush_feed_counters_once): the snapshot is cleared up
    # front, so a per-feed write failure loses that feed's counts for this
    # window rather than requeuing -- acceptable for a monitoring counter
    # that already resets to 0 on every ingest restart via init_feed_usage,
    # but worth locking in as an explicit, intentional behavior.
    assert swim_client._pending_counts == {}


def test_concurrent_accumulation_from_multiple_threads_is_not_lossy():
    _reset_counters()
    n_threads = 8
    calls_per_thread = 500

    def worker():
        for _ in range(calls_per_thread):
            swim_client._accumulate_feed_bytes("fdps", 10, 1, 1)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected_records = n_threads * calls_per_thread
    assert swim_client._pending_counts["fdps"] == [
        expected_records * 10, expected_records, expected_records,
    ], "lock must prevent lost updates under concurrent accumulation"
    _reset_counters()
