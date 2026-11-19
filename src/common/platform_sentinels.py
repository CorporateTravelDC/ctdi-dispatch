"""common.platform_sentinels -- shared plumbing for the platform-side sentinels (2026-11-18).

Operator, 2026-11-18: the 11-04..11-17 lapse was on the agent side (the cloud subscription), not the
FAA side -- the feeds kept flowing into the database while every agent that might have read them was
offline, so nothing noticed that our FDPS store rarely holds a pre-departure plan. These sentinels
therefore run as user timers ON THE BOX and keep working with no agent running:

  plan_coverage        daily   share of legs with a same-occurrence plan visible before OUT/OFF
  ground_stop_tripwire 5 min   wide ground stops / a watched leg's airport under a GS or GDP
  artcc_sentinel       daily   per-centre first-message volume vs its trailing baseline
  agent_lapse          30 min  agent accounts silent 6 h / 12 h / 24 h, with dismissal

Shared rules: every check is fail-soft (a failure is logged with its reason and the run exits 0 so
the timer is not marked failed for a transient cause); a run skips itself above the load line
(heavy queries never push the box toward the thermal guard); times are UTC with ET beside them;
no secret, token or protected identifier is ever printed. Reports go to
/var/lib/corporatetraveldc/reports/<name>.json; a short Markdown copy goes to a handoff folder the
handoff mirror carries into the vault (best effort).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("platform_sentinels")
ET = ZoneInfo("America/New_York")
REPORTS_DIR = Path(os.environ.get("CTDC_REPORTS_DIR", "/var/lib/corporatetraveldc/reports"))
HANDOFF_DIR = Path(os.environ.get("CTDC_SENTINEL_HANDOFF_DIR",
                                  "/srv/ctdc-handoff/platform-sentinels-from-corporatetraveldc"))
MAX_LOAD = float(os.environ.get("CTDC_SENTINEL_MAX_LOAD", "24"))   # the relay-wait-load line
TOPIC = "ops-health"
VERTICAL = "platform_ops"


def now() -> float:
    return time.time()


def fmt(ts: float | None) -> str:
    """'2026-11-18 18:51Z (13:51 ET)' -- UTC first, ET beside it, from the system zone database."""
    if ts is None:
        return "unknown"
    u = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc)
    e = u.astimezone(ET)
    return f"{u:%Y-%m-%d %H:%M}Z ({e:%H:%M} ET)"


def hours(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    return f"{seconds / 3600:.1f} h"


def load1() -> float | None:
    try:
        return float(Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def load_ok(name: str, limit: float | None = None) -> bool:
    """False (and a logged reason) when load is at or above the line; the caller exits 0."""
    lim = MAX_LOAD if limit is None else limit
    l1 = load1()
    if l1 is not None and l1 >= lim:
        log.warning("%s: skipped this run: load1 %.2f >= %.0f (heavy checks wait for load)", name, l1, lim)
        return False
    return True


def write_report(name: str, report: dict, markdown: str | None = None) -> Path | None:
    """JSON report under REPORTS_DIR; optional Markdown copy for the vault mirror. Never raises."""
    out = None
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORTS_DIR / f"{name}.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        tmp.replace(out)
    except OSError as e:
        log.warning("%s: report not written (%s)", name, e)
        out = None
    if markdown:
        try:
            HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
            day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
            (HANDOFF_DIR / f"{name}-{day}.md").write_text(markdown)
        except OSError as e:
            log.info("%s: handoff copy skipped (%s)", name, e)
    return out


def push(kind: str, message: str, *, title: str, priority: int = 3, entity: str | None = None,
         detail: dict | None = None, email: bool = False, actions: str | None = None,
         vertical: str = VERTICAL, topic: str = TOPIC) -> bool:
    """One ops-health push through the push parity gate (kind registered in push_policy_registry).
    Fail-soft: a delivery failure is logged and returns False."""
    try:
        from common import ntfy_push
        return bool(ntfy_push.send(topic, message, title=title, priority=priority, tags="satellite",
                                   email=email, actions=actions, kind=kind, vertical=vertical,
                                   entity=entity, detail=detail or {}))
    except Exception as e:   # noqa: BLE001 -- never let an alert path crash the sentinel
        log.error("push %s failed: %s", kind, type(e).__name__)
        return False


def run_safely(name: str, fn, *args, **kw) -> int:
    """Run one sentinel; any exception is logged with its reason and the run exits 0."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        fn(*args, **kw)
    except Exception as e:   # noqa: BLE001
        log.error("%s: run failed (%s: %s); next timer run retries", name, type(e).__name__, str(e)[:200])
    return 0
