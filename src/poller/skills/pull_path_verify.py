"""
pull_path_verify -- belt-and-suspenders connectivity/validity probe of every
PULL-capable feed source, run on a 12h timer.

Rationale (operator directive 2026-08-07): feed_state answers "is data
arriving right now?" For feeds that also have a PUSH equivalent, a stale pull
is fine AS LONG AS push covers it -- but that means the pull FALLBACK path can
silently rot and nobody finds out until the push feed dies and the fallback is
needed. This skill independently confirms each pull path is still functional:
a minimal request (NOT a full ingest, nothing persisted from the feed itself),
classifying the response, into db.pull_path_status. /api/v1/feeds joins that on
so both dimensions show ("push: covered, pull: verified" / "pull: failed").

FIDELITY: probes replicate each fetcher's REAL request by importing that
fetcher's own URL/header constants -- so the probe can't drift from the real
pull path, and a probe pass means the real fetcher's request would work too.

Classification:
  verified     2xx/3xx -- endpoint up and answering
  rate_limited 429 -- up, just throttled
  auth_gated   401/403 on a source we KNOW is credential-gated -- path present,
               creds are the only thing missing (a known separate state)
  degraded     other 4xx (400/404/...) -- up but path/contract may have moved
  failed       5xx or transport error (DNS/connection/timeout) -- pull broken
  unconfigured a non-active source (never provisioned) that isn't reachable --
               reported, but NOT alerted on (it was never a live fallback)
ok = True for verified/rate_limited/auth_gated; False for degraded/failed;
None for unconfigured. Only ACTIVE feeds with ok=False trigger the ntfy alert.

SR-2: exempt -- deterministic, no LLM call.
"""
import logging
import time

import requests

from common import config, db, ntfy_push
from common.sr1_log import log_usage

# Import real request constants from the fetchers so probes mirror them exactly.
from poller.fetchers.metar import ADDS_URL, DC_STATIONS
from poller.fetchers.nws import ALERTS_URL, HEADERS as NWS_HEADERS
from poller.fetchers.nas import NAS_URL
from poller.fetchers.tfr import TFR_URL
from poller.fetchers.notam import NOTAM_URL
from common.airport_fids import AIRPORTS as FIDS_AIRPORTS, _UA as FIDS_UA, _COOKIE as FIDS_COOKIE

_AMTRAK_URL = config.get("AMTRAK_FEED_URL", "https://api.amtraker.com/v3/trains").rstrip("/")

log = logging.getLogger(__name__)

SKILL_NAME = "pull-path-verify"
_UA = "corporatetraveldc/1.0"
CONNECT_TIMEOUT = 6
READ_TIMEOUT = 12


def _fids_headers(airport: str) -> dict:
    cfg = FIDS_AIRPORTS[airport]
    return {"User-Agent": FIDS_UA, "Referer": cfg["referer"], "Cookie": FIDS_COOKIE}


# active=True  -> a pull path we actually rely on (pull-only feed, or the pull
#                 fallback for a push feed); ok=False here is a real alert.
# active=False -> never provisioned (no creds / not a live fallback); reported
#                 but never alerted on.
# auth_gated=True -> 401/403 is an EXPECTED "creds missing", not a break.
def _build_probes() -> list[dict]:
    return [
        {"feed": "metar", "url": ADDS_URL.format(stations=",".join(DC_STATIONS)),
         "headers": {"User-Agent": _UA}, "active": True},
        {"feed": "tfr", "url": TFR_URL,
         "headers": {"User-Agent": _UA, "Accept": "application/json"}, "active": True},
        {"feed": "nas", "url": NAS_URL, "headers": {"User-Agent": _UA}, "active": True},
        {"feed": "nws", "url": ALERTS_URL, "headers": NWS_HEADERS, "active": True},
        {"feed": "amtrak", "url": _AMTRAK_URL,
         "headers": {"User-Agent": _UA}, "active": True},
        {"feed": "dca_fids", "url": FIDS_AIRPORTS["DCA"]["url"],
         "headers": _fids_headers("DCA"), "active": True},
        {"feed": "iad_fids", "url": FIDS_AIRPORTS["IAD"]["url"],
         "headers": _fids_headers("IAD"), "active": True},
        # push:fns carries NOTAMs live; the REST pull needs FAA_NOTAM_API_KEY
        # (not provisioned), so it is NOT a viable fallback -- report, don't alert.
        {"feed": "notam", "url": NOTAM_URL, "headers": {"User-Agent": _UA},
         "auth_gated": True, "active": False},
        # B2B feeds -- never provisioned; hosts may not even resolve publicly.
        {"feed": "eurocontrol", "url": "https://www.b2b.opsnetwork.eurocontrol.int/B2B_OPS/gateway/spec",
         "headers": {"User-Agent": _UA}, "auth_gated": True, "active": False},
        {"feed": "jasdat", "url": "https://www.jasdat.go.jp/api/v1",
         "headers": {"User-Agent": _UA}, "auth_gated": True, "active": False},
    ]


def _classify_code(code: int, auth_gated: bool):
    if 200 <= code < 400:
        return True, "verified"
    if code == 429:
        return True, "rate_limited"
    if code in (401, 403):
        return (auth_gated, "auth_gated" if auth_gated else "degraded")
    if 400 <= code < 500:
        return False, "degraded"
    return False, "failed"  # 5xx


def _one_request(p: dict):
    """Returns (code, detail, transport_error_bool). code is None on transport error."""
    try:
        r = requests.get(p["url"], headers=p.get("headers"),
                         timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                         stream=True, allow_redirects=True)
        code = r.status_code
        r.close()
        return code, f"HTTP {code}", False
    except requests.RequestException as e:
        return None, f"{type(e).__name__}: {e}"[:200], True


def _probe(p: dict) -> dict:
    auth_gated = p.get("auth_gated", False)
    active = p.get("active", True)
    t0 = time.monotonic()
    code, detail, transport_err = _one_request(p)
    # Retry ONCE on a transient failure (5xx or transport error) so a blip
    # doesn't page anyone -- a real outage still fails both.
    if transport_err or (code is not None and code >= 500):
        time.sleep(2)
        code, detail, transport_err = _one_request(p)
    latency = int((time.monotonic() - t0) * 1000)

    if transport_err:
        if not active:
            return {"feed": p["feed"], "ok": None, "state": "unconfigured",
                    "code": None, "latency_ms": latency, "detail": detail}
        return {"feed": p["feed"], "ok": False, "state": "failed",
                "code": None, "latency_ms": latency, "detail": detail}

    ok, state = _classify_code(code, auth_gated)
    if not active and not ok:
        state, ok = "unconfigured", None
    return {"feed": p["feed"], "ok": ok, "state": state, "code": code,
            "latency_ms": latency, "detail": detail}


def run_verify() -> list[dict]:
    results = []
    now = time.time()
    for p in _build_probes():
        res = _probe(p)
        db.upsert_pull_path_status(
            res["feed"], now, res["ok"], res["state"],
            res["code"], res["latency_ms"], res["detail"],
        )
        results.append(res)
        log.info("%s: %-12s %-12s %s (%dms)", SKILL_NAME, res["feed"],
                 res["state"], res["detail"], res["latency_ms"])
    return results


def main() -> None:
    status = "ok"
    try:
        results = run_verify()
        # Only ACTIVE feeds with ok is False are real alerts.
        failed = [r for r in results if r["ok"] is False]
        verified = sum(1 for r in results if r["state"] == "verified")
        gated = sum(1 for r in results if r["state"] == "auth_gated")
        unconf = sum(1 for r in results if r["state"] == "unconfigured")
        summary = (f"{verified} verified, {gated} auth-gated, {unconf} unconfigured, "
                   f"{len(failed)} failed / {len(results)} pull sources")
        if failed:
            status = "pull_failure"
            body = "\n".join(f"- {r['feed']}: {r['state']} ({r['detail']})" for r in failed)
            log.warning("%s: %s\n%s", SKILL_NAME, summary, body)
            ntfy_push.send(
                "ops-health",
                f"Pull-path verify: {summary}\nFailed (active pull fallback broken):\n{body}",
                title="Pull-path fallback FAILED", priority=4, tags="warning",
            )
        else:
            log.info("%s: all active pull paths viable -- %s", SKILL_NAME, summary)
            ntfy_push.send(
                "ops-health",
                f"Pull-path verify OK: {summary}",
                title="Pull-path fallback check", priority=1, tags="white_check_mark",
            )
    except Exception as e:
        log.error("%s: verify pass itself failed: %s", SKILL_NAME, e)
        status = "error"
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
