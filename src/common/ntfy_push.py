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
from urllib.parse import urlsplit

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

RUNNER_BASE = config.runner_click_base()
# 2026-08-03: defaults to the Tailscale hostname (corporatetraveldc-dispatch.
# tailxxxxxxx.ts.net), not ops.example.com. That hostname was
# retired 2026-08-02 (see runner/main.py's _RETIRED_HOSTNAMES) and now
# hard-rejects every request at the app layer -- every click-through below
# was landing on a dead link, not just an exposed one. All tap-through links
# below point to the runner's real routed views, not anchor fragments, since
# it no longer serves the old single-page anchor-based PWA.

# Per-topic deep-link targets — mobile tap opens the right routed view.
TOPIC_CLICK: dict[str, str] = {
    "tfr-alert":            f"{RUNNER_BASE}/tfr",
    "hot-alerts":           f"{RUNNER_BASE}/tfr",              # VIP/POTUS movement — same signals view
    "flight-alerts":        f"{RUNNER_BASE}/map",              # ADS-B view
    "cps":                  f"{RUNNER_BASE}/",                 # CpsIndicator is in the global header on every view
    "ops-health":           f"{RUNNER_BASE}/status",           # feed health / freshness
    "train-alerts":         f"{RUNNER_BASE}/trains",           # EOTD view
    "wx-alerts":            f"{RUNNER_BASE}/signals#meteorology",
    # 2026-08-12: was pointed at IntelView (/intel), an unrelated RSS reader
    # that never queried osint_scopes/osint_items -- tapping an osint-alerts
    # push landed on a page showing nothing about it. EventIntelView is the
    # real dedicated view (GET /api/v1/osint/feed, grouped by scope_type).
    "osint-alerts":         f"{RUNNER_BASE}/events",            # EventIntelView (osint_scopes/items)
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


def _safe_click_url(url: str, fallback: str) -> str:
    """2026-08-26 (Opus blind review C-9): several callers (osint_monitor.py
    the one actually exploited in the audit, but any caller passing a
    third-party-sourced URL through as click_url has the identical
    exposure) pass an attacker-influenced URL straight through as the tap-
    through destination with no validation -- a malicious/compromised RSS
    entry, or any Google-News-indexed page an attacker controls, could set
    click_url to a javascript:/data: URI and have it delivered unchanged on
    a priority-4/5 push to the operator's phone. Enforcing this once here,
    at the one place every caller's click_url actually reaches the wire,
    protects every current and future caller without each one needing its
    own scheme check. Only http/https survive; anything else (including an
    unparseable value) falls back to the safe per-topic default."""
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme in ("http", "https"):
        return url
    log.warning("ntfy: refusing unsafe click_url scheme %r -- falling back to %s",
                scheme or url, fallback)
    return fallback


def send(
    topic: str,
    message: str,
    *,
    title: str,
    priority: int = 3,
    tags: str = "satellite",
    click_url: Optional[str] = None,
    email: bool = False,
) -> bool:
    """
    Send a plain-text push notification via ntfy.

    Args:
        topic:     ntfy topic name (e.g. "cps", "tfr-alert")
        message:   Notification body (plain text).  Bodies > 4096 bytes are fine
                   because message-size-limit is set to 65536 in server.yml.
        title:     Notification title shown on device. REQUIRED, no default
                   (2026-08-11) -- title becomes the email Subject: line for
                   every ntfy-relayed SMTP notification, and subject-line
                   discipline is the only per-alert-category filter available
                   client-side (ntfy has no per-topic/per-message sender
                   override, confirmed against its own docs). A generic
                   fallback here would have silently defeated that for any
                   call site that forgot to pass one.
        priority:  ntfy priority 1–5 (default 3).
        tags:      Comma-separated ntfy emoji tags (default "satellite").
        click_url: Override the tap-to-open URL.  Defaults to the per-topic
                   mapping in TOPIC_CLICK, falling back to RUNNER_BASE.
        email:     2026-09-02 (operator directive) -- opt-in per-call. When
                   True, adds X-Email so ntfy's SMTP relay also delivers this
                   push to config.operator_email(). Default False: before
                   this, NO skill in this codebase ever set X-Email (confirmed
                   via full-repo grep + ntfy's own lifetime log showing every
                   email ever sent came from the one-off blog-substack-
                   reminder.sh script, not through this shared helper) --
                   every report skill using send()/send_dual()/
                   send_run_status() was push-notification-only, silently,
                   the whole time. Opt-in (not a global default) so turning
                   this on for one report doesn't suddenly email-blast every
                   other push topic that already goes through this function.

    Returns True on HTTP 2xx, False on any failure.
    """
    base  = config.ntfy_url()
    token = config.ntfy_token().split(":")[0]   # strip "token:label" suffix
    url   = f"{base}/{topic}"
    topic_default = TOPIC_CLICK.get(topic, _DEFAULT_CLICK)
    dest  = _safe_click_url(click_url, topic_default) if click_url else topic_default

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "X-Priority":   str(priority),
        "X-Title":      title.encode("utf-8").decode("latin-1", errors="replace"),
        "X-Tags":       tags,
        "Click":        dest,
    }
    if email:
        headers["X-Email"] = config.operator_email()
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
                    # 2026-08-25 correction (Opus blind review C-6): this
                    # branch used to `return True` on 401/403, on the theory
                    # that ntfy's own messages_published counter kept
                    # climbing through these windows, so the send probably
                    # went out and this was a spurious auth error under
                    # load. That theory has no per-message confirmation
                    # behind it -- an aggregate counter climbing doesn't
                    # prove THIS message landed -- and a permanently wrong/
                    # revoked NTFY_TOKEN produces the exact same status
                    # code, forever. Returning True either way made a
                    # revoked token indistinguishable from healthy delivery
                    # on an alerting platform: 1,274 real dedup entries had
                    # accumulated under the old code with nothing ever
                    # correctly reporting failure. Still suppress the
                    # RESEND (a real transient 401/403 on an otherwise-
                    # healthy token could otherwise duplicate-alert), but
                    # never claim success for a request that never got one.
                    if _ambiguous_dedup.should_push(idem_key, idem_key):
                        _ambiguous_dedup.record(idem_key, idem_key)
                        log.error(
                            "ntfy %s: url=%s topic=%s -- NOT resending (avoids "
                            "duplicate-alerting if this is transient), but this "
                            "is NOT confirmed delivery -- treat as a failed "
                            "send: %s",
                            status, attempt_url, topic, exc,
                        )
                    else:
                        log.error(
                            "ntfy %s: url=%s topic=%s -- already logged as a "
                            "failed send within %ds window, suppressing resend "
                            "(still NOT confirmed delivered)",
                            status, attempt_url, topic, _AMBIGUOUS_STATUS_TTL_SECS,
                        )
                    return False
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
    email: bool = False,
) -> None:
    """Send the same alert to two topics — full narrative + concise one-liner.

    email: passed to the FULL-narrative send only (see send()'s email= doc)
    -- the concise one-liner is a duplicate summary, not worth a second
    email for the same report.
    """
    send(topic_full,  full_message,     title=title, priority=priority,
         tags="airplane,partly_sunny", email=email)
    send(topic_brief, concise_message,  title=title, priority=priority,
         tags="airplane")


def send_run_status(
    skill_name: str,
    status: str,
    *,
    ok_statuses: tuple = ("ok", "fallback"),
    detail: str | None = None,
    systemd_unit: str | None = None,
    topic: str = "dispatch-ops",
    email: bool = False,
) -> None:
    """Lightweight health-check ping for a daily/weekly digest skill's own
    run outcome -- NOT the report content itself. The report stays
    vault-only, per each skill's own established convention (see each
    skill's module docstring for why); this is just "did today's/this
    week's report actually run", added 2026-08-30 per operator directive
    so dispatch-ops carries a real per-skill signal instead of only
    lighting up on weekly_summary's own fire.

    status: whatever value the calling skill's own local status variable
    already holds (e.g. "ok"/"fallback"/"blocked"/"error"). ok_statuses
    lists which of those count as success for this ping's wording -- a
    "fallback" run still produced a usable report via the deterministic
    path, so it still counts as success here even though the skill's own
    log_usage() call records it as a distinct outcome.
    detail: vault path or other one-line specifics to include when the
    caller has one on hand -- optional, since not every call site has an
    easy handle on it (e.g. an exception before the write completed).
    systemd_unit: the corporatetraveldc-<name> unit to point at for log
    investigation on failure -- defaults to a name derived from
    skill_name (underscores -> hyphens) if not given.
    """
    unit = systemd_unit or f"corporatetraveldc-{skill_name.replace('_', '-')}"
    if status in ok_statuses:
        msg = f"{skill_name} ran (status={status})"
        if detail:
            msg += f" — available at {detail}"
        send(topic, msg, title=f"{skill_name}: complete", priority=2, tags="white_check_mark",
             email=email)
    else:
        msg = f"{skill_name} failed (status={status}) — check: journalctl --user -u {unit}"
        send(topic, msg, title=f"{skill_name}: FAILED", priority=4, tags="x", email=email)
