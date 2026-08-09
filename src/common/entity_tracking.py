"""
common.entity_tracking -- cross-link/recurring-source detection, wired to
actually ACT on findings (auto-promote a tracked source, or route to a
human-reviewable holding area) instead of only reporting them in chat.

2026-08-06/07, operator directives (paraphrased across several messages):

1. Confirmed nothing before this persisted a byte of cross-link data --
   companies flagged earlier (Joby, Archer, MagniX, etc.) came from
   reading fetched RSS content directly in conversation. Build the
   "act on it" logic.

2. Exact threshold, operator's own words -- an entity auto-promotes when
   EITHER:
     - it recurs >=5 times across a rolling 1-week window, OR
     - it's corroborated by 2+ distinct existing feeds/sources (not just
       repeated within one feed)
   Whichever hits first fires the auto-promote.

3. Placement: nest under an existing category where that fits (e.g. an
   AAM company nests under advanced_air_mobility) rather than spawning a
   new top-level category. New top-level categories are a bigger
   structural decision than adding one feed -- this module NEVER creates
   one automatically; that always routes through the novel-findings
   bucket for a human to decide (see docs/AAM_WATCH_STATUS.md-style
   reasoning: safe/reversible actions auto-apply, structural ones don't).

4. Novel-findings holding area, modeled on second-brain's 00-Inbox
   staging pattern: below-threshold findings AND at-or-above-threshold
   "first-mover" findings land here instead of auto-promoting silently.
   Written as real vault notes under 00-Inbox/cross-link-findings/ and
   indexed via second_brain.index_db.index_note() so index_db.py
   --search finds them -- visible and queryable, not a log line.

5. First-mover definition (operator's own examples -- illustrative, not
   exhaustive, extrapolate the spirit):
     - a new edge buy/JV between a BESS supplier and an eVTOL/eSTOL
       provider (a novel RELATIONSHIP TYPE, not just a new company)
     - notable content, or notable ABSENCE of expected content, in an
       earnings call/release -- deliberate embargo (withholding/delaying
       on purpose) is a stronger signal than ordinary silence
     - a gig platform (Uber etc.) signaling a new venture, policy
       tightening, legal proceedings, or a stock/earnings move -- this
       is what put gig-economy in scope as its own watched category
     - concierge/luxury/adventure-travel signals: new destinations,
       private retreats, off-market listings -- this put
       concierge/luxury travel in scope as its own watched category too
   Two deterministic first-mover triggers, no LLM judgment needed:
     - entity's first-ever appearance in this tracker (first_seen ==
       today) is always first-mover, regardless of threshold.
   One LLM-assisted trigger:
     - the extraction call tags a specific mention with a non-"routine"
       signal_type (see SIGNAL_TYPES below) -- an already-known entity
       doing something novel is still first-mover for THAT finding, even
       though the entity itself isn't new.
   A routine recurring mention of an already-known entity (more coverage
   of an already-known player) auto-promotes cleanly if it crosses
   threshold -- only novel/first-mover findings get routed to review.

6. Backlink source discovery: for an entity with no dedicated source
   currently tracked anywhere, don't just tag mentions inside existing
   feeds -- try to find and add that entity's own newsroom/press/blog
   feed as a new tracked source. No live web-search tool is available to
   an unattended container skill, so this asks the same local Ollama
   model for its best-guess official domain (general knowledge, not a
   live lookup), then deterministically probes a fixed list of common
   feed-path conventions against that domain and verifies each candidate
   actually parses as real RSS/Atom XML before ever persisting it --
   same verification bar _RSS_CATALOG's own 2026-07-23 comment already
   holds every feed to. A wrong domain guess or a site with no
   discoverable feed just means discovery fails cleanly (logged, entity
   still gets tracked via mentions-in-existing-feeds either way) -- never
   a fabricated or unverified URL persisted.

7. Provenance labeling: anything sourced from a feed THIS module
   discovered and added must be visibly marked as such, both in daily/
   weekly brief output (see rss_retrieval.format_citations()) and in the
   novel-findings/entity vault notes -- never blended in indistinguishably
   with feeds the operator curated by hand.

8. Justification field: every flagged signal carries a `justification`
   string explaining why it was flagged -- built into the data model
   generally (not just for the absence/embargo case where the "why"
   matters most), even though for a routine "new JV announced" hit the
   justification is closer to restating the headline.

Vault layout used:
  corporatetraveldc/00-Inbox/cross-link-findings/<category>-<slug>.md
    -- novel-findings bucket, one stable note per entity-in-category,
       updated (not duplicated) as new evidence arrives. PARA "00-Inbox"
       is exactly the staging-not-yet-promoted folder second-brain
       already uses for this purpose.
  corporatetraveldc/03-Entities/<category>-<slug>.md
    -- written/updated on promotion (auto or eventually manual). PARA
       "03-Entities" is already the recognized named-entity folder in
       second_brain.index_db._FOLDER_CATEGORY.
"""
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import requests

from common.llm import generate as llm_generate
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from shared.rss_catalog import (
    find_existing_category, load_user_categories, load_user_feeds,
    save_user_categories, save_user_feeds,
)

log = logging.getLogger(__name__)

STATE_PATH = "/var/lib/corporatetraveldc/rss_entity_tracker.json"

ROLLING_WINDOW_DAYS = 7
RECURRENCE_THRESHOLD = 5
DISTINCT_FEED_THRESHOLD = 2

EXTRACTION_MODEL = "corporatetraveldc-pi5-chat"
EXTRACTION_TIMEOUT = 90
DOMAIN_GUESS_TIMEOUT = 30

# Non-"routine" signal types route a THRESHOLD-CROSSING finding to the
# novel-findings bucket instead of silent auto-promotion, per operator
# directive #5 above. "routine" is the only type that can auto-promote
# once threshold is met; everything else always gets human eyes first.
SIGNAL_TYPES = {
    "routine", "first_mover_jv", "absence_notable", "absence_embargo",
    "new_venture", "policy_change", "legal", "market_signal", "other",
    # Added 2026-08-07 for executive_protection: a single category with
    # multiple signal types (not split categories) -- training_cert_opportunity
    # for trauma/critical-care and security-driving courses/certifications
    # becoming available, threat_intel_service_provider for cyber threats
    # and targeted-attack-vector trends aimed at service providers like
    # this business specifically (not generic consumer-facing threats).
    "training_cert_opportunity", "threat_intel_service_provider",
}
_NOVEL_SIGNAL_TYPES = SIGNAL_TYPES - {"routine"}

_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9 .&'/\-]{1,60}?)\s*\|\s*([0-9,\s]+)\s*\|\s*"
    r"([a-z_]+)\s*\|\s*(.*)$"
)

# Common feed-path conventions to probe for backlink source discovery,
# in likelihood order -- WordPress (/feed/) is by far the most common
# CMS for a company newsroom/blog, hence first.
_FEED_PATH_CANDIDATES = [
    "/feed/", "/feed", "/rss/", "/rss.xml", "/blog/feed/", "/blog/feed",
    "/news/feed/", "/press/feed/", "/newsroom/feed/", "/atom.xml",
]

# ── Citable / Uber Series tagging (2026-08-07) ────────────────────────────
# Operator-approved proposal: gig_economy findings get a citable field
# (this pipeline's output is fair game to source/cite from) plus a check
# against the operator's actual "Uber Series" article drafts (Nextcloud
# Notes/Uber Series/Article 1-6.md, WebDAV, NOT this git repo -- confirmed
# 2026-08-07 after initially looking in the wrong place). Read all six
# articles directly to build this keyword list from their real content,
# not a guess -- topics: Article 1 (surge pricing / take-rate on the
# operator's own 866 trips), Article 2 (per-rider price discrimination,
# Consumer Reports study), Article 3 (take-rate accounting / insurance-
# cost exclusion / Len Sherman research / driver-transparency legal
# suppression), Article 4 (driver-app permissions / device surveillance /
# accessibility access), Article 5 (captive insurers Aleka/Pacific
# Valley, robotaxi investment, liability caps), Article 6 (Journey Ads /
# Lyft Media advertising business, App Tracking Transparency).
#
# Lexical keyword matching, same honest non-embedding approach as
# rss_retrieval.py -- no embedding model/vector store exists on this box
# (see that module's own docstring for the full reasoning, unchanged
# here).
UBER_SERIES_TOPICS: dict[int, list[str]] = {
    1: ["surge pricing", "surge", "take rate", "take-rate", "driver pay", "fare split"],
    2: ["price discrimination", "personalized pricing", "same route", "quote gap",
        "consumer reports", "fake discount"],
    3: ["take rate", "take-rate", "upfront pricing", "commercial insurance",
        "driver transparency", "fare split", "len sherman"],
    4: ["driver app", "permission", "accessibility access", "device scanning",
        "screen watch", "surveillance", "bluetooth scanning"],
    5: ["captive insurer", "aleka", "pacific valley", "liability cap",
        "robotaxi", "insurance"],
    6: ["advertising", "ad revenue", "journey ads", "lyft media", "programmatic",
        "app tracking transparency", "ad tech", "rider graph"],
}


def check_uber_series_relevance(text: str) -> list[int]:
    """Returns the Uber Series article numbers (1-6) whose real content
    topics this text overlaps with, via simple keyword matching. Empty
    list if nothing overlaps -- most gig_economy findings won't, and
    that's fine, this is a targeted flag not a blanket tag."""
    lowered = text.lower()
    return [n for n, keywords in UBER_SERIES_TOPICS.items()
            if any(kw in lowered for kw in keywords)]


def _citability_fields(category: str, entry: dict) -> dict:
    """citable is a blanket flag for the whole gig_economy category (per
    operator directive: this pipeline's gig-economy output is fair game
    to source/cite from generally) -- uber_series_articles is the
    targeted overlap check against the operator's actual Uber Series
    drafts, only meaningful within that same category."""
    if category != "gig_economy":
        return {"citable": False, "uber_series_articles": []}
    text = entry["display"] + " " + " ".join(
        f"{m['item_title']} {m['justification']}" for m in entry["mentions"]
    )
    return {"citable": True, "uber_series_articles": check_uber_series_relevance(text)}


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("entity_tracking: state load failed, starting fresh: %s", e)
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error("entity_tracking: state save failed: %s", e)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "entity"


# ── Extraction ────────────────────────────────────────────────────────────

def extract_entities(items: list[dict]) -> dict[str, dict]:
    """Ask Ollama which companies/organizations/named systems are mentioned
    in this batch of RSS items, tagged with a signal_type + short
    justification per entity. Returns {} on any failure -- non-fatal side
    pipeline, must never take down the main brief.

    Return shape: {entity_name: {"indices": [1,3], "signal_type": "...",
    "justification": "..."}}
    """
    if not items:
        return {}

    numbered = "\n".join(
        f"{i+1}. {it.get('title','')} -- {(it.get('summary') or '')[:200]} "
        f"[source: {it.get('source') or it.get('feed') or 'unknown'}]"
        for i, it in enumerate(items)
    )
    signal_list = ", ".join(sorted(SIGNAL_TYPES))
    prompt = (
        "List every specific company, organization, or named aircraft/system "
        "mentioned in the numbered items below. Skip generic terms (FAA, "
        "NTSB, DOT, government agencies, \"the industry\", airport codes).\n\n"
        "One line per entity, EXACTLY this format (pipe-separated, no extra "
        "spaces around pipes beyond what's shown):\n"
        "EntityName | 1,3,7 | signal_type | short justification\n\n"
        f"signal_type must be exactly one of: {signal_list}\n"
        "Use \"routine\" unless something genuinely stands out: a brand-new "
        "kind of partnership/JV, a notable absence or deliberate embargo of "
        "expected news, a new venture/policy/legal move, or a clear market "
        "signal. training_cert_opportunity is for a new or newly-available "
        "training/certification course (trauma/critical care, security-"
        "driving, close protection) someone in this field could actually "
        "enroll in. threat_intel_service_provider is for a cyber threat or "
        "targeted-attack-vector trend aimed at service providers/small "
        "businesses specifically, not generic consumer-facing threats. "
        "justification is one short sentence on WHY you flagged it that "
        "specific way -- for \"routine\" this can just restate the gist.\n\n"
        "If nothing qualifies, output nothing.\n\n" + numbered
    )

    try:
        result = llm_generate(
            system=None, prompt=prompt,
            ollama_model=EXTRACTION_MODEL, max_tokens=500, temperature=0.1,
            timeout=EXTRACTION_TIMEOUT,
            allow_anthropic=False, max_retries=0,
        )
    except Exception as e:
        log.warning("entity_tracking: extraction call failed: %s", e)
        return {}

    if not result:
        return {}

    hits: dict[str, dict] = {}
    for line in result.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        if not name:
            continue
        try:
            indices = [int(n) for n in m.group(2).split(",") if n.strip()]
        except ValueError:
            continue
        indices = [n for n in indices if 1 <= n <= len(items)]
        if not indices:
            continue
        signal_type = m.group(3).strip().lower()
        if signal_type not in SIGNAL_TYPES:
            signal_type = "other"
        justification = m.group(4).strip() or "recurring mention, no notable pattern flagged"
        hits[name] = {"indices": indices, "signal_type": signal_type, "justification": justification}
    return hits


# ── Recording + trigger evaluation ──────────────────────────────────────

def record_mentions(category: str, entity_hits: dict[str, dict], items: list[dict], today: str) -> dict:
    """Persist today's extraction results. Returns the updated per-entity
    state dict for this category (not just newly-changed ones) so callers
    can evaluate triggers immediately without a second load_state() round
    trip."""
    state = load_state()
    cat_state = state.setdefault(category, {})

    for name, hit in entity_hits.items():
        key = name.lower().strip()
        entry = cat_state.get(key)
        if entry is None:
            entry = {
                "display": name,
                "mentions": [],
                "first_seen": today,
                "last_seen": today,
                "status": "new",
                "promoted": False,
                "promoted_at": None,
                "promotion_trigger": None,
                "novel_review_reasons": [],
                "discovered_source_url": None,
                "discovered_source_verified": False,
                "cross_category_matches": [],
                "promotion_history": [],
            }
        entry["display"] = name
        entry["last_seen"] = today
        for idx in hit["indices"]:
            it = items[idx - 1]
            entry["mentions"].append({
                "date": today,
                "feed_source": it.get("source") or it.get("feed") or "unknown",
                "item_title": (it.get("title") or "")[:200],
                "signal_type": hit["signal_type"],
                "justification": hit["justification"],
            })
        cat_state[key] = entry

    state[category] = cat_state
    save_state(state)
    return cat_state


def _mentions_in_window(entry: dict, today: date, window_days: int = ROLLING_WINDOW_DAYS) -> list[dict]:
    cutoff = (today - timedelta(days=window_days - 1)).isoformat()
    return [m for m in entry["mentions"] if m["date"] >= cutoff]


def evaluate_trigger(entry: dict, today: date, category: str | None = None) -> str | None:
    """Returns the trigger name that fired ("5x_week" / "2_distinct_feeds"),
    or None if neither threshold is met yet. Checked in the order given --
    "whichever hits first" per operator directive, though since this is
    evaluated as a point-in-time snapshot rather than a true race, either
    condition being true fires promotion; the label just says which one.

    2026-08-07: category is optional (defaults to no adjustment, keeping
    every existing caller that doesn't pass it working unchanged) -- when
    given, Tier 2's learned per-(category, source_type, trigger)
    multiplier (see effective_threshold_multiplier) tightens the raw
    threshold for combos with a track record of fruitless promotions.
    source_type is hardcoded "auto_threshold" here since this function is
    only ever called from the RSS-threshold auto-promotion path."""
    window = _mentions_in_window(entry, today)
    recurrence_threshold = RECURRENCE_THRESHOLD
    feed_threshold = DISTINCT_FEED_THRESHOLD
    if category:
        recurrence_threshold = round(RECURRENCE_THRESHOLD *
            effective_threshold_multiplier(category, "auto_threshold", "5x_week"))
        feed_threshold = round(DISTINCT_FEED_THRESHOLD *
            effective_threshold_multiplier(category, "auto_threshold", "2_distinct_feeds"))
    if len(window) >= recurrence_threshold:
        return "5x_week"
    if len({m["feed_source"] for m in window}) >= feed_threshold:
        return "2_distinct_feeds"
    return None


def _is_first_mover(entry: dict, today_str: str) -> tuple[bool, list[str]]:
    """Returns (is_first_mover, reasons). Two triggers per operator
    directive #5: brand-new entity (first_seen == today, i.e. this run is
    the first time it's EVER been seen), or any mention this run carrying
    a non-"routine" signal_type -- an already-known entity doing something
    novel is still first-mover for that specific finding."""
    reasons = []
    if entry["first_seen"] == today_str:
        reasons.append("first-ever appearance of this entity in the tracker")
    for m in entry["mentions"]:
        if m["date"] == today_str and m["signal_type"] in _NOVEL_SIGNAL_TYPES:
            reasons.append(f'signal_type={m["signal_type"]}: {m["justification"]}')
    return (len(reasons) > 0, reasons)


# ── Backlink source discovery ────────────────────────────────────────────

def _guess_domain(entity_name: str) -> str | None:
    """Ask the local model for its best-guess official domain for this
    entity. General-knowledge guess, NOT a live lookup -- no web-search
    tool is available to an unattended container skill. Wrong guesses
    just make discovery fail cleanly downstream (verified against real
    HTTP+XML before anything is ever persisted)."""
    prompt = (
        f'What is the official website domain for the company/organization '
        f'"{entity_name}"? Reply with ONLY the bare domain, e.g. "example.com" '
        f'-- no scheme, no path, no explanation. If you are not confident, '
        f'reply exactly "unknown".'
    )
    try:
        result = llm_generate(
            system=None, prompt=prompt,
            ollama_model=EXTRACTION_MODEL, max_tokens=20, temperature=0.0,
            timeout=DOMAIN_GUESS_TIMEOUT, allow_anthropic=False, max_retries=0,
        )
    except Exception as e:
        log.warning("entity_tracking: domain guess call failed for %r: %s", entity_name, e)
        return None
    if not result:
        return None
    domain = result.strip().strip(".").lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0]
    if domain == "unknown" or not re.match(r"^[a-z0-9.\-]+\.[a-z]{2,}$", domain):
        return None
    return domain


def _verify_feed(url: str) -> bool:
    """Fetch url and confirm it parses as real RSS/Atom XML with at least
    one item -- same bar every feed in _RSS_CATALOG is already held to."""
    try:
        r = requests.get(
            url, timeout=10,
            headers={"User-Agent": "corporatetraveldc-dispatch/1.0"},
        )
        if r.status_code != 200:
            return False
        root = ET.fromstring(r.content)
        # Atom: <feed><entry>...  RSS: <rss><channel><item>...
        has_atom_entry = root.find(".//{http://www.w3.org/2005/Atom}entry") is not None
        has_rss_item = root.find(".//item") is not None
        return has_atom_entry or has_rss_item
    except Exception:
        return False


def discover_source(entity_name: str) -> str | None:
    """Try to find a real, verified RSS/Atom feed for entity_name. Returns
    the verified URL, or None if discovery failed at any step (no domain
    guess, or no feed-path convention on that domain returns real content).
    Never returns an unverified URL."""
    domain = _guess_domain(entity_name)
    if not domain:
        log.info("entity_tracking: no confident domain guess for %r", entity_name)
        return None
    for path in _FEED_PATH_CANDIDATES:
        url = f"https://{domain}{path}"
        if _verify_feed(url):
            log.info("entity_tracking: discovered+verified feed for %r: %s", entity_name, url)
            return url
    log.info("entity_tracking: domain %s guessed for %r but no feed-path convention verified", domain, entity_name)
    return None


def _add_discovered_feed(entity_name: str, url: str, category: str) -> None:
    """Add a backlink-discovered feed to user_rss_feeds.json, nested under
    `category` (per operator directive #3 -- auto-promotion only ever nests
    under the category the entity was found in, never spawns a new
    top-level category). Marked discovered=True for provenance (directive
    #7) -- surfaced downstream by rss_retrieval.format_citations() and in
    the vault notes this module writes."""
    feeds = load_user_feeds()
    if any(f.get("url") == url for f in feeds):
        return  # already tracked, don't duplicate
    feeds.append({
        "id": f"discovered-{_slug(entity_name)}",
        "name": entity_name,
        "url": url,
        "category": category,
        "scope": "company",
        "department": None,
        "owner": None,
        "created_by": "entity_tracking (auto)",
        "discovered": True,
        "discovered_by": "cross-link-auto-track",
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    })
    save_user_feeds(feeds)
    log.info("entity_tracking: added discovered feed %r (%s) to category=%s", entity_name, url, category)


# ── Vault notes (novel-findings bucket + promoted-entity records) ────────

def _index(conn: sqlite3.Connection, rel_path: str, title: str, content: str, tags: str, ingest_method: str) -> None:
    index_note(conn, rel_path, title=title, content=content, tags=tags, ingest_method=ingest_method)


def _write_review_note(category: str, key: str, entry: dict, trigger: str | None, first_mover_reasons: list[str]) -> None:
    """00-Inbox/cross-link-findings/ -- the novel-findings holding area.
    Stable path per entity-in-category so repeat runs UPDATE the same
    note (fuller mention history) rather than fragmenting into duplicates
    a reviewer would have to hunt across."""
    slug = _slug(entry["display"])
    rel_path = f"{webdav_client.BUSINESS_ROOT}/00-Inbox/cross-link-findings/{category}-{slug}.md"

    window = _mentions_in_window(entry, date.today())
    distinct_feeds = sorted({m["feed_source"] for m in window})
    status_line = (
        f"THRESHOLD MET ({trigger}) but routed here for human review -- first-mover signal"
        if trigger else
        f"sub-threshold ({len(window)}/{RECURRENCE_THRESHOLD} mentions this week, "
        f"{len(distinct_feeds)}/{DISTINCT_FEED_THRESHOLD} distinct feeds)"
    )
    reasons_block = (
        "\n".join(f"- {r}" for r in first_mover_reasons) if first_mover_reasons
        else "(none -- routine sub-threshold recurrence, not yet a first-mover signal)"
    )
    source_block = (
        f"Discovered candidate source: {entry['discovered_source_url']} "
        f"({'VERIFIED, not yet added -- promote to add it' if entry['discovered_source_verified'] else 'unverified'})"
        if entry.get("discovered_source_url") else
        "No dedicated source discovered yet (or discovery not yet attempted)."
    )
    mentions_block = "\n".join(
        f"- {m['date']} | {m['feed_source']} | {m['signal_type']} | \"{m['item_title']}\" -- {m['justification']}"
        for m in entry["mentions"][-20:]  # most recent 20, avoid unbounded note growth
    )

    cite = _citability_fields(category, entry)
    uber_line = (
        f"**Relevant to Uber Series:** article(s) {', '.join(str(n) for n in cite['uber_series_articles'])}\n\n"
        if cite["uber_series_articles"] else ""
    )

    # Intercategory cross-linking (2026-08-07): nested subsection, only
    # present when there's something to show. This entity stays filed
    # under `category` regardless -- this is annotation, not a re-file.
    cross_matches = entry.get("cross_category_matches", [])
    cross_domain_block = (
        "\n\n### Cross-domain relevance\n\n" +
        "\n".join(
            f'- Also tracked in **{m["category"]}** (as "{m["display"]}", status: {m["status"]})'
            for m in cross_matches
        )
        if cross_matches else ""
    )

    note = (
        "---\n"
        f"category: {category}\n"
        f"entity: {entry['display']}\n"
        f"status: {status_line}\n"
        f"first_seen: {entry['first_seen']}\n"
        f"last_seen: {entry['last_seen']}\n"
        f"total_mentions: {len(entry['mentions'])}\n"
        f"citable: {str(cite['citable']).lower()}\n"
        f"uber_series_articles: {cite['uber_series_articles']}\n"
        f"cross_category_matches: {[m['category'] for m in cross_matches]}\n"
        f"generated_at: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n\n"
        f"# Cross-link finding: {entry['display']} ({category})\n\n"
        f"**Status:** {status_line}\n\n"
        f"**Why flagged as first-mover/novel:**\n{reasons_block}\n\n"
        f"**Source discovery:** {source_block}\n\n"
        f"{uber_line}"
        "**To promote manually:** add a feed for this entity to the "
        f"{category} category via the PWA Intel tab (or POST /api/rss/user-feeds), "
        "or wait -- it will auto-promote on its own once it crosses threshold "
        "AND isn't flagged as a first-mover signal.\n\n"
        f"**Recent mentions ({len(entry['mentions'])} total, most recent {min(len(entry['mentions']),20)} shown):**\n"
        f"{mentions_block}"
        f"{cross_domain_block}\n"
    )

    tags = f"novel-finding,cross-link,auto,{category}"
    if cite["citable"]:
        tags += ",citable"
    if cite["uber_series_articles"]:
        tags += ",uber-series"

    webdav_client.put(rel_path, note)
    conn = sqlite3.connect(INDEX_DB)
    init_vault_db(conn)
    _index(conn, rel_path, title=f"Cross-link finding: {entry['display']} ({category})",
           content=note, tags=tags,
           ingest_method="entity-tracking-novel-finding")
    conn.close()
    entry["novel_findings_note_path"] = rel_path


def _write_entity_note(category: str, key: str, entry: dict, trigger: str) -> None:
    """03-Entities/ -- written on promotion. PARA's existing named-entity
    folder, per second_brain.index_db._FOLDER_CATEGORY."""
    slug = _slug(entry["display"])
    rel_path = f"{webdav_client.BUSINESS_ROOT}/03-Entities/{category}-{slug}.md"

    provenance = (
        f"**Provenance: AUTO-DISCOVERED SOURCE** -- {entry['discovered_source_url']} "
        "(found and verified by entity_tracking's backlink-discovery, not manually curated)."
        if entry.get("discovered_source_verified") else
        "No dedicated source added -- promoted based on recurrence within existing tracked feeds only."
    )
    mentions_block = "\n".join(
        f"- {m['date']} | {m['feed_source']} | \"{m['item_title']}\""
        for m in entry["mentions"][-20:]
    )

    cite = _citability_fields(category, entry)
    uber_line = (
        f"**Relevant to Uber Series:** article(s) {', '.join(str(n) for n in cite['uber_series_articles'])}\n\n"
        if cite["uber_series_articles"] else ""
    )

    cross_matches = entry.get("cross_category_matches", [])
    cross_domain_block = (
        "\n### Cross-domain relevance\n\n" +
        "\n".join(
            f'- Also tracked in **{m["category"]}** (as "{m["display"]}", status: {m["status"]})'
            for m in cross_matches
        ) + "\n\n"
        if cross_matches else ""
    )

    note = (
        "---\n"
        f"category: {category}\n"
        f"entity: {entry['display']}\n"
        f"promoted_at: {entry['promoted_at']}\n"
        f"promotion_trigger: {trigger}\n"
        f"nested_under: {category}\n"
        f"citable: {str(cite['citable']).lower()}\n"
        f"uber_series_articles: {cite['uber_series_articles']}\n"
        f"cross_category_matches: {[m['category'] for m in cross_matches]}\n"
        "---\n\n"
        f"# {entry['display']} ({category})\n\n"
        f"Auto-promoted to a tracked entity {entry['promoted_at']} -- trigger: {trigger}.\n\n"
        f"{provenance}\n\n"
        f"{uber_line}"
        f"{cross_domain_block}"
        f"**Mention history ({len(entry['mentions'])} total, most recent shown):**\n"
        f"{mentions_block}\n"
    )

    tags = f"entity,cross-link,auto,{category}"
    if cite["citable"]:
        tags += ",citable"
    if cite["uber_series_articles"]:
        tags += ",uber-series"

    webdav_client.put(rel_path, note)
    conn = sqlite3.connect(INDEX_DB)
    init_vault_db(conn)
    _index(conn, rel_path, title=f"{entry['display']} ({category})",
           content=note, tags=tags,
           ingest_method="entity-tracking-promotion")
    conn.close()


# ── Top-level orchestration ──────────────────────────────────────────────

def get_boost_terms(category: str) -> str:
    """Space-joined display names of every entity ever tracked for this
    category (promoted or still under review) -- appended to
    RETRIEVE_QUERY so rss_retrieval.py's scoring weights articles
    mentioning them higher in every future pass. Deliberately not
    limited to promoted-only: the point is surfacing relevant content in
    the brief, which is useful regardless of whether a dedicated source
    was ever added for it."""
    state = load_state()
    cat_state = state.get(category, {})
    return " ".join(entry["display"] for entry in cat_state.values())


def find_cross_category_matches(entity_key: str, exclude_category: str) -> list[dict]:
    """Intercategory cross-linking (2026-08-07 operator directive): every
    category's entity-discovery pass checks both within its own category
    AND across every other tracked category for the same entity. Exact
    lowercase-name match only, matching this module's existing key
    convention -- deliberately not fuzzy (e.g. "Joby" vs "Joby Aviation"
    won't match yet), consistent with the lexical-not-fuzzy approach used
    everywhere else in this pipeline. Pure lookup, no mutation -- called
    fresh each time so results reflect whatever every OTHER category has
    discovered as of right now, not a stale snapshot.

    Returns [{category, display, status}] for every OTHER category
    tracking a matching entity. The caller (run_tracking_pass) never
    re-files the entity into another category -- it stays under whichever
    category originally discovered it; this is purely an annotation."""
    state = load_state()
    matches = []
    for cat, cat_state in state.items():
        if cat == exclude_category:
            continue
        entry = cat_state.get(entity_key)
        if entry:
            matches.append({
                "category": cat,
                "display": entry["display"],
                "status": entry.get("status", "unknown"),
            })
    return matches


# ── Two-tier promotion + demotion + unified auto-learning (2026-08-07) ───
#
# Tier 1: manual/human-triggered promotion (promote_manual), distinct
# from the existing threshold-based auto-promotion in run_tracking_pass
# (which stays unchanged). Tier 2: lightweight, explainable auto-learning
# (compute_learning_stats / effective_threshold) that adjusts future
# threshold sensitivity per (category, source_type, trigger) based on
# real track record -- NOT an ML model, a rate-based adjustment over the
# same promotion_history event log every source writes into.
#
# Demotion is a universal safety net across ALL THREE promotion paths
# (auto_threshold, manual, learned) -- always operator-triggered, never
# automatic, so an auto-system can never un-promote its own decisions
# unsupervised (that would risk promote/demote flapping). Every promoted
# item stays reviewable and revertible; nothing is a one-way pipeline.
#
# promotion_history is an append-only event log per entity -- current
# status is DERIVED from the last event (and mirrored into the existing
# flat promoted/promoted_at/promotion_trigger fields for backward
# compatibility with code that already reads them), not a separate
# source of truth that can drift out of sync with the real history.
#
# source_type is deliberately generic, not "rss"-specific -- today only
# RSS-threshold promotions exist, but Trends/export-analysis or any
# future ingest source plug into this SAME event log by writing events
# with their own source_type value. The learning stats aggregate over
# the full log regardless of source_type, so nothing needs a parallel
# learning system built later.

DEMOTION_REASONS = {"fruitless", "stale"}
MIN_LEARNING_SAMPLE = 8  # don't adjust a threshold on fewer resolved outcomes than this


def _append_promotion_event(entry: dict, event: str, **fields) -> dict:
    """Appends one event to promotion_history and mirrors the resulting
    current state into the existing flat fields every other function in
    this module already reads (promoted/promoted_at/promotion_trigger/
    status) -- so nothing else needs to change to stay correct."""
    entry.setdefault("promotion_history", [])
    record = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **fields}
    entry["promotion_history"].append(record)

    if event == "promoted":
        entry["promoted"] = True
        entry["promoted_at"] = record["at"]
        entry["promotion_trigger"] = fields.get("trigger")
        entry["status"] = "promoted"
    elif event == "demoted":
        entry["promoted"] = False
        entry["status"] = "demoted"
    elif event == "confirmed":
        pass  # positive signal only, doesn't change promoted/status
    return entry


def promote_manual(category: str, entity_key: str, operator: str, reason: str,
                    target_category: str | None = None) -> dict:
    """Tier 1 -- manual/human-triggered promotion. Distinct from
    run_tracking_pass's threshold-based auto-promotion, which is
    unchanged by this function's existence.

    target_category=None nests the entity under its existing category
    (same placement rule as auto-promotion: never silently spawns a new
    category). target_category=<new id> creates that category via the
    SAME mechanism the PWA's existing "Add Category" feature already
    uses (shared.rss_catalog.save_user_categories) -- reused, not
    reinvented -- then promotes into it. Raises ValueError if the entity
    doesn't exist or reason is empty (manual promotion requires a real
    justification, unlike auto-promotion's mechanical trigger reason)."""
    if not reason or not reason.strip():
        raise ValueError("promote_manual requires a non-empty reason")

    state = load_state()
    cat_state = state.get(category, {})
    entry = cat_state.get(entity_key)
    if entry is None:
        raise ValueError(f"no entity {entity_key!r} tracked under category {category!r}")

    placement_category = category
    if target_category and target_category != category:
        # Alias-aware dup guard (2026-08-07): if target_category names a concept
        # that already exists (exact, or shorthand/jargon/STT-variant), nest into
        # the existing category instead of spawning a duplicate.
        dup = find_existing_category(target_category)
        if dup:
            placement_category = dup["id"]
        else:
            existing_ids = {c["id"] for c in load_user_categories()} | set(state.keys())
            if target_category not in existing_ids:
                cats = load_user_categories()
                cats.append({"id": target_category, "label": target_category.replace("_", " ").title(),
                             "scope": "company", "builtin": False})
                save_user_categories(cats)
            placement_category = target_category

    if entry.get("discovered_source_verified"):
        _add_discovered_feed(entry["display"], entry["discovered_source_url"], placement_category)

    _append_promotion_event(
        entry, "promoted", method="manual", trigger=None, by=f"operator:{operator}",
        category=placement_category,
        placement={"type": "nested" if placement_category == category else "new_category",
                   "category": placement_category},
        reason=reason.strip(),
    )
    _write_entity_note(category, entity_key, entry, "manual")

    state[category][entity_key] = entry
    save_state(state)
    log.info("entity_tracking: %r manually promoted into %s by %s: %s",
              entry["display"], placement_category, operator, reason)
    return entry


def confirm_valuable(category: str, entity_key: str, operator: str, notes: str = "") -> dict:
    """Explicit positive signal -- operator confirms a promoted entity is
    actually delivering value. Feeds Tier 2's learning stats as a
    positive outcome alongside "never demoted" implicit signal; doesn't
    change promoted/status on its own."""
    state = load_state()
    cat_state = state.get(category, {})
    entry = cat_state.get(entity_key)
    if entry is None:
        raise ValueError(f"no entity {entity_key!r} tracked under category {category!r}")

    _append_promotion_event(entry, "confirmed", by=f"operator:{operator}", notes=notes)
    state[category][entity_key] = entry
    save_state(state)
    log.info("entity_tracking: %r confirmed valuable by %s", entry["display"], operator)
    return entry


def demote(category: str, entity_key: str, operator: str, reason: str, notes: str = "") -> dict:
    """Universal demotion -- works identically regardless of whether the
    entity got promoted via auto_threshold, manual, or (future) learned.
    ALWAYS operator-triggered, never automatic -- see module docstring
    for why. reason must be exactly "fruitless" (promoted but never
    delivered value) or "stale" (was relevant, isn't anymore) -- the two
    review questions this whole mechanism exists to answer. If a feed
    was added to user_rss_feeds.json at promotion time, it's removed
    from active pulling here -- the historical record stays in
    promotion_history, nothing is silently deleted."""
    if reason not in DEMOTION_REASONS:
        raise ValueError(f"reason must be one of {DEMOTION_REASONS}, got {reason!r}")

    state = load_state()
    cat_state = state.get(category, {})
    entry = cat_state.get(entity_key)
    if entry is None:
        raise ValueError(f"no entity {entity_key!r} tracked under category {category!r}")

    if entry.get("discovered_source_url"):
        feeds = load_user_feeds()
        feeds = [f for f in feeds if f.get("url") != entry["discovered_source_url"]]
        save_user_feeds(feeds)

    _append_promotion_event(entry, "demoted", by=f"operator:{operator}", reason=reason, notes=notes)
    state[category][entity_key] = entry
    save_state(state)
    log.info("entity_tracking: %r demoted (%s) by %s", entry["display"], reason, operator)
    return entry


def compute_learning_stats() -> dict:
    """Tier 2 -- the actual 'learning', lightweight and explainable, not
    ML. Aggregates outcome rates from EVERY entity's promotion_history
    across ALL categories and source_types, grouped by
    (category, source_type, trigger). A promotion's outcome is
    'fruitless_demoted' if its most recent demoted event has
    reason=fruitless, 'stale_demoted' for reason=stale, otherwise it
    counts as a non-negative outcome (confirmed or simply never
    demoted). Returns {(category, source_type, trigger): {"total": N,
    "fruitless_rate": pct}} -- only for groups with >= MIN_LEARNING_SAMPLE
    resolved (promoted, regardless of demoted or not) outcomes, so a
    thin sample can't swing a threshold."""
    state = load_state()
    groups: dict[tuple, list[bool]] = {}  # key -> [was_fruitless, ...]

    for cat_state in state.values():
        for entry in cat_state.values():
            history = entry.get("promotion_history", [])
            promo_events = [e for e in history if e["event"] == "promoted"]
            if not promo_events:
                continue
            last_promo = promo_events[-1]
            key = (last_promo.get("category", ""), last_promo.get("method", ""), last_promo.get("trigger"))
            demote_events = [e for e in history if e["event"] == "demoted" and e["at"] > last_promo["at"]]
            was_fruitless = bool(demote_events) and demote_events[-1].get("reason") == "fruitless"
            groups.setdefault(key, []).append(was_fruitless)

    stats = {}
    for key, outcomes in groups.items():
        if len(outcomes) < MIN_LEARNING_SAMPLE:
            continue
        fruitless_rate = round(100 * sum(outcomes) / len(outcomes), 1)
        stats[key] = {"total": len(outcomes), "fruitless_rate": fruitless_rate}
    return stats


def effective_threshold_multiplier(category: str, source_type: str, trigger: str) -> float:
    """Looks up whether this (category, source_type, trigger) combo has
    a high fruitless-demotion rate per compute_learning_stats(), and if
    so returns a multiplier > 1.0 to tighten future thresholds for that
    specific combo (e.g. 1.5x means DISTINCT_FEED_THRESHOLD effectively
    becomes 3 instead of 2 for this category). Returns 1.0 (no
    adjustment) when there's not enough sample yet or the fruitless rate
    isn't notably high. Deliberately simple, explainable arithmetic --
    the whole point is that this can be read and second-guessed, not a
    black box."""
    stats = compute_learning_stats()
    entry = stats.get((category, source_type, trigger))
    if not entry:
        return 1.0
    rate = entry["fruitless_rate"]
    if rate >= 50:
        return 2.0
    if rate >= 30:
        return 1.5
    return 1.0


def run_tracking_pass(category: str, items: list[dict], today: str) -> dict:
    """Full pipeline: extract -> record -> evaluate each entity mentioned
    this run -> auto-promote (routine + threshold met) or route to the
    novel-findings review bucket (first-mover, or still sub-threshold).
    Never raises -- a broken pass must not block the main brief. Returns
    a summary dict for the caller to log/notify from."""
    summary = {"auto_promoted": [], "routed_to_review": []}
    try:
        hits = extract_entities(items)
        if not hits:
            return summary
        cat_state = record_mentions(category, hits, items, today)
        today_d = date.fromisoformat(today)

        for name in hits:
            key = name.lower().strip()
            entry = cat_state[key]
            if entry.get("promoted"):
                continue  # already handled in a prior run

            trigger = evaluate_trigger(entry, today_d, category=category)
            is_first_mover, reasons = _is_first_mover(entry, today)

            # Intercategory cross-linking (2026-08-07): check every OTHER
            # category for this same entity. A NEWLY-found cross-category
            # match (wasn't there last time we recorded this entity) is
            # itself treated as a first-mover-style reason -- an entity
            # organically straddling multiple domains gets human eyes via
            # the existing review-routing path, same mechanism as any
            # other first-mover signal, not a new promotion pathway.
            prior_cats = {m["category"] for m in entry.get("cross_category_matches", [])}
            cross_matches = find_cross_category_matches(key, category)
            entry["cross_category_matches"] = cross_matches
            new_cats = {m["category"] for m in cross_matches} - prior_cats
            if new_cats:
                is_first_mover = True
                for m in cross_matches:
                    if m["category"] in new_cats:
                        reasons.append(
                            f'also tracked in {m["category"]} (as "{m["display"]}", status: {m["status"]})'
                        )

            # Directive #6: attempt backlink discovery for any genuinely
            # new entity (first appearance today), independent of which
            # branch it ends up in below -- a review-bucket entry should
            # still show a discovered candidate source if one exists.
            if entry["first_seen"] == today and not entry.get("discovered_source_url"):
                found_url = discover_source(entry["display"])
                if found_url:
                    entry["discovered_source_url"] = found_url
                    entry["discovered_source_verified"] = True

            if trigger and not is_first_mover:
                if entry.get("discovered_source_verified"):
                    _add_discovered_feed(entry["display"], entry["discovered_source_url"], category)
                _append_promotion_event(
                    entry, "promoted", method="auto_threshold", trigger=trigger, by="auto",
                    category=category, placement={"type": "nested", "category": category},
                    reason=f"crossed threshold: {trigger}",
                )
                _write_entity_note(category, key, entry, trigger)
                summary["auto_promoted"].append({
                    "name": entry["display"], "trigger": trigger,
                    "discovered_source": entry.get("discovered_source_url"),
                })
            else:
                entry["status"] = "novel_pending" if (trigger or is_first_mover) else "sub_threshold"
                for r in reasons:
                    if r not in entry["novel_review_reasons"]:
                        entry["novel_review_reasons"].append(r)
                _write_review_note(category, key, entry, trigger, reasons)
                summary["routed_to_review"].append({
                    "name": entry["display"],
                    "reason": "threshold+first-mover" if trigger else ("first-mover" if is_first_mover else "sub-threshold"),
                })

        state = load_state()
        state[category] = cat_state
        save_state(state)
    except Exception as e:
        log.warning("entity_tracking: tracking pass failed for %s: %s", category, e)
    return summary


# ── CLI trigger surface (2026-08-07) ──────────────────────────────────────
# First-pass trigger mechanism for Tier 1 promote/confirm/demote -- a
# vault-frontmatter-flag mechanism (editing the novel-findings note
# itself to request promotion) was flagged as a natural next step but
# not built yet; this CLI is what actually exists and works today.
# Invoke via: podman exec systemd-corporatetraveldc-poller
#   python3 -m common.entity_tracking promote <category> <entity_key> <operator> <reason>
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="entity_tracking manual promotion/demotion CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_promote = sub.add_parser("promote", help="Tier 1 manual promotion")
    p_promote.add_argument("category")
    p_promote.add_argument("entity_key")
    p_promote.add_argument("operator")
    p_promote.add_argument("reason")
    p_promote.add_argument("--new-category", default=None, help="promote into a brand-new category id")

    p_confirm = sub.add_parser("confirm", help="mark a promoted entity as confirmed valuable")
    p_confirm.add_argument("category")
    p_confirm.add_argument("entity_key")
    p_confirm.add_argument("operator")
    p_confirm.add_argument("--notes", default="")

    p_demote = sub.add_parser("demote", help="demote any promoted entity (auto/manual/learned)")
    p_demote.add_argument("category")
    p_demote.add_argument("entity_key")
    p_demote.add_argument("operator")
    p_demote.add_argument("reason", choices=sorted(DEMOTION_REASONS))
    p_demote.add_argument("--notes", default="")

    p_stats = sub.add_parser("stats", help="print Tier 2 learning stats")

    args = ap.parse_args()
    try:
        if args.cmd == "promote":
            result = promote_manual(args.category, args.entity_key, args.operator, args.reason,
                                     target_category=args.new_category)
            print(f"promoted: {result['display']} -> status={result['status']}")
        elif args.cmd == "confirm":
            result = confirm_valuable(args.category, args.entity_key, args.operator, notes=args.notes)
            print(f"confirmed: {result['display']}")
        elif args.cmd == "demote":
            result = demote(args.category, args.entity_key, args.operator, args.reason, notes=args.notes)
            print(f"demoted: {result['display']} -> reason={args.reason}")
        elif args.cmd == "stats":
            for key, s in compute_learning_stats().items():
                print(f"{key}: {s}")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
