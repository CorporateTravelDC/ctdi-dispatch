"""
common.ntfy_push — central ntfy dispatch helper.

All notification pushes go through here so that:
  - Content-Type: text/plain is always set (prevents file-download on long bodies)
  - Click: points to the correct dispatch-runner view per topic
  - Token strip (some configs store "token:label" in secrets.env)
"""
import logging
import time
from typing import Optional

import requests

from common import config
from common.push_dedup import PushDedup, content_hash

log = logging.getLogger(__name__)

# Retry added 2026-07-20 -- confirmed via shared/watchlist.py's identical
# push path that ntfy intermittently returns 403 Forbidden under
# concurrent load with nothing in ntfy's own server logs to explain it
# (messages_published counter climbs steadily and healthily through the
# same windows) -- points to a transient client/network-path hiccup
# rather than a deterministic auth/config problem. This module shares the
# same server, network path, and concurrent-access pattern, so the same
# mitigation applies here: retry generic (non-connection/timeout) failures
# a bounded number of times before giving up, rather than a single
# silent-drop attempt. Connection/timeout errors already had a distinct
# fallback-URL path below; this only affects the "the request reached the
# server and got a bad status" branch.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECS = 0.5  # doubles each retry: 0.5s, 1s

# Idempotency guard added 2026-07-21 -- same fix and same shared state file
# as shared/watchlist.py's identical guard (see that module's docstring for
# the full false-negative-403 root cause). Narrowly targets 401/403 only;
# genuine failure signals (timeouts, connection errors, other statuses)
# still retry-and-resend exactly as before.
_AMBIGUOUS_STATUS_TTL_SECS = 90
_ambiguous_dedup = PushDedup("ntfy-ambiguous-status", dedup_secs=_AMBIGUOUS_STATUS_TTL_SECS)

RUNNER_BASE = "https://ops.example.com"
# ops.example.com now serves the full runner app (the same
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
        idem_key = content_hash(f"{topic}|{title}|{message}|{priority}")
        backoff = _RETRY_BACKOFF_SECS
        last_exc: Exception | None = None
        last_body: str | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                resp = requests.post(attempt_url, data=message.encode("utf-8"), headers=headers, timeout=10)
                resp.raise_for_status()
                if attempt > 1:
                    log.info("ntfy OK on retry %d/%d: url=%s topic=%s priority=%d",
                             attempt, _RETRY_ATTEMPTS, attempt_url, topic, priority)
                else:
                    log.info("ntfy OK: url=%s topic=%s priority=%d", attempt_url, topic, priority)
                return True
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                log.warning("ntfy unreachable: url=%s error=%s", attempt_url, exc)
                return None  # signal: try fallback -- not worth retrying the same unreachable host
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (401, 403):
                    # Documented false-negative pattern -- don't resend, mark
                    # as probable-delivery and stop instead of risking a
                    # confirmed duplicate for a message that likely already went out.
                    if _ambiguous_dedup.should_push(idem_key, idem_key):
                        _ambiguous_dedup.record(idem_key, idem_key)
                        log.warning(
                            "ntfy %s: url=%s topic=%s -- known false-negative pattern, "
                            "treating as delivered, NOT resending: %s",
                            status, attempt_url, topic, exc,
                        )
                    else:
                        log.warning(
                            "ntfy %s: url=%s topic=%s -- already marked probable-delivery "
                            "within %ds window, suppressing resend",
                            status, attempt_url, topic, _AMBIGUOUS_STATUS_TTL_SECS,
                        )
                    return True
                last_exc = exc
                last_body = getattr(getattr(exc, "response", None), "text", None)
                if attempt < _RETRY_ATTEMPTS:
                    log.warning(
                        "ntfy attempt %d/%d failed: url=%s topic=%s error=%s -- retrying in %.1fs",
                        attempt, _RETRY_ATTEMPTS, attempt_url, topic, exc, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
            except Exception as exc:
                last_exc = exc
                last_body = getattr(getattr(exc, "response", None), "text", None)
                if attempt < _RETRY_ATTEMPTS:
                    log.warning(
                        "ntfy attempt %d/%d failed: url=%s topic=%s error=%s -- retrying in %.1fs",
                        attempt, _RETRY_ATTEMPTS, attempt_url, topic, exc, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
        log.error("ntfy FAILED after %d attempts: url=%s topic=%s error=%s body=%s",
                  _RETRY_ATTEMPTS, attempt_url, topic, last_exc, last_body)
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
