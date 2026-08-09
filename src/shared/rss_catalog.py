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
import re

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
    # Added 2026-08-07 per operator request (cross-link auto-track feature --
    # gig-platform moves and legal/policy/venture signals came up as explicit
    # examples of "first-mover" content worth tracking as its own category,
    # not just mentions inside AAM/aviation feeds). Only one feed verified so
    # far -- most gig-economy commentary lives on one-off Substack/Medium
    # posts rather than an ongoing trade outlet, or the outlet blocks
    # scraping/bot UAs outright (elitetraveler.com, techcrunch.com tag feeds,
    # several rideshare-adjacent blogs all failed live verification from this
    # Pi 2026-08-07). Add more here as they're independently verified --
    # don't add an unverified URL, see the 2026-07-23 comment above.
    "gig_economy": [
        {"name": "The Rideshare Guy",         "url": "https://therideshareguy.com/feed/"},
    ],
    # Added 2026-08-07, same request -- concierge/luxury/private-travel deal
    # sourcing is directly relevant to the operator's own executive-services
    # business line, distinct from marketing_intel's hospitality-MARKETING
    # framing (Robb Report Travel there is the trade-press angle; this is
    # the deal/destination-sourcing angle). Same one-feed caveat as above --
    # most luxury-travel "concierge" sites (Departures, Elite Traveler,
    # Quintessentially, PrivateFly, private-island brokers) either have no
    # discoverable feed or block scraping; only robbreport.com's general
    # feed (broader than the travel-only one already in marketing_intel)
    # verified live.
    "concierge_luxury_travel": [
        {"name": "Robb Report",               "url": "https://robbreport.com/feed/"},
    ],
    # Added 2026-08-07 per operator request -- rail/marine industry
    # "first-mover" signal worth tracking (shipbuilder output shifts, new
    # shipyard openings, next-gen rail program status e.g. Acela rebuild).
    # Combined into one category rather than split -- total feed volume
    # for either half alone is thin, matches gig_economy's precedent of
    # starting small and adding more once verified.
    "trains_yachts": [
        {"name": "Railway-News",              "url": "https://railway-news.com/feed/"},
        {"name": "Railway Gazette",            "url": "https://www.railwaygazette.com/rss"},
        {"name": "Baird Maritime (Shipbuilding)", "url": "https://www.bairdmaritime.com/feed"},
        {"name": "Trade Only Today",           "url": "https://tradeonlytoday.com/feed"},
    ],
    # Added 2026-08-07 per operator request -- single category, multiple
    # signal types (folds in counter-UAS, previously only an incidental
    # AAM-adjacent finding, plus two new angles): training/certification
    # opportunities (trauma/critical care, security-driving courses) and
    # threat intelligence aimed at service providers specifically (cyber
    # threats, targeted-attack-vector trends). See entity_tracking.py's
    # SIGNAL_TYPES for how these are distinguished at the extraction
    # layer -- same category, tagged differently, not split categories.
    "executive_protection": [
        {"name": "Krebs on Security",          "url": "https://krebsonsecurity.com/feed/"},
        {"name": "Security Magazine",          "url": "https://www.securitymagazine.com/rss/topic/2236-security-news"},
    ],
    # Added 2026-08-07 per operator request. Category is OSINT / cybersecurity
    # (video feeds), for future work; pentest is a NESTED topic/signal within it,
    # NOT the category name (operator correction 2026-08-07). Channel-level
    # YouTube ingestion via the live RSS-Bridge YoutubeBridge resolver
    # (rss-bridge.container, bound 100.x.x.x:3001 -- reachable from the fetch
    # containers, verified HTTP 200 from inside the runner). First feed: Black
    # Hat conference talks; channel confirmed via YouTube oEmbed
    # (id UCJ6q9Ie29ajGqKApbLqfBOg, @BlackHatOfficialYT). Add DEF CON / others
    # as independently verified.
    "osint_cybersecurity_video": [
        {"name": "Black Hat (YouTube)",
         "url": "http://100.x.x.x:3001/?action=display&bridge=Youtube&context=By%20channel%20id&c=UCJ6q9Ie29ajGqKApbLqfBOg&format=Atom"},
    ],
}

USER_FEEDS_PATH = "/var/lib/corporatetraveldc/user_rss_feeds.json"
USER_CATEGORIES_PATH = "/var/lib/corporatetraveldc/user_rss_categories.json"


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


# ── User-defined categories + multi-operator visibility model ───────────────
# Added 2026-08-02 per operator direction: "Add Category" parallel to Add
# Feed, plus a department/multi-operator model -- "Anyone within a
# department or division is able to see the same feeds if they are opted
# in, or create a department-wide category ... Everyone could have, on an
# admin token or username basis, their own independent feeds ... not flood
# everybody else's feed."
#
# Both categories (this file) and feeds (user_rss_feeds.json above) share
# the same three-value `scope` field:
#   "company"    -- visible to everyone, including anonymous/no-token
#                   callers. Default for anything with no scope set at all
#                   (backward compat with every category/feed that existed
#                   before this change -- nothing already saved silently
#                   becomes hidden).
#   "department" -- visible only to callers whose resolved identity
#                   (auth/auth.py resolve_identity(), via web/main.py's
#                   /api/v1/whoami-token) has a `department` matching this
#                   entry's `department` field.
#   "personal"   -- visible only to the caller whose token_prefix matches
#                   this entry's `owner` field.
# `owner` is always a token_prefix (e.g. "ctdc_operator_"), never a raw token
# or hashed value -- token_prefix is already the codebase's existing
# display/audit identifier (see auth/auth.py's _token_prefix()), so this
# reuses that rather than inventing a second identity string.
def load_user_categories() -> list[dict]:
    try:
        with open(USER_CATEGORIES_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("user_rss_categories: load failed: %s", e)
        return []


def save_user_categories(categories: list[dict]) -> None:
    try:
        with open(USER_CATEGORIES_PATH, "w") as f:
            json.dump(categories, f, indent=2)
    except Exception as e:
        log.error("user_rss_categories: save failed: %s", e)


# ── Alias-aware duplicate-category guard (added 2026-08-07) ──────────────────
# Prevents the bug where "AAM" was created as a second, EMPTY category while the
# built-in "advanced_air_mobility" (display "Advanced Air Mobility") already
# existed -- an exact-name check missed the shorthand. This maps known
# shorthand / industry jargon / common speech-to-text mishears to ONE canonical
# category so neither the operator "Add Category" path nor the automated
# promotion path can silently spawn a concept that already exists.
#
# To extend a cluster, add normalized surface forms (see _normalize_label).
# STT-typo precedent (recorded in AI-memory): "eVTOL"/"eSTOL" mis-transcribes to
# "EV toll(s)" -- included explicitly so a voice-typed variant still matches.
_CONCEPT_ALIASES: dict[str, set[str]] = {
    "advanced_air_mobility": {
        "aam", "advancedairmobility",
        "evtol", "estol", "evtolestol", "evtols",
        "evtoll", "evtolls",              # STT mishear of eVTOL -> "EV toll(s)"
        "uam", "urbanairmobility",
        "vertiport", "vertiports", "airtaxi", "airtaxis",
    },
    "aviation": {"aviation", "aero", "airtravel", "airlineindustry", "airlines"},
    "gig_economy": {"gigeconomy", "gig", "rideshare", "ridesharing", "gigwork"},
    "concierge_luxury_travel": {
        "conciergeluxurytravel", "conciergetravel", "luxurytravel", "concierge"},
    "trains_yachts": {"trainsyachts", "rail", "railways", "yacht", "yachts", "marine"},
    "executive_protection": {
        "executiveprotection", "ep", "closeprotection", "protectivedetail",
        "counteruas", "counteruav", "cuas"},
    "osint_cybersecurity_video": {
        "osintcybersecurityvideo", "osintcybersecurity", "osint",
        "cybersecurityvideo", "cybersecurity", "cybersec", "infosec",
        # pentest is a NESTED signal within this category -- kept as an alias so
        # a future "pentest" category attempt folds in here rather than splitting
        "pentest", "pentesting", "penetrationtesting", "offsec", "redteam",
        "blackhat", "defcon", "securityvideo",
        # operator STT variant "paint (cyber) security"
        "paintcybersecurity", "paintsecurityvideo", "paintsecurity"},
}


def _normalize_label(s: str) -> str:
    """Lowercase and strip everything but a-z0-9, so 'Advanced Air Mobility',
    'advanced_air_mobility', 'AAM', and 'A.A.M.' all collapse to a comparable
    key. Deliberately aggressive so spacing/punctuation/casing never hides a dup."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def canonical_concept(label: str) -> str | None:
    """Return the canonical category id a label refers to -- via an exact
    (normalized) built-in catalog key OR one of the alias clusters above --
    or None if it names no known concept."""
    norm = _normalize_label(label)
    if not norm:
        return None
    for key in _RSS_CATALOG:
        if _normalize_label(key) == norm:
            return key
    for canonical, aliases in _CONCEPT_ALIASES.items():
        if norm in aliases:
            return canonical
    return None


def find_existing_category(label: str, user_categories: list[dict] | None = None) -> dict | None:
    """If `label` names a category (or the same concept as one) that ALREADY
    exists -- built-in or user-created, matched by exact name, id, or known
    alias/shorthand/STT-variant -- return that existing category as
    {id, label, source}. Otherwise None. Callers use this to REFUSE creating a
    duplicate. Symmetric: a new 'AAM' matches existing 'advanced_air_mobility'
    and vice-versa; 'eVTOL' / 'EV tolls' match it too."""
    norm = _normalize_label(label)
    if not norm:
        return None
    if user_categories is None:
        user_categories = load_user_categories()
    concept = canonical_concept(label)

    # 1) built-in catalog -- exact-normalized key, or shared concept
    for key in _RSS_CATALOG:
        if _normalize_label(key) == norm or (concept is not None and concept == key):
            return {"id": key, "label": key.replace("_", " ").title(), "source": "builtin"}

    # 2) user categories -- exact-normalized id/label, or shared concept
    for c in user_categories:
        cid = c.get("id", "")
        clabel = c.get("label", "")
        if norm in (_normalize_label(cid), _normalize_label(clabel)):
            return {"id": cid, "label": clabel, "source": "user"}
        if concept is not None and canonical_concept(clabel) == concept:
            return {"id": cid, "label": clabel, "source": "user"}
    return None


def visible_to(entry: dict, identity: dict) -> bool:
    """Shared visibility rule for both user categories and user feeds.
    identity is the dict shape returned by auth.auth.resolve_identity() /
    GET /api/v1/whoami-token: {tier, user_label, department, token_prefix}.
    Fails open to visible for an unrecognized scope value rather than
    silently hiding an entry someone can't explain the disappearance of."""
    scope = entry.get("scope") or "company"
    if scope == "company":
        return True
    if scope == "department":
        dept = identity.get("department")
        return bool(dept) and dept == entry.get("department")
    if scope == "personal":
        prefix = identity.get("token_prefix")
        return bool(prefix) and prefix == entry.get("owner")
    return True


def list_all_categories(identity: dict) -> list[dict]:
    """Built-in catalog categories (always company-scope) + user-created
    categories filtered by visibility for this identity. Built-ins get a
    human label derived from their key since _RSS_CATALOG only ever had
    keys, not display labels, before this."""
    result = [
        {"id": key, "label": key.replace("_", " ").title(), "scope": "company", "builtin": True}
        for key in _RSS_CATALOG
    ]
    for cat in load_user_categories():
        if visible_to(cat, identity):
            result.append(cat)
    return result


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
