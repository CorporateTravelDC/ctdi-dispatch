"""
osint-monitor — SR-1 compliant.

Polls operator-defined OSINT scopes (RSS feeds + keyword filtering).
Scores each item deterministically; optionally generates narrative via Ollama.
Fires ntfy push on items meeting scope push_threshold.

SR-1: log_usage() in finally block.
SR-2: content_hash per item (INSERT OR IGNORE dedup in DB — no hash_gate needed
      since each fetch may produce new articles even with identical inputs).

Model: phi3.5 via Ollama (narrative generation, HIGH+ items only) or "deterministic".
Schedule: every 15 minutes (SKILL_SCHEDULE in poller/main.py).
"""

import argparse
import hashlib
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import timezone
from typing import Optional
from urllib.parse import urldefrag, urlparse

import httpx

from common import config, db
from common.llm import generate as llm_generate
from common.ntfy_push import send as ntfy_send
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME  = "osint-monitor"

OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "")
# OSINT narratives use the instruction-optimised model (mistral by default).
# Falls back to OLLAMA_MODEL → OLLAMA_CHAT_MODEL if OSINT-specific var unset.
OLLAMA_MODEL     = (os.getenv("OLLAMA_OSINT_NARRATOR_MODEL")
                    or os.getenv("OLLAMA_MODEL")
                    or "corporatetraveldc-pi5-brief:latest")
OLLAMA_TIMEOUT   = int(os.getenv("OLLAMA_TIMEOUT", "900"))  # stopgap
MODEL            = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"
FETCH_TIMEOUT    = 20           # seconds per RSS fetch
MAX_ITEMS_SCOPE  = 20           # cap per scope per run to limit CPU
MAX_AGE_DAYS     = 30           # prune items older than this

# ── Score thresholds ──────────────────────────────────────────────────────────
LABEL_THRESHOLDS = {
    "CRITICAL": 9,
    "HIGH":     7,
    "MEDIUM":   4,
    "LOW":      0,
}

PUSH_PRIORITY = {
    "CRITICAL": 5,
    "HIGH":     4,
    "MEDIUM":   3,
}

# Sources that get a +1 quality bonus on score.
TIER1_SOURCES = frozenset({
    "reuters.com", "apnews.com", "bbc.co.uk", "bbc.com",
    "washingtonpost.com", "nytimes.com", "wsj.com",
    "wtop.com", "dcist.com", "federalnewsnetwork.com",
    "fema.gov", "dhs.gov", "fbi.gov", "doj.gov",
    "nws.noaa.gov", "weather.gov",
    # DC-area local news — high EP signal value
    "wusa9.com", "wjla.com", "nbcwashington.com", "foxdc.com",
    "northernvirginiamag.com", "arlnow.com",
})

# Geographic tokens that indicate DC-area relevance.
# EP scopes get +1 when any token appears in title or first 200 chars of body.
_DC_AREA_TOKENS = frozenset({
    "washington", "dc", "arlington", "fairfax", "alexandria",
    "mclean", "tyson", "tysons", "bethesda", "chevy chase",
    "pentagon", "capitol hill", "national mall", "foggy bottom",
    "virginia", "maryland", "nova", "northern virginia",
    "dmv", "beltway",
})

# Scope types treated as executive-protection context.
_EP_SCOPE_TYPES = frozenset({
    "ep_threat", "ep_principal", "ep_venue", "executive_protection",
})

# Scope types treated as marketing/brand-intelligence context.
_MARKETING_SCOPE_TYPES = frozenset({
    "brand_monitor", "market_intel", "competitor", "marketing",
})

# Scope types for a named, dated, venue-bound event (conference/summit/
# forum) -- added 2026-08-12 (SCHEMA_V32: osint_scopes.event_name/
# audience/genre) for COS26. Gets its own narrative treatment (see
# _build_narrative_prompt) that leans on the event/audience/genre
# metadata directly rather than the generic marketing framing, but still
# treated as a marketing/positioning context for tagging purposes -- see
# _ntfy_tags_for_scope/_push_item.
_EVENT_SCOPE_TYPES = frozenset({"event"})

# ── Feed namespace map for XML parsing ────────────────────────────────────────
NS = {
    "atom":    "http://www.w3.org/2005/Atom",
    "media":   "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc":      "http://purl.org/dc/elements/1.1/",
}


# ── RSS / Atom parsing ────────────────────────────────────────────────────────

def _fetch_feed(url: str) -> list[dict]:
    """
    Fetch and parse an RSS 2.0 or Atom 1.0 feed.
    Returns a list of dicts: {title, url, published_at, source_name, summary}.
    Never raises — returns [] on any failure.
    """
    try:
        headers = {
            "User-Agent": "corporatetraveldc-dispatch/1.0 (+https://example.com)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        }
        resp = httpx.get(url, headers=headers, timeout=FETCH_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        raw = resp.text
    except Exception as exc:
        log.debug("osint: fetch failed %s: %s", url, exc)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        log.debug("osint: XML parse failed %s: %s", url, exc)
        return []

    items: list[dict] = []

    # ── Atom 1.0 ──────────────────────────────────────────────────────────────
    if root.tag in (f"{{{NS['atom']}}}feed", "feed"):
        source_name = _text(root, f"{{{NS['atom']}}}title") or urlparse(url).hostname
        for entry in root.findall(f"{{{NS['atom']}}}entry"):
            title = _text(entry, f"{{{NS['atom']}}}title")
            link  = None
            for lel in entry.findall(f"{{{NS['atom']}}}link"):
                if lel.get("rel") in ("alternate", None):
                    link = lel.get("href")
                    break
            published = _text(entry, f"{{{NS['atom']}}}published") \
                     or _text(entry, f"{{{NS['atom']}}}updated")
            summary  = _text(entry, f"{{{NS['atom']}}}summary") or ""
            if title and link:
                items.append({
                    "title":       title.strip(),
                    "url":         urldefrag(link)[0],
                    "published_at": _parse_date(published),
                    "source_name": source_name,
                    "summary":     summary[:500],
                })

    # ── RSS 2.0 ───────────────────────────────────────────────────────────────
    else:
        channel = root.find("channel")
        if channel is None:
            channel = root
        source_name = _text(channel, "title") or urlparse(url).hostname
        for item in channel.findall(".//item"):
            title   = _text(item, "title")
            link    = _text(item, "link") or _text(item, "guid")
            pubdate = _text(item, "pubDate") or _text(item, f"{{{NS['dc']}}}date")
            summary = _text(item, "description") or \
                      _text(item, f"{{{NS['content']}}}encoded") or ""
            # Strip basic HTML tags from summary
            summary = re.sub(r"<[^>]+>", " ", summary)[:500].strip()
            if title and link:
                items.append({
                    "title":       title.strip(),
                    "url":         urldefrag(link)[0],
                    "published_at": _parse_date(pubdate),
                    "source_name": source_name,
                    "summary":     summary,
                })

    return items[:MAX_ITEMS_SCOPE]


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else None


def _parse_date(s: str | None) -> float | None:
    if not s:
        return None
    # Try RFC 2822 (RSS pubDate) then ISO 8601 (Atom)
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).timestamp()
    except Exception:
        pass
    try:
        import datetime
        return datetime.datetime.fromisoformat(
            s.rstrip("Z").replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_item(item: dict, terms: list[str], scope_type: str = "keyword") -> tuple[int, str]:
    """
    Deterministic relevance score 0–10.
    Returns (score_int, score_label).

    Factors:
      - Title match:    +3 per distinct matched term (cap 6)
      - Body match:     +1 per distinct matched term (cap 3, beyond title matches)
      - Recency:        published < 6h → +2, < 24h → +1
      - Tier-1 source:  +1
      - DC-area geo:    +1 for EP scope types (ep_threat, ep_principal, ep_venue)
                        when DC-area geographic token found in title or lead summary
    """
    title = (item.get("title") or "").lower()
    body  = (item.get("summary") or "").lower()
    score = 0

    title_matches = 0
    body_only_matches = 0
    for term in terms:
        t = term.lower().strip()
        if not t:
            continue
        in_title = bool(re.search(r'\b' + re.escape(t) + r'\b', title) or t in title)
        in_body  = bool(re.search(r'\b' + re.escape(t) + r'\b', body)  or t in body)
        if in_title:
            title_matches += 1
        elif in_body:
            body_only_matches += 1

    # 2026-08-12: hard gate -- recency/source/geo bonuses below must never
    # be the ONLY thing that puts an item above 0. Found the hard way after
    # adding several high-volume general-news feeds (Guardian/NPR/AJ/CNBC/
    # UPI/DW) alongside each scope's precise Google News query: those feeds
    # aren't keyword-filtered upstream the way Google News is, so without
    # this gate, any sufficiently recent article from a reputable outlet --
    # regardless of topic -- scored nonzero purely on recency/source-quality
    # bonuses and passed the `if score == 0: continue` skip in main(). A
    # scope watching for "AAEI Annual Conference" was picking up Colombia
    # earthquake coverage and Lakers sale news at LOW/MEDIUM purely because
    # they were recent Guardian/UPI articles, zero keyword relevance.
    if title_matches == 0 and body_only_matches == 0:
        return 0, "LOW"

    score += min(title_matches * 3, 6)
    score += min(body_only_matches, 3)

    # Recency bonus
    pub = item.get("published_at")
    if pub:
        age = time.time() - pub
        if age < 21600:    # 6 hours
            score += 2
        elif age < 86400:  # 24 hours
            score += 1

    # Source quality bonus
    source_domain = urlparse(item.get("url", "")).hostname or ""
    source_domain = source_domain.removeprefix("www.")
    if any(t1 in source_domain for t1 in TIER1_SOURCES):
        score += 1

    # DC-area geographic proximity bonus — EP scopes only
    if scope_type in _EP_SCOPE_TYPES:
        haystack = title + " " + body[:200]
        if any(tok in haystack for tok in _DC_AREA_TOKENS):
            score += 1

    score = min(score, 10)
    label = "LOW"
    for lbl, threshold in LABEL_THRESHOLDS.items():
        if score >= threshold:
            label = lbl
            break

    return score, label


def _content_hash(url: str, title: str) -> str:
    """Stable dedup key: hash of normalized URL + normalized title."""
    key = (urldefrag(url.strip())[0] + "|" + title.strip().lower()).encode()
    return hashlib.sha256(key).hexdigest()[:32]


# ── Cross-outlet story clustering (2026-08-12) ─────────────────────────────────
#
# Operator observation: the same real-world story routinely shows up as
# several separate osint_items rows -- one per outlet -- because several
# feeds (Google News per-scope queries + the broad Guardian/BBC/NPR/etc.
# sources) can all carry the same story. Google News item titles are
# formatted "Headline - Outlet Name"; splitting that apart at ingest time
# lets the PWA show ONE story with a list of covering outlets instead of
# what looks like N unrelated duplicate rows.
_GOOGLE_NEWS_MARKER = "google news"
_TITLE_SOURCE_SPLIT_RE = re.compile(r"\s+-\s+(?!.*\s+-\s+)")  # last " - "
_STORY_KEY_STRIP_RE = re.compile(r"[^a-z0-9 ]+")


def _split_headline_outlet(title: str, source_name: str | None) -> tuple[str, str]:
    """Return (headline, outlet). Google News items are titled
    "Headline - Outlet"; split on the LAST " - " to recover the real
    outlet. Non-Google-News feeds already carry the real outlet in
    source_name, so title is used unsplit as the headline."""
    src = (source_name or "").lower()
    if _GOOGLE_NEWS_MARKER in src:
        parts = _TITLE_SOURCE_SPLIT_RE.split(title, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()
    return title.strip(), (source_name or "unknown").strip()


def _story_key(headline: str) -> str:
    """Fingerprint a headline for cross-outlet clustering.

    Deliberately an EXACT normalized-string match (lowercase, punctuation
    stripped, whitespace collapsed) rather than fuzzy/token-similarity
    matching -- different outlets phrase the same event differently
    enough ("Colombia quake leaves 110 dead" vs "Scores dead in Colombia
    earthquake") that a similarity threshold would just as often
    merge unrelated stories as catch real duplicates. This exact-match
    approach reliably catches the actual observed case (Google News
    surfacing the identical headline from multiple outlets) without that
    false-positive risk. Nothing downstream currently uses this for
    scoring/routing decisions, only client-side display grouping, so the
    conservative (miss some, never wrongly-merge) tradeoff is the right
    one here.
    """
    norm = _STORY_KEY_STRIP_RE.sub(" ", headline.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return ""
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


# ── Ollama narrative (optional) ───────────────────────────────────────────────

def _build_narrative_prompt(item: dict, scope_label: str, matched_terms: list[str],
                             scope_type: str, event_name: str = "",
                             audience: str = "", genre: str = "") -> str:
    """Build a scope-type-aware Ollama prompt for narrative generation.

    event_name/audience/genre (2026-08-12): only meaningful for
    scope_type="event" -- ignored otherwise. See _EVENT_SCOPE_TYPES."""
    terms_str = ", ".join(matched_terms) if matched_terms else "see title"
    header = (
        f"Scope: {scope_label} (monitoring: {terms_str})\n"
        f"Article: {item['title']}\n"
        f"Source: {item.get('source_name', 'unknown')}\n"
        f"Summary: {item.get('summary', '')[:300]}\n\n"
    )

    if scope_type in _EP_SCOPE_TYPES:
        instruction = (
            "Write a 2-sentence executive protection assessment for an EP operator "
            "in the Washington DC metro area. First sentence: what happened and where. "
            "Second sentence: operational relevance to principal safety, route planning, "
            "or advance work — state if action is required. Plain text only. No markdown."
        )
    elif scope_type in _EVENT_SCOPE_TYPES:
        event_line = f"Event: {event_name}\n" if event_name else ""
        audience_line = f"Audience: {audience}\n" if audience else ""
        genre_line = f"Genre: {genre}\n" if genre else ""
        header = event_line + audience_line + genre_line + header
        instruction = (
            "Write a 2-sentence brief for [operator LLC] (a boutique DC-area "
            "executive services firm: chauffeur transportation, EP-adjacent logistics, "
            "brand strategy) about this event. First sentence: what this article says "
            "about the event and why it matters given the stated audience/genre. "
            "Second sentence: a concrete cross-link opportunity — ground transport "
            "demand around the venue/dates, a marketing/positioning angle toward this "
            "audience, or a networking/sponsorship opening. Plain text only. No markdown."
        )
    elif scope_type in _MARKETING_SCOPE_TYPES:
        instruction = (
            "Write a 2-sentence brand and market intelligence summary for [operator LLC abbreviation]utive "
            "Services, a boutique DC-area executive services firm (automotive detailing, "
            "brand strategy, executive chauffeur transportation, IT security). "
            "First sentence: what happened and why it matters to the market. "
            "Second sentence: strategic implication or opportunity. Plain text only. No markdown."
        )
    else:
        # Generic keyword / catch-all
        instruction = (
            "Write a 2-sentence operational assessment for an executive dispatch operator "
            "in Washington DC. First sentence: what happened. Second sentence: operational "
            "relevance or recommended action. Plain text only. No markdown."
        )

    return header + instruction


def _generate_narrative(item: dict, scope_label: str, matched_terms: list[str],
                         scope_type: str = "keyword", event_name: str = "",
                         audience: str = "", genre: str = "") -> str | None:
    """
    Call LLM (Ollama-first, deterministic fallback -- Anthropic explicitly
    closed, see allow_anthropic below) for a 2-sentence narrative on a HIGH+
    item. Returns None on any failure — caller falls back to deterministic
    narrative.
    """
    prompt = _build_narrative_prompt(item, scope_label, matched_terms, scope_type,
                                      event_name, audience, genre)
    return llm_generate(
        system="",
        prompt=prompt,
        ollama_model=OLLAMA_MODEL,
        max_tokens=100,
        temperature=0.3,
        # 2026-08-12: was implicitly True (the generate() default) -- this
        # skill was the one that surfaced the global ANTHROPIC_FALLBACK_ENABLED
        # gate being unset (silently "true") on an Ollama failure. Belt-and-
        # suspenders per-call close, same as ops_brief.py.
        allow_anthropic=False,
    )


def _deterministic_narrative(item: dict, scope_label: str, matched_terms: list[str],
                              score: int, score_label: str) -> str:
    """Fallback narrative when Ollama is not configured."""
    terms_str = ", ".join(matched_terms[:3]) if matched_terms else "monitored terms"
    pub = item.get("published_at")
    age_str = ""
    if pub:
        minutes = int((time.time() - pub) / 60)
        if minutes < 60:
            age_str = f" ({minutes}m ago)"
        elif minutes < 1440:
            age_str = f" ({minutes // 60}h ago)"
    source = item.get("source_name") or "unknown source"
    return (
        f"[{score_label}] {source}{age_str}: \"{item['title'][:120]}\" "
        f"— matched {scope_label} terms: {terms_str}."
    )


# ── Push logic ────────────────────────────────────────────────────────────────

def _should_push(score_label: str, push_threshold: str) -> bool:
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    try:
        return order.index(score_label) >= order.index(push_threshold)
    except ValueError:
        return False


def _ntfy_tags_for_scope(scope_type: str, score_label: str) -> str:
    """Return ntfy emoji tags appropriate for scope type and score level."""
    urgency = "rotating_light" if score_label == "CRITICAL" else ""
    if scope_type in _EP_SCOPE_TYPES:
        base = "shield,newspaper"
    elif scope_type in _EVENT_SCOPE_TYPES:
        base = "calendar,newspaper"
    elif scope_type in _MARKETING_SCOPE_TYPES:
        base = "chart_with_upwards_trend,newspaper"
    else:
        base = "newspaper,mag"
    return f"{urgency},{base}".lstrip(",") if urgency else base


def _push_item(item: dict, scope_label: str, narrative: str,
               scope_type: str = "keyword") -> None:
    score_label = item.get("score_label", "LOW")
    priority    = PUSH_PRIORITY.get(score_label, 3)
    # Type indicator prefix for mobile notification title
    if scope_type in _EP_SCOPE_TYPES:
        type_tag = "EP"
    elif scope_type in _EVENT_SCOPE_TYPES:
        type_tag = "EVT"
    elif scope_type in _MARKETING_SCOPE_TYPES:
        type_tag = "MKT"
    else:
        type_tag = "OSINT"
    title_text = f"[{score_label}][{type_tag}] {scope_label}: {item['title'][:70]}"
    body       = f"{narrative}\n\n{item['url']}"
    ntfy_send(
        "osint-alerts",
        body,
        title=title_text,
        priority=priority,
        tags=_ntfy_tags_for_scope(scope_type, score_label),
        click_url=item["url"],
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main(force: bool = False) -> None:
    scopes = db.osint_get_scopes(enabled_only=True)
    if not scopes:
        log.debug("%s: no enabled OSINT scopes — skipping", SKILL_NAME)
        return

    status         = "error"
    total_new      = 0
    total_pushed   = 0
    total_scored   = 0
    model_used     = OLLAMA_MODEL if OLLAMA_BASE_URL else MODEL

    try:
        # Prune stale items first (house-keeping, non-fatal)
        try:
            db.osint_prune_items(MAX_AGE_DAYS)
        except Exception:
            pass

        for scope in scopes:
            scope_id    = scope["id"]
            scope_label = scope["label"]
            scope_type  = scope.get("scope_type", "keyword")
            event_name  = scope.get("event_name") or ""
            audience    = scope.get("audience") or ""
            genre       = scope.get("genre") or ""
            # Split on commas ONLY, not whitespace -- 2026-08-12, found via
            # COS26: comma-and-whitespace splitting shredded a term like
            # "Chief of Staff Association" into four separate single-word
            # matches ("Chief", "of", "Staff", "Association"), any one of
            # which alone triggered a false-positive title-match bonus on
            # completely unrelated "chief of staff" personnel-change news.
            # Each comma-separated entry is now kept whole as one phrase,
            # matched as a substring/word-boundary in _score_item exactly
            # like a single-word term always was -- multi-word terms just
            # work correctly now instead of needing to be avoided.
            terms       = [t.strip() for t in
                           scope["query_terms"].split(",") if t.strip()]
            if not terms:
                continue

            feed_urls = [u.strip() for u in scope["feed_urls"].splitlines() if u.strip()]
            if not feed_urls:
                log.debug("osint: scope %r has no feed_urls — skipping", scope_label)
                continue

            scope_new = 0
            for feed_url in feed_urls:
                items = _fetch_feed(feed_url)
                for item in items:
                    title = item.get("title", "")
                    url   = item.get("url", "")
                    if not title or not url:
                        continue

                    score, score_label = _score_item(item, terms, scope_type)
                    total_scored += 1

                    if score == 0:
                        continue   # no match at all — skip

                    content_hash = _content_hash(url, title)
                    headline, outlet = _split_headline_outlet(
                        title, item.get("source_name")
                    )
                    story_key = _story_key(headline)

                    # Build narrative (Ollama for HIGH+, deterministic fallback)
                    matched = [t for t in terms
                               if t.lower() in (title + " " + item.get("summary", "")).lower()]
                    narrative: Optional[str] = None
                    if score >= LABEL_THRESHOLDS["HIGH"] and OLLAMA_BASE_URL:
                        narrative = _generate_narrative(item, scope_label, matched, scope_type,
                                                         event_name, audience, genre)
                    if not narrative:
                        # 2026-08-06: narrow safety net around the fallback
                        # ITSELF -- same pattern applied identically across
                        # every skill with an Ollama fallback (see
                        # route_impact.py for the full note). Per-item here
                        # rather than whole-run: this call site sits inside
                        # the per-feed/per-item loop with no outer catch, so
                        # an unguarded exception would silently abort
                        # processing of every remaining item in this run,
                        # not just this one.
                        try:
                            narrative = _deterministic_narrative(
                                item, scope_label, matched, score, score_label
                            )
                        except Exception as fallback_err:
                            log.error("osint: deterministic fallback also failed for %r — %s",
                                      title, fallback_err)
                            narrative = (
                                f"[OSINT-MONITOR] Generation failed for this item -- both "
                                f"Ollama and the deterministic fallback errored. See logs."
                            )

                    is_new = db.osint_save_item(
                        scope_id=scope_id,
                        title=title,
                        url=url,
                        source_name=item.get("source_name"),
                        published_at=item.get("published_at"),
                        score=score,
                        score_label=score_label,
                        narrative=narrative,
                        content_hash=content_hash,
                        headline=headline,
                        outlet=outlet,
                        story_key=story_key,
                    )

                    if is_new:
                        scope_new += 1
                        total_new += 1

                        # Push if scope threshold met
                        if _should_push(score_label, scope.get("push_threshold", "HIGH")):
                            try:
                                _push_item(
                                    item | {"score_label": score_label},
                                    scope_label,
                                    narrative,
                                    scope_type,
                                )
                                db.osint_mark_pushed(
                                    db.osint_get_feed(scope_id=scope_id, min_score=score, limit=1)[0]["id"]
                                )
                                total_pushed += 1
                            except Exception as exc:
                                log.warning("osint: push failed for %r: %s", title[:60], exc)

            if scope_new:
                log.info("osint: scope %r — %d new items", scope_label, scope_new)

        status = "ok"
        log.info(
            "%s: OK — %d scopes, %d scored, %d new, %d pushed",
            SKILL_NAME, len(scopes), total_scored, total_new, total_pushed,
        )

    except Exception as exc:
        log.error("%s: unhandled error: %s", SKILL_NAME, exc)
        raise

    finally:
        log_usage(SKILL_NAME, model_used, 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true",
                        help="Bypass SR-2 gate (has no effect for OSINT — per-item dedup always runs)")
    args = parser.parse_args()
    main(force=args.force)
