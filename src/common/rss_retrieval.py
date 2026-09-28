"""
common.rss_retrieval -- lexical (keyword/TF) relevance retrieval over an
RSS item corpus, for the AAM and aviation daily/weekly watch skills.

2026-08-06, operator request: "start wiring actual RAG-based surfacing
of that content INTO the daily and weekly briefs -- not just cataloging/
storing it, but having the brief generation actually retrieve and cite
relevant items from the RSS corpus when composing the brief content."

What this IS: real retrieve-then-cite. score_items() ranks every fetched
item against a query built from the skill's own topic anchors (its
status block / system prompt context), retrieve() selects the top-N by
that score, and the caller builds the LLM prompt from the retrieved
subset with each item's title/source/link included as an explicit
citation -- not a blind dump of every headline the way aam_weekly_watch.py
originally did (headline_block = ALL items joined, no ranking, no
citation beyond title/source).

What this is NOT: embedding-based semantic search. There's no embedding
model pulled on this box and no vector store built (checked before
starting this -- neither exists yet, and pulling/validating a new Ollama
embedding model plus a vector index is a materially bigger, separate
undertaking than "start wiring" calls for tonight, especially right
after finding how badly a rushed change can go wrong tonight). This is
sparse/lexical retrieval (term-frequency overlap against a query, roughly
BM25-shaped without full BM25's corpus-frequency normalization) -- a
real, working, honest RAG variant, just not a dense/semantic one. If
semantic retrieval is wanted later, this module's retrieve() signature
(items in, ranked subset out) is the seam to swap the scoring function
behind without touching any caller.
"""
import re
from collections import Counter

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "is", "are", "was", "were", "be", "been", "this",
    "that", "it", "its", "as", "has", "have", "had", "will", "would",
    "its", "their", "his", "her", "not", "but", "into", "over", "after",
    "new", "news",
}


def _tokenize(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2]


def score_items(items: list[dict], query: str) -> list[tuple[dict, float]]:
    """Score each item's relevance to `query` by term-frequency overlap
    (title weighted 3x over summary -- a query term matching the
    headline itself is a much stronger relevance signal than matching
    somewhere in the summary body). Returns (item, score) pairs, highest
    score first. Items scoring 0 (no overlap at all) are still included
    -- callers decide the cutoff, this function doesn't silently drop
    the tail.
    """
    query_terms = set(_tokenize(query))
    if not query_terms:
        return [(it, 0.0) for it in items]

    scored = []
    for it in items:
        title_terms = Counter(_tokenize(it.get("title", "")))
        summary_terms = Counter(_tokenize(it.get("summary", "") or ""))
        score = 0.0
        for term in query_terms:
            score += title_terms.get(term, 0) * 3.0
            score += summary_terms.get(term, 0) * 1.0
        scored.append((it, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def retrieve(items: list[dict], query: str, top_n: int) -> list[dict]:
    """Score items against query, return the top_n (or fewer if the
    corpus itself is smaller than top_n). Ties broken by original order
    (stable sort) -- no randomness, same input always retrieves the same
    subset.
    """
    scored = score_items(items, query)
    return [it for it, _score in scored[:top_n]]


def format_citations(items: list[dict]) -> str:
    """Render retrieved items as an explicit, citable block for the LLM
    prompt: title, source, and link per item, so the model (and anyone
    reading the raw prompt for debugging) can see exactly which real
    article backs each claim it's asked to synthesize from -- not just
    a bare headline with no way to trace it back to a source.

    2026-08-07: items whose source feed was added by entity_tracking's
    backlink-discovery (runner/main.py's /api/rss tags these
    item["discovered"] = True, threaded from the feed's own discovered
    flag in user_rss_feeds.json) get an explicit [auto-discovered source]
    marker -- operator directive: never let auto-discovered-source content
    blend in indistinguishably with feeds curated by hand."""
    lines = []
    for it in items:
        title = (it.get("title") or "").strip()
        source = it.get("source") or it.get("feed") or "unknown"
        link = it.get("link") or ""
        marker = " [auto-discovered source]" if it.get("discovered") else ""
        if link:
            lines.append(f"- {title} ({source}){marker} — {link}")
        else:
            lines.append(f"- {title} ({source}){marker}")
    return "\n".join(lines)
