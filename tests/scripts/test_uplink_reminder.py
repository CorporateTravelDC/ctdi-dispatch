"""tests/scripts/test_uplink_reminder.py -- weekly nudge while the failover link is down; one nudge when stable."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("ur", Path(__file__).resolve().parents[2] / "scripts/uplink_reminder.py")
ur = importlib.util.module_from_spec(spec); spec.loader.exec_module(ur)


def run_days(states_up, start, monkeypatch):
    sent = []
    monkeypatch.setattr(ur, "nudge", lambda t, m, p: sent.append((start, t)))
    st = {"down_since": "2026-10-06"}
    for i, up in enumerate(states_up):
        st = ur.step(st, up, start + timedelta(days=i), "enu1", 0, 3)
    return st, [t for _, t in sent]


def test_down_nudges_only_on_mondays(monkeypatch):
    start = datetime(2026, 10, 13, 14, 7, tzinfo=timezone.utc)      # a Tuesday
    st, sent = run_days([False] * 14, start, monkeypatch)
    assert sent == ["Failover uplink still down"] * 2 and st["down_since"] == "2026-10-06"


def test_back_and_stable_announced_once_then_quiet(monkeypatch):
    start = datetime(2026, 10, 13, 14, 7, tzinfo=timezone.utc)
    st, sent = run_days([True] * 10, start, monkeypatch)
    assert sent == ["Failover uplink back and stable"] and st["announced_stable"]


def test_flapping_resets_and_drop_restarts_reminders(monkeypatch):
    start = datetime(2026, 10, 13, 14, 7, tzinfo=timezone.utc)
    st, sent = run_days([True, True, False, True, True, True] + [False] * 7, start, monkeypatch)
    assert sent[0] == "Failover uplink back and stable" and "Failover uplink still down" in sent[1:]
    assert st["down_since"] == (start + timedelta(days=6)).strftime("%Y-%m-%d")


def test_one_decision_per_day(monkeypatch):
    sent = []
    monkeypatch.setattr(ur, "nudge", lambda t, m, p: sent.append(t))
    monday = datetime(2026, 10, 19, 14, 7, tzinfo=timezone.utc)
    st = ur.step({"down_since": "2026-10-06"}, False, monday, "enu1", 0, 3)
    ur.step(st, False, monday + timedelta(hours=2), "enu1", 0, 3)
    assert sent == ["Failover uplink still down"]
