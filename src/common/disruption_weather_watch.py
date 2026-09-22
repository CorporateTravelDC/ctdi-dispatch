"""
common.disruption_weather_watch -- shared helper for folding a short
truncated capsule of the daily disruption/weather digest into ops_brief.py,
without ops_brief.py re-querying nas_programs/train_events itself or
importing poller.skills.disruption_weather_digest directly.

Same freshness-gated-cache-read shape as common/aam_watch.py (that
module's docstring explains the split-cache pattern this one doesn't need,
since this capsule is a factual stats summary, not audience-framed
narrative -- one shared version suffices for both ops and ep contexts).

The actual analysis + write happens once a day in
poller/skills/disruption_weather_digest.py, which writes the capsule here.
ops_brief.py just reads this cache file and splices it in (truncated
further if needed) if it's fresh -- the "Maxwell's 365 days of leadership
standup"-style short capsule the operator asked for in the daily brief,
distinct from the full digest note written to the second-brain vault.
"""
import logging
import pathlib
from datetime import datetime, timezone

from common import config

log = logging.getLogger(__name__)

_CACHE_FILENAME = "disruption-weather-capsule.txt"
_MAX_AGE_HOURS = 30  # daily job + grace period before a run is considered overdue/stale


def get_disruption_weather_capsule() -> str:
    """Return the cached daily disruption/weather capsule if it exists
    and is fresh (written within the last _MAX_AGE_HOURS), else "".
    Never raises -- a missing or stale cache is a normal state (first
    deploy, or the daily job hasn't run yet), not an error."""
    try:
        path = pathlib.Path(config.state_dir()) / _CACHE_FILENAME
        if not path.exists():
            return ""
        age_hours = (
            datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        ) / 3600
        if age_hours > _MAX_AGE_HOURS:
            log.info("disruption_weather_watch: cache is %.1fh old (>%d) -- skipping, "
                      "daily job may be overdue", age_hours, _MAX_AGE_HOURS)
            return ""
        return path.read_text().strip()
    except Exception as e:
        log.debug("disruption_weather_watch: cache read failed: %s", e)
        return ""
