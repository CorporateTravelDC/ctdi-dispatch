"""
common.trends_signal -- Google Trends supplementary signal, layered onto
the existing RSS/cross-link automation (common.entity_tracking). 2026-08-07
operator request, refined across several messages.

Explicitly supplementary/annotation-only -- NEVER wired into
entity_tracking's auto-promotion threshold (5x/week OR 2+ distinct
feeds). That threshold is deliberately built entirely from this box's
own tracked RSS corpus so it stays auditable and doesn't depend on an
external, fragile call succeeding. Trends data only ever does two
things, both human-facing, neither of which mutates tracked state:
  1. Annotate an already-tracked entity with search-interest context
     ("Joby Aviation search interest up 40% this week").
  2. Surface a "promotion suggested" candidate -- an entity Trends
     itself turned up as related/rising for an established topic, not
     currently tracked anywhere -- as a call-out for a HUMAN to act on,
     distinct from entity_tracking's own novel-findings bucket (that
     bucket is RSS-corpus-driven; this is Trends-driven).

No official Google Trends API exists. Uses `trendspy` (PyPI, actively
maintained fork of the archived pytrends) -- checked live 2026-08-07:
real, working calls against trends.google.com from this box.
interest_over_time() worked immediately; related_queries() hit
TrendsQuotaExceededError on the very SECOND call from this box's IP,
recovered with the library's own suggested remedy (a Google referer
header) plus a short delay. That fragility is real and expected to
recur -- every call in this module is wrapped non-fatal, matching the
established pattern for every other best-effort enrichment in this
codebase (a broken call skips that annotation/candidate for this run,
never blocks the main brief, never raises).

Two-tier cadence (operator-specified):
  - EARLY-AM sweep: interest_over_time annotations only, against the
    UNION of promoted + novel-findings/sub-threshold entities for a
    category (the broader set) -- cheaper call type, wider keyword set.
  - MID-DAY sweep: interest_over_time annotations against PROMOTED
    entities only (narrower set) PLUS related_queries-based "promotion
    suggested" detection for those same entities -- the more fragile
    call type, deliberately kept to a small, bounded keyword count.

Batches up to BATCH_SIZE keywords per interest_over_time call (Trends
supports multi-keyword comparison natively -- confirmed live) to keep
total request volume down; related_queries has no batch form (one
keyword per call) so MAX_PROMOTION_CHECK caps how many promoted
entities get checked for promotion-suggestions per mid-day run.
"""
import logging
import time

from trendspy import Trends

from common import entity_tracking

log = logging.getLogger(__name__)

TIMEFRAME = "now 7-d"
BATCH_SIZE = 5
MAX_PROMOTION_CHECK = 2  # related_queries is the fragile call -- keep this small
REQUEST_DELAY = 2.0
# 2026-08-07: confirmed live -- related_queries hit TrendsQuotaExceededError
# on the second raw call from this box without it; recovered immediately
# with this header + a short delay. interest_over_time didn't need it but
# sending it everywhere is harmless and one less thing to get wrong.
_HEADERS = {"referer": "https://www.google.com/"}


def _client() -> Trends:
    return Trends(request_delay=REQUEST_DELAY)


def _promoted_names(category: str) -> list[str]:
    state = entity_tracking.load_state()
    cat_state = state.get(category, {})
    return [e["display"] for e in cat_state.values() if e.get("promoted")]


def _novel_names(category: str) -> list[str]:
    state = entity_tracking.load_state()
    cat_state = state.get(category, {})
    return [e["display"] for e in cat_state.values() if not e.get("promoted")]


def _all_tracked_names_everywhere() -> set[str]:
    """Lowercased display names of every entity tracked in ANY category --
    used to filter promotion-suggestion candidates so we don't suggest
    something already tracked elsewhere (e.g. an AAM company that's
    already nested under aviation)."""
    state = entity_tracking.load_state()
    names = set()
    for cat_state in state.values():
        for entry in cat_state.values():
            names.add(entry["display"].lower().strip())
    return names


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _pct_change(series) -> float | None:
    """% change from the first to the last non-partial row of an
    interest_over_time series. None if too little data to compare."""
    try:
        clean = series[series.get("isPartial", False) != True] if "isPartial" in series else series
        clean = clean.dropna()
        if len(clean) < 2:
            return None
        first, last = clean.iloc[0], clean.iloc[-1]
        if first == 0:
            return None
        return round((last - first) / first * 100, 1)
    except Exception:
        return None


def fetch_annotations(keywords: list[str]) -> dict[str, str]:
    """Batched interest_over_time lookup. Returns {keyword: annotation
    string} for whichever keywords succeeded -- silently omits any that
    fail or have too little data, never raises."""
    if not keywords:
        return {}
    client = _client()
    annotations: dict[str, str] = {}
    for batch in _batched(keywords, BATCH_SIZE):
        try:
            df = client.interest_over_time(batch, timeframe=TIMEFRAME, headers=_HEADERS)
            for kw in batch:
                if kw not in df.columns:
                    continue
                pct = _pct_change(df[kw])
                if pct is None:
                    continue
                direction = "up" if pct >= 0 else "down"
                annotations[kw] = f"search interest {direction} {abs(pct):.0f}% this week"
        except Exception as e:
            log.warning("trends_signal: interest_over_time failed for batch %s: %s", batch, e)
        time.sleep(REQUEST_DELAY)
    return annotations


def fetch_promotion_candidates(keywords: list[str]) -> list[dict]:
    """related_queries lookup for a SMALL set of established keywords
    (caller is responsible for keeping this short -- see
    MAX_PROMOTION_CHECK). Returns a list of {source_entity, candidate,
    score, kind} for related/rising queries that look like a distinct
    entity name (not already tracked anywhere in entity_tracking) --
    never raises, returns [] on total failure."""
    if not keywords:
        return []
    already_tracked = _all_tracked_names_everywhere()
    client = _client()
    candidates = []
    for kw in keywords[:MAX_PROMOTION_CHECK]:
        try:
            rq = client.related_queries(kw, timeframe=TIMEFRAME, headers=_HEADERS)
        except Exception as e:
            log.warning("trends_signal: related_queries failed for %r: %s", kw, e)
            time.sleep(REQUEST_DELAY)
            continue
        kw_first_word = kw.lower().split()[0] if kw.split() else kw.lower()
        for kind in ("rising", "top"):
            df = rq.get(kind)
            if df is None or not hasattr(df, "iterrows") or df.empty:
                continue
            for _, row in df.iterrows():
                query = str(row.get("query", "")).strip()
                if not query or query.lower() in already_tracked or kw.lower() in query.lower():
                    continue
                # Skip generic price/stock/earnings modifiers of the same
                # entity (e.g. "joby stock", "joby earnings") -- not a
                # distinct entity, just more of the one already tracked.
                if query.lower().startswith(kw_first_word):
                    continue
                candidates.append({
                    "source_entity": kw,
                    "candidate": query,
                    "score": row.get("value"),
                    "kind": kind,
                })
        time.sleep(REQUEST_DELAY)
    return candidates


def early_am_sweep(category: str) -> dict[str, str]:
    """Broad annotation pass: promoted + novel-findings/sub-threshold
    entities for this category. Annotation-only, no promotion-suggestion
    detection (that's mid-day-specific, per operator directive)."""
    keywords = _promoted_names(category) + _novel_names(category)
    if not keywords:
        return {}
    try:
        return fetch_annotations(keywords)
    except Exception as e:
        log.warning("trends_signal: early_am_sweep failed for %s: %s", category, e)
        return {}


def mid_day_sweep(category: str) -> dict:
    """Narrow sweep: promoted entities only, annotations PLUS
    promotion-suggestion candidates. Returns {"annotations": {...},
    "promotion_suggested": [...]}."""
    promoted = _promoted_names(category)
    result = {"annotations": {}, "promotion_suggested": []}
    if not promoted:
        return result
    try:
        result["annotations"] = fetch_annotations(promoted)
    except Exception as e:
        log.warning("trends_signal: mid_day_sweep annotations failed for %s: %s", category, e)
    try:
        result["promotion_suggested"] = fetch_promotion_candidates(promoted)
    except Exception as e:
        log.warning("trends_signal: mid_day_sweep promotion-candidates failed for %s: %s", category, e)
    return result


def format_promotion_suggested_section(candidates: list[dict]) -> str:
    """Renders the dedicated 'promotion suggested' block for a mid-day
    brief -- distinct from entity_tracking's novel-findings bucket per
    operator directive. Empty string if nothing to show (caller omits
    the section entirely rather than printing an empty header)."""
    if not candidates:
        return ""
    lines = ["PROMOTION SUGGESTED (Trends-surfaced, not yet tracked):"]
    for c in candidates:
        lines.append(
            f"- {c['candidate']} ({c['kind']} query for {c['source_entity']}, score={c['score']})"
        )
    return "\n".join(lines)
