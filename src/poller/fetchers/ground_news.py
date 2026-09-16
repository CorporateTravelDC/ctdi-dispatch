"""
Ground News fetcher -- operator's own personalized bias/coverage feed.
https://ground.news/

Requires GROUND_NEWS_SESSION_TOKEN (preferred) or GROUND_NEWS_EMAIL +
GROUND_NEWS_PASSWORD in dispatch-secrets.env. Ground News has no published
public API -- see docs/DATA_SOURCES.md and docs/GROUND_NEWS_ACCESS_REQUEST.md
for the access process and the sign-off request this integration is pending
on before it is enabled by default.

Credential model: exactly one operator's own Ground News account per
deployment -- not a shared scraping-as-a-service, not resold, not
redistributed. Same posture as this repo's FAA SWIM/NWWS-OI credential
handling (see docs/DATA_SOURCES.md). Skips gracefully if no credentials
are configured, same pattern as poller/fetchers/notam.py,
poller/fetchers/eurocontrol.py, and poller/fetchers/jasdat.py.

The real ground.news request/response contract is a placeholder pending
Ground News's sign-off (see common/ground_news_client.py's module
docstring) -- this fetcher is written and tested against that interface
so wiring in a confirmed endpoint later is a one-module change, not a
rewrite of the feed-state / storage plumbing below.
"""

import logging
import time

import requests

from common import config, db
from common.ground_news_client import GroundNewsAuthError, credentials_configured, fetch_my_feed

log = logging.getLogger(__name__)

FEED_NAME = "ground_news"


def run() -> dict:
    fetched_at = time.time()

    if not credentials_configured():
        log.info("Ground News: credentials not configured -- marking awaiting_credentials")
        db.upsert_feed_skip(FEED_NAME, fetched_at, "awaiting_credentials")
        return {"skipped": True, "reason": "awaiting_credentials"}

    try:
        items = fetch_my_feed(limit=100)
        n = db.upsert_ground_news_items(items)

        db.upsert_feed(FEED_NAME, fetched_at, error=None)
        log.info("Ground News fetch OK -- %d item(s)", n)
        return {"count": n}

    except GroundNewsAuthError as e:
        # Distinct from a plain transport failure -- surfaces as
        # awaiting_credentials/auth-pending rather than a generic error,
        # since operators seeing this before Ground News sign-off should
        # not read it as "your credentials are wrong."
        msg = str(e)
        log.info("Ground News: not yet available -- %s", msg)
        db.upsert_feed_skip(FEED_NAME, fetched_at, msg)
        return {"skipped": True, "reason": msg}

    except (requests.RequestException, ValueError) as e:
        msg = str(e)
        log.error("Ground News fetch FAILED: %s", msg)
        db.upsert_feed(FEED_NAME, fetched_at, error=msg)
        return {"error": msg}
