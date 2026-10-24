"""poller/hot_reload_hold.py -- let a relay hold the watchlist hot-reload until its rollout is done.

2026-10-23 (operator): the poller's WatchlistFileWatcher re-reads watchlists/permanent_*.json from
the MAIN checkout (bind-mounted via /opt/corporatetraveldc/watchlists) every ~60 s on mtime change.
A relay that `git apply --index`es a batch into the checkout therefore makes new data live before
its code/schema rollout: 2026-10-20, 46 new watches went live before migration 0082 was applied.

The sentinel: while HOLD_FILE exists, the periodic hot-reload is skipped (mtimes are NOT recorded,
so the first poll after the sentinel is removed loads every change). A relay touches it BEFORE the
apply and removes it AFTER the rollout (scripts/relay-lint.py enforces this).

The path is /var/lib/corporatetraveldc/hold-hot-reload, not ~/.local/state/...: /var/lib/
corporatetraveldc is already mounted read-write into the poller container (same path), so no quadlet
change is needed for the poller to see it.

Fail-open: a sentinel older than HOLD_MAX_AGE_S (6 h; a relay that died mid-way leaves it behind)
is ignored with a warning, so a forgotten hold can never freeze watchlist changes indefinitely.
Only the periodic hot-reload is held; the startup load is not (a restarted poller runs the new
code, which is what the hold waits for).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from shared.watchlist import WatchlistFileWatcher

log = logging.getLogger("poller.hot_reload_hold")

HOLD_FILE = Path(os.environ.get("CTDC_HOLD_HOT_RELOAD", "/var/lib/corporatetraveldc/hold-hot-reload"))
HOLD_MAX_AGE_S = 6 * 3600


def hold_active(path: Path = HOLD_FILE, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except OSError:
        return False
    if age > HOLD_MAX_AGE_S:
        log.warning("watchlist hot-reload hold %s is %.1f h old (> %d h) -- ignoring it (fail-open)",
                    path, age / 3600, HOLD_MAX_AGE_S // 3600)
        return False
    return True


class HoldableWatchlistFileWatcher(WatchlistFileWatcher):
    """WatchlistFileWatcher whose periodic re-read honours the hold sentinel."""

    hold_file: Path = HOLD_FILE

    def __init__(self) -> None:
        super().__init__()
        self._held_logged = False

    def _check_for_changes(self) -> None:
        if hold_active(self.hold_file):
            if not self._held_logged:
                log.info("watchlist hot-reload held by %s (a relay rollout is in progress)", self.hold_file)
                self._held_logged = True
            return
        if self._held_logged:
            log.info("watchlist hot-reload hold released -- re-reading changed files")
            self._held_logged = False
        super()._check_for_changes()
