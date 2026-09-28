"""
FAA NOTAM fetcher — NMS-API (NOTAM Management Service), production
https://api-nms.aim.faa.gov/nmsapi/v1/notams

2026-09-02 (operator research + real onboarding credentials/docs
recovered from a months-old spam-folder email): this fetcher previously
targeted the legacy FAA NOTAM Search API (api.faa.gov/notamSearch),
retired 2026-04-18 as part of the FAA's cutover to the new NOTAM
Management Service (NMS) -- there was never a working credential to
find for that old product, it was simply gone. The operator located
their actual NMS-API onboarding materials (client_id/client_secret
Excel handoff, curl examples, OpenAPI spec, FAQ) and this fetcher is
rewritten against the real, current API.

Auth: OAuth2 client-credentials. POST to the auth host (NOT the
/nmsapi/ API host) with HTTP Basic client_id:client_secret returns a
bearer token, confirmed live 30-minute expiry ("expires_in": 1799).
_get_bearer_token() below caches it and refreshes proactively with a
safety margin, rather than waiting for a live request to fail on 401.

Data path: GET /nmsapi/v1/notams with nmsResponseFormat: AIXM returns
`data.aixm[]` -- a list of individual AIXM 5.1 AIXMBasicMessage XML
documents, confirmed live to be the SAME format the SWIM push path
already consumes (ingest/parsers/aim_parser.py) -- the FAA's own FAQ
confirms this explicitly ("NMS-API NOTAMs are consumed from FNS, but
are updated to AXIM 5.1"). Each entry is fed through
aim_parser.parse_aim_message() + aim_parser.write_aim_notams() --
full reuse of the already-proven parsing, geo-filtering, VIP-detection,
and alert-firing pipeline, not a second parallel implementation.

Query strategy: a single incremental `lastUpdatedDate` call per poll
(nationwide -- the reused write_aim_notams() geo-filter is what scopes
relevance, same as the SWIM push path), rather than looping per-airport
like the old fetcher did against the old API's location-scoped
endpoint. lastUpdatedDate is capped at a 24h lookback by the API itself
(confirmed in the OpenAPI spec) -- this fetcher tracks its own last
successful fetch and uses that (or a 24h fallback on first run / after
a long gap) as the delta boundary, so a normal 5-minute poll cadence
only ever asks for what's actually new.

Polled every 5 minutes by the poller scheduler.
Skips gracefully if no NMS_API_CLIENT_ID/SECRET is configured.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone, timedelta

import requests

from common import config, db
from ingest.parsers.aim_parser import parse_aim_message, write_aim_notams

log = logging.getLogger(__name__)

AUTH_URL = "https://api-nms.aim.faa.gov/v1/auth/token"
NOTAM_URL = "https://api-nms.aim.faa.gov/nmsapi/v1/notams"
FETCH_TIMEOUT = 25  # server enforces its own 30s cap (408 past that)
TOKEN_REFRESH_MARGIN_SECS = 300  # refresh 5min before the real ~30min expiry
LOOKBACK_MAX_HOURS = 24  # API-enforced cap on lastUpdatedDate
# 2026-09-02: a floor computed at exactly 24h00m00s got rejected live
# ("exceeds the 24 hour threshold", error code 7.1) -- the few hundred ms
# between computing `now` here and the server checking it is enough to
# tip over a strict boundary. Small safety margin, not a real behavior
# change (this is only ever the first-run/long-gap fallback boundary).
LOOKBACK_SAFETY_MARGIN_MINS = 5

_token_cache: dict = {"access_token": None, "expires_at": 0.0}


def _client_id() -> str:
    return config.get("NMS_API_CLIENT_ID", "")


def _client_secret() -> str:
    return config.get("NMS_API_CLIENT_SECRET", "")


def _get_bearer_token() -> str | None:
    """Cached OAuth2 client-credentials token, refreshed proactively.
    Returns None if credentials aren't configured or the auth call fails."""
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    client_id, client_secret = _client_id(), _client_secret()
    if not client_id or not client_secret:
        return None

    try:
        resp = requests.post(
            AUTH_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        j = resp.json()
        token = j.get("access_token")
        expires_in = float(j.get("expires_in", 1799))
        if not token:
            log.error("NMS-API auth: no access_token in response: %s", j)
            return None
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = now + expires_in - TOKEN_REFRESH_MARGIN_SECS
        log.info("NMS-API: bearer token refreshed (expires_in=%.0fs)", expires_in)
        return token
    except Exception as e:
        log.error("NMS-API auth failed: %s", e)
        return None


def _last_fetch_since_iso(feed_name: str) -> str:
    """lastUpdatedDate boundary: last successful fetch, capped to the
    API's 24h max lookback. First run / long gap both fall back to the
    24h cap."""
    now = datetime.now(timezone.utc)
    floor = now - timedelta(hours=LOOKBACK_MAX_HOURS) + timedelta(minutes=LOOKBACK_SAFETY_MARGIN_MINS)
    try:
        states = {s["feed_name"]: s for s in db.get_feed_states()}
        row = states.get(feed_name)
        last_ts = row.get("fetched_at") if row and not row.get("error") else None
    except Exception:
        last_ts = None
    since = datetime.fromtimestamp(last_ts, tz=timezone.utc) if last_ts else floor
    since = max(since, floor)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def run() -> dict:
    feed_name = "notam"
    fetched_at = time.time()

    token = _get_bearer_token()
    if not token:
        log.info("NOTAM: NMS_API_CLIENT_ID/SECRET not configured — marking awaiting_credentials")
        db.upsert_feed_skip(feed_name, fetched_at, "awaiting_credentials")
        return {"skipped": True, "reason": "awaiting_credentials"}

    since_iso = _last_fetch_since_iso(feed_name)

    try:
        resp = requests.get(
            NOTAM_URL,
            params={"lastUpdatedDate": since_iso},
            headers={"Authorization": f"Bearer {token}", "nmsResponseFormat": "AIXM"},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        j = resp.json()
        if j.get("status") != "Success":
            raise RuntimeError(f"NMS-API returned status={j.get('status')!r}: {j.get('errors')}")

        aixm_docs = j.get("data", {}).get("aixm", [])
        total_written = 0
        for doc in aixm_docs:
            notams = parse_aim_message(doc.encode("utf-8"))
            total_written += write_aim_notams(notams)

        payload_hash = hashlib.sha256(
            json.dumps({"since": since_iso, "count": len(aixm_docs)}, sort_keys=True).encode()
        ).hexdigest()[:16]
        db.upsert_feed(feed_name, fetched_at, error=None, payload_hash=payload_hash)
        log.info("NOTAM fetch OK — %d AIXM message(s), %d NOTAM(s) written (since %s)",
                 len(aixm_docs), total_written, since_iso)

        cleaned = db.cleanup_expired_notams()
        if cleaned:
            log.info("NOTAM cleanup: removed %d expired/stale rows", cleaned)

        return {"aixm_messages": len(aixm_docs), "written": total_written, "since": since_iso}

    except Exception as e:
        msg = str(e)
        log.error("NOTAM fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
