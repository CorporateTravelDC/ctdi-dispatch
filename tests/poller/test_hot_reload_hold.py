"""2026-10-23 (operator): the poller's watchlist hot-reload honours a relay's hold sentinel, so a batch
applied to the main checkout cannot go live before its rollout (2026-10-20: 46 watches live before
migration 0082). A stale sentinel (> 6 h) fails open."""
from __future__ import annotations

import os
import time

from poller import hot_reload_hold as h
from shared.watchlist import WatchlistFileWatcher


def _watcher(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(WatchlistFileWatcher, "_check_for_changes", lambda self: calls.append(1))
    w = h.HoldableWatchlistFileWatcher()
    w.hold_file = tmp_path / "hold-hot-reload"
    return w, calls


def test_no_sentinel_reloads(tmp_path, monkeypatch):
    w, calls = _watcher(tmp_path, monkeypatch)
    w._check_for_changes()
    assert calls == [1]


def test_sentinel_holds_then_release_reloads(tmp_path, monkeypatch):
    w, calls = _watcher(tmp_path, monkeypatch)
    w.hold_file.touch()
    w._check_for_changes()
    w._check_for_changes()
    assert calls == []
    w.hold_file.unlink()
    w._check_for_changes()
    assert calls == [1]


def test_stale_sentinel_fails_open(tmp_path, monkeypatch):
    w, calls = _watcher(tmp_path, monkeypatch)
    w.hold_file.touch()
    old = time.time() - h.HOLD_MAX_AGE_S - 60
    os.utime(w.hold_file, (old, old))
    assert not h.hold_active(w.hold_file)
    w._check_for_changes()
    assert calls == [1]


def test_poller_main_uses_the_holdable_watcher():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src/poller/main.py").read_text()
    assert "watcher = HoldableWatchlistFileWatcher()" in src
