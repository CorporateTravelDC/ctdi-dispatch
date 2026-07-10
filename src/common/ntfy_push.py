"""
common.ntfy_push — central ntfy dispatch helper.

All notification pushes go through here so that:
  - Content-Type: text/plain is always set (prevents file-download on long bodies)
  - Click: points to the correct dispatch-runner view per topic
  - Token strip (some configs store "token:label" in secrets.env)
"""
import logging
from typing import Optional

import requests

from common import config

log = logging.getLogger(__name__)

RUNNER_BASE = "https://ops.csexecutiveservices.com"
# ops.csexecutiveservices.com now serves the full runner app (the same
# screen-reader-capable React SPA that used to live at dispatch-runner) — that
# domain is retired as a live public endpoint (reserved for a future demo-archiver
# stub serving time-delayed data; see demo/recorder.py). All tap-through links
# below point to ops's real routed views, not anchor fragments, since ops no
# longer serves the old single-page anchor-based PWA.

# Per-topic deep-link targets — mobile tap opens the right routed view.
TOPIC_CLICK: dict[str, str] = {
    "tfr-alert":            f"{RUNNER_BASE}/tfr",
    "hot-alerts":           f"{RUNNER_BASE}/tfr",              # VIP/POTUS movement — same signals view
    "flight-alerts":        f"{RUNNER_BASE}/map",              # ADS-B view
    "cps":                  f"{RUNNER_BASE}/",                 # CpsIndicator is in the global header on every view
    "ops-health":           f"{RUNNER_BASE}/status",           # feed health / freshness
    "train-alerts":         f"{RUNNER_BASE}/trains",           # EOTD view
    "wx-alerts":            f"{RUNNER_BASE}/signals#meteorology",
    "osint-alerts":         f"{RUNNER_BASE}/intel",            # IntelView (custom/RSS feed monitor)
    "dispatch":             f"{RUNNER_BASE}/feed",             # live ntfy feed view
    "dispatch-debriefs":    f"{RUNNER_BASE}/brief?tab=ops",
    "ops-brief":            f"{RUNNER_BASE}/brief?tab=ops",
    "ep-advance-debriefs":  f"{RUNNER_BASE}/brief?tab=ep-advance",  # legacy — keep for existing subs
    # EP-specific topics (lowercase per operator directive)
    "ep":               f"{RUNNER_BASE}/brief?tab=ep-advance",   # generic EP alerts (concise)
    "ep-advance":       f"{RUNNER_BASE}/brief?tab=ep-advance",   # advance intel brief (full narrative)
    "ep-briefs":        f"{RUNNER_BASE}/brief?tab=ep-advance",   # on-demand EP snapshots (OOOI-style)
}

_DEFAULT_CLICK = f"{RUNNER_BASE}/"


def send(
    topic: str,
    message: str,
    *,
    title: str = "corporatetraveldc",
    priority: int = 3,
    tags: str = "satellite",
    click_url: Optional[str] = None,
) -> bool:
    """
    Send a plain-text push notification via ntfy.

    Args:
        topic:     ntfy topic name (e.g. "cps", "tfr-alert")
        message:   Notification body (plain text).  Bodies > 4096 bytes are fine
                   because message-size-limit is set to 65536 in server.yml.
        title:     Notification title shown on device.
        priority:  ntfy priority 1–5 (default 3).
        tags:      Comma-separated ntfy emoji tags (default "satellite").
        click_url: Override the tap-to-open URL.  Defaults to the per-topic
                   mapping in TOPIC_CLICK, falling back to RUNNER_BASE.

    Returns True on HTTP 2xx, False on any failure.
    """
    base  = config.ntfy_url()
    token = config.ntfy_token().split(":")[0]   # strip "token:label" suffix
    url   = f"{base}/{topic}"
    dest  = click_url or TOPIC_CLICK.get(topic, _DEFAULT_CLICK)

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "X-Priority":   str(priority),
        "X-Title":      title.encode("utf-8").decode("latin-1", errors="replace"),
        "X-Tags":       tags,
        "Click":        dest,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def _attempt(base_url: str) -> bool:
        attempt_url = f"{base_url}/{topic}"
        try:
            resp = requests.post(attempt_url, data=message.encode("utf-8"), headers=headers, timeout=10)
            resp.raise_for_status()
            log.info("ntfy OK: url=%s topic=%s priority=%d", attempt_url, topic, priority)
            return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            log.warning("ntfy unreachable: url=%s error=%s", attempt_url, exc)
            return None  # signal: try fallback
        except Exception as exc:
            log.error("ntfy FAILED: url=%s topic=%s error=%s", attempt_url, topic, exc)
            return False

    result = _attempt(base)
    if result is None:
        # Primary unreachable — try fallback URL if configured
        fallback = config.ntfy_fallback_url()
        if fallback:
            log.warning("ntfy falling back to %s for topic=%s", fallback, topic)
            result = _attempt(fallback)
            if result is None:
                log.error("ntfy fallback also unreachable: topic=%s", topic)
                return False
        else:
            log.error("ntfy primary unreachable and no fallback configured: topic=%s", topic)
            return False
    return bool(result)


def send_dual(
    full_message: str,
    concise_message: str,
    *,
    title: str,
    topic_full: str  = "dispatch-debriefs",
    topic_brief: str = "dispatch-ops",
    priority: int = 3,
) -> None:
    """Send the same alert to two topics — full narrative + concise one-liner."""
    send(topic_full,  full_message,     title=title, priority=priority,
         tags="airplane,partly_sunny")
    send(topic_brief, concise_message,  title=title, priority=priority,
         tags="airplane")
