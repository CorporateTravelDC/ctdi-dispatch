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

RUNNER_BASE = "https://dispatch-runner.csexecutiveservices.com"

# Per-topic deep-link targets — mobile tap opens the right view
TOPIC_CLICK: dict[str, str] = {
    "tfr-alert":         f"{RUNNER_BASE}/tfr",
    "hot-alerts":        f"{RUNNER_BASE}/tfr",
    "flight-alerts":     f"{RUNNER_BASE}/status",
    "cps":               f"{RUNNER_BASE}/status",
    "ops-health":        f"{RUNNER_BASE}/status",
    "train-alerts":      f"{RUNNER_BASE}/status",
    "wx-alerts":         f"{RUNNER_BASE}/status",
    "osint-alerts":      f"{RUNNER_BASE}/osint",
    "dispatch":          f"{RUNNER_BASE}/",
    "dispatch-debriefs": f"{RUNNER_BASE}/brief",
    "ops-brief":         f"{RUNNER_BASE}/brief",
}

_DEFAULT_CLICK = RUNNER_BASE


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

    try:
        resp = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        resp.raise_for_status()
        log.info("ntfy OK: topic=%s priority=%d click=%s", topic, priority, dest)
        return True
    except Exception as exc:
        log.error("ntfy FAILED: topic=%s error=%s", topic, exc)
        return False


def send_dual(
    full_message: str,
    concise_message: str,
    *,
    title: str,
    topic_full: str  = "dispatch-debriefs",
    topic_brief: str = "dispatch",
    priority: int = 3,
) -> None:
    """Send the same alert to two topics — full narrative + concise one-liner."""
    send(topic_full,  full_message,     title=title, priority=priority,
         tags="airplane,partly_sunny")
    send(topic_brief, concise_message,  title=title, priority=priority,
         tags="airplane")
