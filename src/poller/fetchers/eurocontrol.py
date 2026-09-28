"""
EUROCONTROL NM B2B fetcher -- Network Manager Business-to-Business web services
https://www.eurocontrol.int/service/network-manager-business-business-b2b-web-services

Requires EUROCONTROL_NM_B2B_USER, EUROCONTROL_NM_B2B_PASS, and
EUROCONTROL_NM_B2B_CERT_PATH in dispatch-secrets.env. Access is
certificate-based; request via the online form (see docs/DATA_SOURCES.md
for the full process and a ready-to-send access-request email template).

European equivalent of FAA SWIM + ATCSCC combined: flight plans, ATFM flow
measures (CTOT, regulations, MCIs), OPMET, NOTAMs, airspace status.

Skips gracefully if no credentials are configured -- same pattern as
poller/fetchers/notam.py. Once real sandbox access exists, the SOAP/XML
request/response handling below should be validated and adjusted against
an actual NM B2B response (the exact operation name and response schema
can't be finalized from documentation alone).
"""

import logging
import time

import requests

from common import config, db

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 20
NM_B2B_BASE_URL = "https://www.b2b.opsnetwork.eurocontrol.int/B2B_OPS/gateway/spec"


def _user() -> str:
    return config.get("EUROCONTROL_NM_B2B_USER", "")


def _pass() -> str:
    return config.get("EUROCONTROL_NM_B2B_PASS", "")


def _cert_path() -> str:
    return config.get("EUROCONTROL_NM_B2B_CERT_PATH", "")


def run() -> dict:
    feed_name = "eurocontrol"
    fetched_at = time.time()

    user, pw, cert_path = _user(), _pass(), _cert_path()
    if not (user and pw and cert_path):
        log.info("EUROCONTROL: credentials not configured -- marking awaiting_credentials")
        db.upsert_feed_skip(feed_name, fetched_at, "awaiting_credentials")
        return {"skipped": True, "reason": "awaiting_credentials"}

    try:
        # NM B2B is SOAP/XML over a client-certificate-authenticated HTTPS
        # connection. The exact operation (e.g. AIRAC/FlightServices) and its
        # response envelope depend on the service granted during onboarding --
        # placeholder request kept minimal and defensive until a live sandbox
        # response is available to build the real parser against.
        resp = requests.get(
            NM_B2B_BASE_URL,
            auth=(user, pw),
            cert=cert_path,
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()

        records: list[dict] = []  # TODO: parse resp.content once schema is confirmed
        n = db.upsert_international_aviation_records("eurocontrol", records)

        db.upsert_feed(feed_name, fetched_at, error=None)
        log.info("EUROCONTROL fetch OK -- %d record(s)", n)
        return {"count": n}

    except Exception as e:
        msg = str(e)
        log.error("EUROCONTROL fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
