"""
common.ollama_lock -- process-wide mutex + hot/report priority arbitration
for the single shared Ollama instance.

Problem this solves (found 2026-07-26): every Ollama-backed skill runs as its
own short-lived subprocess, dispatched sequentially by the poller's tick loop.
Sequential dispatch means only one skill executes at a time, but nothing
stopped a slow, low-priority report job (ep-advance-brief, ops-brief,
weekly-summary -- each with their own multi-hundred-second Ollama timeouts)
from occupying Ollama for minutes while a genuinely time-sensitive VIP/TFR
enrichment call sat waiting behind it in the schedule, with no way to jump
the queue. Even after tightening the shared OLLAMA_TIMEOUT to 60s the same
day, a report already mid-flight on its own much larger timeout could still
hold Ollama the whole time a hot alert needed it.

Design:
  - A single flock()'d lock file ensures at most one Ollama request executes
    system-wide at any moment, regardless of which skill/process wants it.
    This is the "don't starve the SYSTEM" half -- no two inference calls ever
    compete for the Pi's CPU/RAM/thermal budget at once, on top of whatever
    the ollama-governor thermal cutoff already does.
  - A lightweight marker file signals "a hot-priority caller is waiting for
    or holding the lock". Callers tagged priority="report" check this marker
    BEFORE attempting to acquire the lock; if it's set, they back off
    immediately rather than queue behind hot work, deferring to their own
    next scheduled interval. This is the "starve everything but hot alerts"
    half -- reports never block a hot alert, they just skip a cycle and
    retry later on their normal schedule, so they stay available (not
    permanently starved), just deprioritized.
  - Callers tagged priority="hot" always attempt the lock (marking the hot
    marker for the duration) and never back off for a report -- reports are
    the only side that defers.

Classification (2026-07-26):
  hot:    route_impact.py / tfr_enrichment.py VIP narrative calls -- gate
          real-time Marine One / POTUS / VIP TFR alerts, must never wait
          behind a report.
  report: osint_monitor.py article narratives, weekly_summary.py,
          ops_brief.py, ep_advance_brief.py -- periodic/scheduled output,
          fine to skip a cycle and catch up next time.

Because flock() is held on an open file descriptor, a crashed or killed
skill process releases the lock automatically -- no stale-lock cleanup
needed, which matters since every caller here is a short-lived subprocess,
not a long-running daemon.
"""

import contextlib
import fcntl
import os
import pathlib
import time

STATE_DIR = pathlib.Path("/var/lib/corporatetraveldc/ollama-lock")
LOCK_PATH = STATE_DIR / "ollama.lock"
HOT_MARKER = STATE_DIR / "hot-pending"


class OllamaBusyError(TimeoutError):
    """Raised when the Ollama slot could not be acquired: either a report
    call backed off because hot work is pending, or a hot call's own wait
    timed out. Callers should treat this exactly like "Ollama unavailable"
    and fall through to their existing deterministic fallback."""


def _ensure_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def is_hot_pending() -> bool:
    """True if a hot-priority caller is currently waiting for or holding the lock."""
    return HOT_MARKER.exists()


@contextlib.contextmanager
def _hot_marker():
    _ensure_dir()
    HOT_MARKER.touch(exist_ok=True)
    try:
        yield
    finally:
        HOT_MARKER.unlink(missing_ok=True)


@contextlib.contextmanager
def ollama_slot(priority: str = "report", timeout: float = 60.0, poll_interval: float = 0.5):
    """Context manager granting exclusive access to Ollama.

    priority="hot": always waits for the lock (up to `timeout` seconds),
        marking itself so report callers back off while this is held.
    priority="report": if a hot caller is currently pending, raises
        OllamaBusyError immediately instead of queueing. If no hot caller is
        pending, behaves like a plain mutex acquire with the given timeout.

    Raises OllamaBusyError if the slot could not be acquired -- immediately
    for a report call when hot work is pending, or after `timeout` seconds
    of waiting otherwise.
    """
    _ensure_dir()

    if priority == "report" and is_hot_pending():
        raise OllamaBusyError(
            "hot-priority Ollama work is pending -- report call deferred to next scheduled cycle"
        )

    ctx = _hot_marker() if priority == "hot" else contextlib.nullcontext()
    with ctx:
        LOCK_PATH.touch(exist_ok=True)
        fd = os.open(str(LOCK_PATH), os.O_RDWR)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise OllamaBusyError(f"could not acquire Ollama lock within {timeout}s")
                    time.sleep(poll_interval)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
