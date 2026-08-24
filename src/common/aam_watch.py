"""
common.aam_watch -- shared helper for folding the weekly AAM (advanced air
mobility / vertiport / eVTOL / Part 108) watch section into the hourly
ops-brief and ep-advance briefs, without either of them re-scraping or
re-synthesizing it every hour.

The actual scrape + Ollama synthesis happens once a week in
poller/skills/aam_weekly_watch.py, which writes the result here. Both
hourly briefs just read this cache file and splice it in if it's fresh.
Operator directive 2026-07-23: weekly cadence for this content, not
hourly -- it doesn't change hour to hour and there's no reason to burn an
Ollama cycle on it every run.

Split framing (added 2026-07-23, same-day follow-up): the hourly briefs
append this text as a raw post-synthesis appendix -- it does NOT pass
back through ops_brief.py's or ep_advance_brief.py's own Ollama call, so
a single shared cache would read identically in both briefs. The weekly
job now writes two differently-framed versions (ops: logistics/ground-
transport relevance; ep: security/counter-UAS relevance) so each brief
gets audience-appropriate analysis instead of one generic version copied
into both.
"""
import logging
import pathlib
from datetime import datetime, timezone

from common import config

log = logging.getLogger(__name__)

_LEGACY_CACHE_FILENAME = "aam_weekly_watch.txt"  # pre-split, single shared version
_MAX_AGE_DAYS = 8  # one week + 1 day grace period before a run is skipped/late


def _cache_filename(flavor: str) -> str:
    if flavor not in ("ops", "ep"):
        raise ValueError(f"aam_watch: unknown flavor {flavor!r}, expected 'ops' or 'ep'")
    return f"aam_weekly_watch_{flavor}.txt"


def _read_if_fresh(path: pathlib.Path) -> str:
    if not path.exists():
        return ""
    age_days = (
        datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    ) / 86400
    if age_days > _MAX_AGE_DAYS:
        log.info("aam_watch: %s is %.1f days old (>%d) -- skipping, weekly job may be overdue",
                 path.name, age_days, _MAX_AGE_DAYS)
        return ""
    return path.read_text().strip()


def get_aam_watch_section(flavor: str = "ops") -> str:
    """
    Return the cached weekly AAM watch text for the given audience flavor
    ("ops" or "ep") if it exists and is fresh (written within the last
    _MAX_AGE_DAYS days), else "". Never raises -- a missing or stale cache
    is a normal state (first deploy, or the weekly job hasn't run yet this
    week), not an error.

    Falls back to the legacy single shared-version cache file if the
    flavor-specific file doesn't exist yet -- covers the gap between this
    code deploying and the next Sunday run regenerating split versions.
    """
    try:
        state = pathlib.Path(config.state_dir())
        flavored = _read_if_fresh(state / _cache_filename(flavor))
        if flavored:
            return flavored
        legacy = _read_if_fresh(state / _LEGACY_CACHE_FILENAME)
        if legacy:
            log.info("aam_watch: no %s-flavored cache yet, falling back to legacy shared cache",
                     flavor)
        return legacy
    except Exception as e:
        log.debug("aam_watch: cache read failed: %s", e)
        return ""


def get_aam_watch_summary(flavor: str = "ops") -> str:
    """
    2026-08-18: condensed one-paragraph version of get_aam_watch_section(),
    for callers that want to feed AAM/vertiport context INTO their own
    Ollama prompt (so the model weaves it into the narrative) rather than
    string-append the full weekly report as a separate post-hoc section --
    operator feedback on ops-brief: the bolted-on "=== ADVANCED AIR
    MOBILITY WATCH ===" block reads as an afterthought, not part of the
    brief's actual flow.

    Extracts just the "WHAT MATTERS TODAY" framing paragraph the weekly
    job already writes (aam_weekly_watch.py's own condensed top-line
    summary) -- not the full "THIS WEEK'S DEVELOPMENTS" bullet list, which
    is too long to fold into an already token-constrained hourly prompt.
    Returns "" under the same freshness/missing-cache conditions as
    get_aam_watch_section().
    """
    full = get_aam_watch_section(flavor)
    if not full:
        return ""
    start = full.find("WHAT MATTERS TODAY:")
    if start == -1:
        return ""
    start += len("WHAT MATTERS TODAY:")
    end = full.find("THIS WEEK'S DEVELOPMENTS:", start)
    para = full[start:end if end != -1 else None].strip()
    return para
