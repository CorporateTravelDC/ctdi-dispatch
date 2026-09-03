"""
ingest_feed_watch -- lightweight recurring check over the platform's own
externally-observable feed-health surface: the same /healthz and
/api/v1/feeds endpoints web/main.py serves to the runner UI and to the
dispatch_get_feeds/dispatch_health_check MCP tools -- plus ntfy's own
/v1/health. Built 2026-08-09 per operator request to run ITWS/NWS/ntfy
health checks on a recurring cadence rather than only when someone
happens to ask.

Deliberately distinct from feed_db_integrity_check.py (30 min timer):
that skill checks feed_state's claimed freshness against the ACTUAL
destination table's own last-write age at the SQLite level (catches
"logs success, DB is dark" silent failures), and pushes on every finding
with no dedup. This skill instead checks the outward-facing HTTP surface
that everything else -- including this operator's phone, via the
dispatch runner UI -- actually sees, plus ntfy delivery health itself (a
different failure class: web/main.py itself down, or ntfy down, doesn't
necessarily show up in feed_db_integrity_check's table-level view at
all). And it uses PushDedup (common/push_dedup.py) so a persistent-but-
unchanged degraded state -- e.g. the long-standing dca_fids/iad_fids
marginal-staleness quirk -- pushes once, then stays quiet until the
state actually changes or 6h elapse, instead of paging on every run.

host.containers.internal, not 127.0.0.1 -- this runs inside a poller
container on the pasta network (see .container quadlet), same reasoning
as transport_pattern_digest.py's Nextcloud reach-through.

2026-08-10 fix (caught in the post-rebuild smoke test, never actually
tested against the real container network before tonight): web/main.py's
port 8000 is only published on 127.0.0.1 and the Tailscale IP (see
corporatetraveldc-web.container's PublishPort lines), NOT reachable via
host.containers.internal from a pasta-networked container at all --
confirmed by direct test, not a timing fluke. Routed through the existing
dispatch.example.com nginx vhost on port 80 instead (which
proxies everything to 127.0.0.1:8000, confirmed in
/etc/nginx/conf.d/dispatch.example.com.conf), same Host-
header-spoofing pattern webdav_client.py already uses for Nextcloud.
ntfy's port (2586) has no such restriction and was already reachable
directly -- left as-is, confirmed working.

Schedule: hourly (corporatetraveldc-ingest-feed-watch.timer). Cheap --
three local HTTP calls, no LLM, typically well under a second of real
work -- so hourly costs nothing and catches a fresh degradation fast;
6-hourly (the other cadence discussed) would just delay a genuine
recovery/regression notice for no real savings at this cost profile.

SR-1: log_usage() in finally block.
SR-2: Exempt -- deterministic HTTP checks, no LLM call.
"""
import logging

import requests

from common import ntfy_push
from common.push_dedup import PushDedup, content_hash
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "ingest-feed-watch"

WEB_BASE = "http://host.containers.internal:80"
WEB_HOST_HEADER = "dispatch.example.com"
NTFY_HEALTH_URL = "http://host.containers.internal:2586/v1/health"

# Re-notify at most every 6h if a degraded state persists unchanged --
# frequent enough that a standing problem doesn't go silent for a whole
# night, infrequent enough not to page repeatedly on the same thing.
# 2026-09-03 (forward-only push_dedup redesign): stays on the explicit
# PERIODIC api -- the 6h still-degraded re-page is this skill's documented
# design (health monitoring wants a heartbeat while broken, unlike the
# SWIM alert paths where elapsed-time re-fires were pure spam).
_dedup = PushDedup("ingest-feed-watch", dedup_secs=21600)


def _check_healthz() -> dict:
    r = requests.get(f"{WEB_BASE}/healthz", headers={"Host": WEB_HOST_HEADER}, timeout=10)
    r.raise_for_status()
    return r.json()


def _check_feeds() -> list[dict]:
    r = requests.get(f"{WEB_BASE}/api/v1/feeds", headers={"Host": WEB_HOST_HEADER}, timeout=10)
    r.raise_for_status()
    body = r.json()
    # /api/v1/feeds's exact top-level shape (bare list vs {"feeds": [...]})
    # isn't pinned down from a spec here -- handle either defensively
    # rather than assuming one and silently returning [] on the other.
    if isinstance(body, dict):
        return body.get("feeds", [])
    return body


def _check_ntfy() -> bool:
    try:
        r = requests.get(NTFY_HEALTH_URL, timeout=10)
        r.raise_for_status()
        return bool(r.json().get("healthy"))
    except Exception as e:
        log.warning("%s: ntfy health check itself failed: %s", SKILL_NAME, e)
        return False


def run_check() -> tuple[str, list[str]]:
    """Returns (summary, problems). problems is [] when everything's
    healthy. Never raises -- a broken check must not itself become an
    outage; each sub-check failure becomes a finding instead.

    Two known, permanent, non-actionable states are deliberately excluded
    from problems so this doesn't page every 6h forever on things that
    will never change without an operator-side external action:
      - feed error "awaiting_credentials" (eurocontrol/jasdat as of
        2026-08-09) -- pending external registration, not a degradation.
      - dca_fids/iad_fids in the /healthz stale-feed list -- the known
        MWAA-scrape marginal-freshness quirk (187s vs a 180s threshold),
        already understood and not urgent (see this session's feed-health
        review). Any OTHER feed_name appearing in that same reason string
        still counts as a real, novel problem.
    """
    problems: list[str] = []
    _KNOWN_STALE_FEEDS = {"dca_fids", "iad_fids"}

    try:
        hz = _check_healthz()
    except Exception as e:
        hz = None
        problems.append(f"web/main.py /healthz unreachable: {e}")

    if hz is not None and hz.get("status") != "ok":
        reason = hz.get("reason") or ""
        stale_named = [s.strip() for s in reason.replace("Stale feeds:", "").split(",") if s.strip()]
        novel_stale = [s for s in stale_named if s not in _KNOWN_STALE_FEEDS]
        if novel_stale:
            problems.append(f"platform health degraded: stale feed(s) {', '.join(novel_stale)}")

    try:
        feeds = _check_feeds()
    except Exception as e:
        feeds = []
        problems.append(f"/api/v1/feeds unreachable: {e}")

    for f in feeds:
        name = f.get("feed_name") or f.get("name")
        error = f.get("display_error") if "display_error" in f else f.get("error")
        push_covered = f.get("push_covered")
        if error and not push_covered and error != "awaiting_credentials":
            problems.append(f"feed '{name}': {error}")

    if not _check_ntfy():
        problems.append("ntfy /v1/health reports unhealthy or unreachable")

    if problems:
        summary = f"{len(problems)} issue(s): " + "; ".join(problems)
    else:
        summary = "all clear -- platform healthz ok, feeds nominal, ntfy healthy"

    return summary, problems


def main() -> None:
    status = "ok"
    try:
        summary, problems = run_check()
        key = content_hash(summary)
        prev = _dedup.get_raw("state")
        was_degraded = bool(prev.get("degraded"))

        should_notify = False
        if problems:
            status = "degraded"
            should_notify = _dedup.should_push_periodic("state", key)
        elif was_degraded:
            should_notify = True  # recovery from a prior degraded state -- always worth one ping

        if problems:
            log.warning("%s: %s", SKILL_NAME, summary)
        else:
            log.info("%s: %s", SKILL_NAME, summary)

        if should_notify:
            if problems:
                ntfy_push.send(
                    "ops-health", f"Ingest feed watch: {summary}",
                    title="Ingest feed/ntfy health -- degraded",
                    priority=3, tags="warning",
                )
            else:
                ntfy_push.send(
                    "ops-health", "Ingest feed watch: recovered -- all clear now.",
                    title="Ingest feed/ntfy health -- recovered",
                    priority=3, tags="white_check_mark",
                )

        _dedup.set_raw("state", {"hash": key, "degraded": bool(problems)})
    except Exception as e:
        log.error("%s: check itself failed: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
