"""Semantic compile: batch-sized, transaction-scoped timeouts and an uncontended slot
(lean-out L2 / D6, 2026-10-19).

~17% of compiles failed (6 of 14 in the 04:02Z / 00:02 ET slot, beside the 03:40Z backup, the
00:08 ET knowledge-graph compile and the 00:12 ET entity digest) on the platform's OLTP limits
(statement 60s, lock 10s, idle-in-transaction 60s). The compile now SET LOCALs bounded batch
limits inside its own transaction (revert at COMMIT; nothing global, nothing leaks to a pooled
connection), and its midnight run moved to 02:52 ET."""
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from second_brain.semantic import compile as sc  # noqa: E402

REPO = Path(__file__).parent.parent.parent


class FakePg:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)


def test_set_local_only_bounded_and_never_global(monkeypatch):
    for _, env, _ in sc.COMPILE_TIMEOUTS:
        monkeypatch.delenv(env, raising=False)
    c = FakePg()
    issued = sc._apply_batch_timeouts(c)
    assert issued == c.sql and len(issued) == 3
    assert all(s.startswith("SET LOCAL ") for s in issued)          # transaction-scoped
    assert not any(re.search(r"=\s*'0'", s) for s in issued)        # never unbounded
    joined = " ".join(issued)
    assert "statement_timeout = '15min'" in joined and "lock_timeout = '90s'" in joined
    assert "idle_in_transaction_session_timeout = '10min'" in joined


def test_env_override_is_validated(monkeypatch):
    monkeypatch.setenv("SEMANTIC_COMPILE_LOCK_TIMEOUT", "2min")
    monkeypatch.setenv("SEMANTIC_COMPILE_STATEMENT_TIMEOUT", "0; DROP TABLE x")
    c = FakePg()
    sc._apply_batch_timeouts(c)
    assert "SET LOCAL lock_timeout = '2min'" in c.sql
    assert "SET LOCAL statement_timeout = '15min'" in c.sql         # bad value -> default
    assert not any("DROP" in s for s in c.sql)


def test_sqlite_test_double_is_untouched():
    conn = sqlite3.connect(":memory:")
    assert sc._apply_batch_timeouts(conn) == []


def test_timeouts_are_set_before_the_truncate(monkeypatch):
    order = []
    monkeypatch.setattr(sc, "_apply_batch_timeouts", lambda conn: order.append("timeouts"))

    def stop(conn):
        order.append("schema")
        raise RuntimeError("stop here")

    monkeypatch.setattr(sc, "_create_schema", stop)
    monkeypatch.setattr(sc, "_record_failed_run", lambda *a: None)
    with pytest.raises(RuntimeError):
        sc.compile_layer(db_path=":memory:")
    assert order == ["timeouts", "schema"]


def test_postgresql_conf_keeps_its_oltp_limits():
    conf = (REPO / "config/postgresql.conf").read_text()
    assert re.search(r"^statement_timeout = 60s", conf, re.M)
    assert re.search(r"^lock_timeout = 10s", conf, re.M)


def test_midnight_slot_moved_off_the_backup_window():
    timer = (REPO / ".config/systemd/user/corporatetraveldc-semantic-compile-daily.timer").read_text()
    cal = [l for l in timer.splitlines() if l.startswith("OnCalendar=")]
    assert cal == ["OnCalendar=*-*-* 02:52:00 America/New_York",
                   "OnCalendar=*-*-* 06,12,18:02:00 America/New_York"]
    assert "00,06,12,18:02" not in "\n".join(cal)
