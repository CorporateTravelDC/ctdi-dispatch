"""
JASDAT fetcher -- Japan AIS Data Tool (JCAB / MLIT)
https://www.jasdat.go.jp/en/

Requires JASDAT_USER and JASDAT_PASS in dispatch-secrets.env. Requires
organizational registration with JCAB (see docs/DATA_SOURCES.md for the
full access process and a ready-to-send request email template).

Japanese equivalent of FAA AIM SWIM: NOTAMs, AIS data, SIGMET/AIRMET,
airspace information for Japanese airspace.

Skips gracefully if no credentials are configured -- same pattern as
poller/fetchers/notam.py. The exact endpoint/response schema below should
be validated once real sandbox access exists.
"""

import logging
import time

import requests

from common import config, db

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 20
JASDAT_BASE_URL = "https://www.jasdat.go.jp/api/v1"


def _user() -> str:
    return config.get("JASDAT_USER", "")


def _pass() -> str:
    return config.get("JASDAT_PASS", "")


def run() -> dict:
    feed_name = "jasdat"
    fetched_at = time.time()

    user, pw = _user(), _pass()
    if not (user and pw):
        log.info("JASDAT: credentials not configured -- marking awaiting_credentials")
        db.upsert_feed_skip(feed_name, fetched_at, "awaiting_credentials")
        return {"skipped": True, "reason": "awaiting_credentials"}

    try:
        # Endpoint path and response shape are placeholders pending a live
        # sandbox response -- JASDAT's public docs describe the access
        # process, not a published OpenAPI/WSDL spec.
        resp = requests.get(
            f"{JASDAT_BASE_URL}/notams",
            auth=(user, pw),
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        records = [
            {"record_type": "notam", "external_ref": str(item.get("id", "")), **item}
            for item in data.get("items", [])
        ]
        n = db.upsert_international_aviation_records("jasdat", records)

        db.upsert_feed(feed_name, fetched_at, error=None)
        log.info("JASDAT fetch OK -- %d record(s)", n)
        return {"count": n}

    except Exception as e:
        msg = str(e)
        log.error("JASDAT fetch FAILED: %s", msg)
        db.upsert_feed(feed_name, fetched_at, error=msg)
        return {"error": msg}
