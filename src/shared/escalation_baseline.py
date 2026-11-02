"""shared/escalation_baseline.py -- time-of-day escalation baselines (2026-11-01).

Operator 2026-11-01: "run everything off a per-day or time-of-day baseline (whatever
fidelity level we can get from the nearly 75 days we have), and then run the escalation
based off of that new baseline." scripts/build-escalation-baseline.py writes BASELINE_FILE
weekly from history; this module reads it at alert time.

Slots are local (America/New_York) hours, because traffic follows the local clock:
  - weekday/weekend x hour ("wk-h14", "we-h09") while history is short;
  - day of week x hour ("dow0-h14", Monday = 0) for a cell once that cell holds
    DOW_CUTOVER_WEEKS distinct weeks of samples (operator: switch at 20 weeks).
A cell with fewer than MIN_SAMPLES samples is not used, and the caller falls back to the
rolling 15-minute comparison in shared.sector_coalesce.

Classification for a measured value against its cell:
  escalating = value >= floor and value > p90
  surge      = escalating and value >= max(2 x floor, 2 x max(p90, 1), p99 + 1)
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASELINE_FILE = Path(os.environ.get("CTDC_ESCALATION_BASELINE",
                                    "/var/lib/corporatetraveldc/escalation-baseline.json"))
LOCAL_TZ = ZoneInfo("America/New_York")
DOW_CUTOVER_WEEKS = 20
MIN_SAMPLES = 8
_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": {}}


def _load() -> dict:
    """The parsed baseline file, re-read when its mtime changes. Missing or unreadable
    file = no baselines (callers fall back); never raises."""
    try:
        mtime = BASELINE_FILE.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] != mtime:
            try:
                _cache["data"] = json.loads(BASELINE_FILE.read_text())
            except (OSError, ValueError):
                _cache["data"] = {}
            _cache["mtime"] = mtime
        return _cache["data"]


def slot_keys(when: float) -> tuple[str, str]:
    """(day-of-week slot, weekday/weekend slot) for a Unix time, in local hours."""
    local = datetime.fromtimestamp(when, LOCAL_TZ)
    dow, hour = local.weekday(), local.hour
    return f"dow{dow}-h{hour:02d}", f"{'we' if dow >= 5 else 'wk'}-h{hour:02d}"


def slot_label(slot: str) -> str:
    """Human label: 'Mon 14:00' or 'weekday 14:00'."""
    kind, hour = slot.split("-h")
    if kind.startswith("dow"):
        return f"{_DOW_NAMES[int(kind[3:])]} {hour}:00"
    return f"{'weekend' if kind == 'we' else 'weekday'} {hour}:00"


def lookup(signal: str, key: str, when: float, data: dict | None = None) -> dict | None:
    """The baseline cell for (signal, key) at `when`, or None when there isn't a usable one.
    Prefers the day-of-week cell once it has DOW_CUTOVER_WEEKS weeks, else weekday/weekend."""
    data = _load() if data is None else data
    cells = (data.get("signals") or {}).get(signal, {}).get(str(key).upper())
    if not cells:
        return None
    dow_key, wk_key = slot_keys(when)
    cell = cells.get(dow_key)
    if cell and cell.get("weeks", 0) >= DOW_CUTOVER_WEEKS and cell.get("n", 0) >= MIN_SAMPLES:
        return {**cell, "slot": dow_key}
    cell = cells.get(wk_key)
    if cell and cell.get("n", 0) >= MIN_SAMPLES:
        return {**cell, "slot": wk_key}
    return None


def classify(value: float, cell: dict, floor: int) -> tuple[bool, bool]:
    """(escalating, surge) for `value` against a baseline cell."""
    p90 = float(cell.get("p90", 0))
    p99 = float(cell.get("p99", p90))
    escalating = value >= floor and value > p90
    surge = escalating and value >= max(2 * floor, 2 * max(p90, 1.0), p99 + 1)
    return escalating, surge
