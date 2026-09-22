"""common.maintenance_window -- in-process mirror of scripts/maintenance-window-guard.sh.

The overnight window (default 23:00-05:00 America/New_York) confines
long report-tier work to the quiet hours so it never contends with the
latency-sensitive alert path. systemd-scheduled units enforce it via
`ExecCondition=scripts/maintenance-window-guard.sh`; that hook is a host
process and is unavailable to work scheduled *inside* the poller, which
is a single long-lived asyncio process. This module is how a poller skill
gets the same gate.

It is deliberately a mirror, not a second source of truth: both read the
SAME three CTDC_MAINTENANCE_WINDOW_* variables from dispatch.env, and the
semantics below are kept identical to the shell guard's, including the
two cases that are easy to get wrong:

  * midnight wrap -- 23:00-05:00 means "at/after start OR before end",
    not a simple between-check, which would never be true.
  * start == end -- treated as ALWAYS OPEN, never always-closed. A
    misconfiguration that silently disabled every governed job would be
    far worse than one that lets jobs run at the wrong hour, because the
    failure is invisible: skipped work raises nothing.

If you change the rules here, change scripts/maintenance-window-guard.sh
in the same commit. A drift between the two means a unit and a skill
would disagree about what "overnight" is.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

DEFAULT_START = "23:00"
DEFAULT_END = "05:00"
DEFAULT_TZ = "America/New_York"


def _to_minutes(hhmm: str) -> int | None:
    """HH:MM -> minutes past midnight, or None if malformed.

    Returns None rather than coercing to 0 -- a silent 0 would move the
    window start to midnight and quietly shift every governed job.
    """
    parts = hhmm.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0], 10), int(parts[1], 10)
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _local_now_minutes(tz: str) -> int:
    """Minutes past midnight in `tz`.

    TZ is set around localtime() rather than read from the host clock so
    the answer matches the OnCalendar= zone the governed units declare,
    even if the host zone drifts. tzset() is required -- without it the C
    library keeps the zone it cached at first use and the override is
    ignored.
    """
    prev = os.environ.get("TZ")
    try:
        os.environ["TZ"] = tz
        time.tzset()
        lt = time.localtime()
        return lt.tm_hour * 60 + lt.tm_min
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


def window() -> tuple[str, str, str]:
    """(start, end, tz) as configured. Same env vars as the shell guard."""
    return (
        os.environ.get("CTDC_MAINTENANCE_WINDOW_START", DEFAULT_START),
        os.environ.get("CTDC_MAINTENANCE_WINDOW_END", DEFAULT_END),
        os.environ.get("CTDC_MAINTENANCE_WINDOW_TZ", DEFAULT_TZ),
    )


def is_open(at_minutes: int | None = None) -> bool:
    """True when the maintenance window is currently open.

    at_minutes overrides the clock (minutes past midnight) for tests, the
    same role --at plays in the shell guard.

    Fails OPEN on a malformed configuration, matching the shell guard: a
    bad value must not become an invisible permanent skip.
    """
    start_s, end_s, tz = window()
    start = _to_minutes(start_s)
    end = _to_minutes(end_s)
    if start is None or end is None:
        log.error("maintenance_window: malformed window %r-%r -- treating as "
                  "always open so jobs are not silently disabled", start_s, end_s)
        return True

    now = at_minutes if at_minutes is not None else _local_now_minutes(tz)

    if start == end:
        return True          # degenerate: always open (see module docstring)
    if start < end:
        return start <= now < end        # same-day window, e.g. 01:00-05:00
    return now >= start or now < end     # wraps midnight, e.g. 23:00-05:00


def describe() -> str:
    """One-line human summary for logs."""
    start, end, tz = window()
    return f"{start}-{end} {tz} ({'open' if is_open() else 'closed'})"
