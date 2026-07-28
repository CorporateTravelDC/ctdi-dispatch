"""
shared/rss_catalog.py -- single source of truth for the dispatch RSS feed
pool: the built-in category catalog shown in the PWA Intel tab, plus any
operator-added custom feeds persisted via that same tab.

Split out of runner/main.py 2026-07-28 so second_brain_rss (a poller
skill, separate container from runner) can churn on the exact same feed
pool instead of a disconnected, manually-maintained list -- operator
request: "seed second-brain-rss with the same feeds in the FAA UTM bits
and the current intel tile ... so it's churning on the existing intel and
anything added to the dispatch's rss pool" going forward. Since both
runner and second_brain_rss now import from here, any feed added via the
PWA (user_rss_feeds.json) or added to the built-in catalog below
automatically flows into second-brain ingestion too -- no separate seed
step needed ever again.
"""
import json
import logging

log = logging.getLogger(__name__)

_RSS_CATALOG: dict[str, list[dict]] = {
    "corporate_intel": [
        {"name": "Skift",                   "url": "https://skift.com/feed/"},
        {"name": "Federal News Network",    "url": "https://federalnewsnetwork.com/feed/"},
        {"name": "The Air Current",         "url": "https://theaircurrent.com/feed/"},
    ],
    "marketing_intel": [
        {"name": "Robb Report Travel",      "url": "https://robbreport.com/category/travel/feed/"},
        {"name": "Forbes Travel Guide",     "url": "https://stories.forbestravelguide.com/feed/"},
        {"name": "Lodging Magazine",        "url": "https://lodgingmagazine.com/feed/"},
    ],
    "travel_trends": [
        {"name": "The Points Guy",          "url": "https://thepointsguy.com/feed/"},
        {"name": "Condé Nast Traveler",     "url": "https://www.cntraveler.com/feed/rss"},
        {"name": "One Mile at a Time",      "url": "https://onemileatatime.com/feed/"},
    ],
    "dc_area": [
        {"name": "WTOP Traffic & Transit",  "url": "https://wtop.com/category/traffic/feed/"},
        {"name": "Washingtonian",           "url": "https://www.washingtonian.com/feed/"},
        {"name": "ARLnow",                  "url": "https://www.arlnow.com/feed/"},
    ],
    "aviation": [
        {"name": "AviationSource",          "url": "https://aviationsourcenews.com/feed/"},
        {"name": "AOPA News",               "url": "https://www.aopa.org/news-and-media/all-news/rss"},
        {"name": "Cranky Flier",            "url": "https://crankyflier.com/feed/"},
    ],
    # Added 2026-07-23 per operator request (vertiport/eVTOL/Part 108
    # research chat). Only URLs independently verified to return real
    # RSS/Atom XML from this Pi are included here -- Electric VTOL News
    # (evtol.news) and AIN FutureFlight both publish <link rel="alternate"
    # rss+xml> tags that 404/serve HTML instead of feed content when
    # fetched externally (bot-gated or broken), and Federal Register's
    # search.rss endpoint 500s regardless of params/UA from this network.
    # Don't add those three without re-verifying from a different network
    # first -- see hex_only_sweep_authority-adjacent lesson on unverified
    # feeds/parsers (feedback_swim_parser_verification memory).
    "advanced_air_mobility": [
        {"name": "Urban Air Mobility News",  "url": "https://urbanairmobilitynews.com/feed/"},
        {"name": "UASWeekly",                "url": "https://uasweekly.com/feed/"},
        {"name": "FAA News",                 "url": "https://www.faa.gov/rss.xml"},
    ],
}

USER_FEEDS_PATH = "/var/lib/corporatetraveldc/user_rss_feeds.json"


def load_user_feeds() -> list[dict]:
    try:
        with open(USER_FEEDS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("user_rss_feeds: load failed: %s", e)
        return []


def save_user_feeds(feeds: list[dict]) -> None:
    try:
        with open(USER_FEEDS_PATH, "w") as f:
            json.dump(feeds, f, indent=2)
    except Exception as e:
        log.error("user_rss_feeds: save failed: %s", e)


def all_feed_urls() -> list[str]:
    """Every feed URL in the dispatch RSS pool -- built-in catalog (all
    categories, including advanced_air_mobility / the FAA UTM feeds) plus
    operator-added custom feeds -- deduplicated, stable order. This is
    "everything the PWA Intel tab currently shows, plus anything added to
    it going forward" flattened to a URL list, for consumers (like
    second_brain_rss) that just want to ingest the whole pool rather than
    render it by category.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for feeds in _RSS_CATALOG.values():
        for feed in feeds:
            u = feed.get("url")
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
    for feed in load_user_feeds():
        u = feed.get("url")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls
