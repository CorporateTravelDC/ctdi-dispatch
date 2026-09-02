"""
executive_standard.render -- static HTML rendering for the self-hosted
mirror of "The Executive Standard" (corporatetraveldc.substack.com).

Design approved by the operator 2026-08-31 (two design-review rounds):
quiet-luxury editorial, ink/parchment palette, Fraunces (display) +
Newsreader (body) + IBM Plex Mono (labels/data). Palette corrected the
same day to match the REAL wordmark (src/executive_standard/assets/
wordmark.png, pulled from the live Substack site) -- navy/near-black ink
+ gold/brass accent, sampled directly from that image, not the green
used in the first draft preview.

Accessibility/PWA pass (2026-09-01), same standard as the dispatch-runner
PWA's ARIA-live "azimuth method" (src/runner/frontend/src/components/
AriaCompassRegion.jsx) -- that component itself doesn't transfer (it's
built for a live-refreshing dashboard; this site is static), but the
underlying bar does: real document structure (this previously had no
<!DOCTYPE>/<html>/<head>/<body> at all -- browsers were silently
auto-repairing it, but that means no declared page language, a real
WCAG 3.1.1 miss for a screen reader), landmark elements, a skip link,
visible focus states, and -- the actual root cause of the "weird mobile
layout" the operator flagged -- a missing viewport meta tag, which was
letting phones render the page at desktop width and scale it down
instead of laying out natively. Also now a real installable PWA
(manifest.json + sw.js + icon set, see assets/icons/ -- extracted from
the gold column-seal half of the wordmark image, not a separate asset).

Pure functions only -- no I/O, no DB. The caller (a one-time backfill
script and/or a periodic sync skill) supplies post dicts and writes the
returned strings to disk.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Post:
    slug: str
    title: str
    dek: str
    date_iso: str          # e.g. "2026-08-20T12:54:01Z"
    body_html: str         # already-safe HTML (paragraphs), not markdown
    kicker: str = "Desk Memo"
    body_md: str = ""      # 2026-09-02: raw markdown body, for the
                            # Accept: text/markdown content-negotiation
                            # path (render_markdown() below) -- real
                            # source for Pi-native posts, html2text-
                            # converted for Substack-sourced ones (see
                            # executive_standard_sync.py). Empty string
                            # falls back to a plain body_html strip.


SITE_TITLE = "The Executive Standard"
SITE_TAGLINE = (
    "Everyone who's helped build and keep this platform flying" if False else
    "Where elite client service meets absolute discretion — tactical guidance "
    "for executives and support staff managing U/HNW portfolios."
)
SUBSCRIBE_URL = "https://corporatetraveldc.substack.com"
WORDMARK_PATH = "assets/wordmark.png"  # relative to site root

# ── Shared CSS ────────────────────────────────────────────────────────────
# Palette corrected 2026-08-31 to reuse the MAIN company site's literal
# design tokens (example.com/css/style.css --navy/--amber/
# --slate/--sgray/--lgray custom properties), not an independent
# approximation sampled from the wordmark image -- confirms this reads as
# provably the same brand family, not just a close match. Light mode
# keeps the cream/parchment ground (matches the wordmark's own paper
# background, a deliberate "different office, same company" editorial
# treatment) with the site's real navy for ink and amber-dim for accent
# (full-brightness amber is too vivid against light cream at body-text
# scale). Dark mode uses the site's navy tokens directly as bg/surface,
# not an independently-chosen near-black.
_CSS = """
:root {
  --bg: #F3EFE5;
  --surface: #FFFFFF;
  --ink: #0a1628;
  --ink-soft: #13233d;
  --muted: #6b7c8d;
  --rule: #E1DACB;
  --accent: #c4881e;
  --accent-ink: #FFFFFF;
  --shadow: 0 1px 2px rgba(10,22,40,0.06), 0 10px 28px -16px rgba(10,22,40,0.24);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0a1628;
    --surface: #13233d;
    --ink: #eef1f4;
    --ink-soft: #b8c2cc;
    --muted: #6b7c8d;
    --rule: #0d1e35;
    --accent: #e8a124;
    --accent-ink: #0a1628;
    --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 14px 34px -16px rgba(0,0,0,0.6);
  }
}
:root[data-theme="dark"] {
  --bg: #0a1628;
  --surface: #13233d;
  --ink: #eef1f4;
  --ink-soft: #b8c2cc;
  --muted: #6b7c8d;
  --rule: #0d1e35;
  --accent: #e8a124;
  --accent-ink: #0a1628;
  --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 14px 34px -16px rgba(0,0,0,0.6);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Newsreader", Georgia, serif;
  line-height: 1.62;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); }

:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
}
.sr-only:focus, .sr-only:focus-within {
  position: fixed; top: 0.75rem; left: 0.75rem; z-index: 100;
  width: auto; height: auto; margin: 0; overflow: visible; clip: auto;
  white-space: normal;
  background: var(--accent); color: var(--accent-ink);
  padding: 0.6rem 1rem; border-radius: 4px;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.78rem; text-decoration: none;
}

@media (prefers-reduced-motion: reduce) {
  * {
    animation-duration: 0.01ms !important; animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important; scroll-behavior: auto !important;
  }
}

.masthead { border-bottom: 1px solid var(--rule); padding: 1.1rem 1.5rem; }
.masthead-inner {
  max-width: 760px; margin: 0 auto;
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; flex-wrap: wrap;
}
/* wordmark.png reworked 2026-08-31: background removed (was opaque
   cream/paper) and cropped tight to the actual mark -- see git history
   for the extraction. Renders directly on both themes now, no plaque
   wrapper needed. */
.wordmark-link { display: block; line-height: 0; }
.wordmark-link img { height: 40px; width: auto; display: block; }

.masthead nav {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase;
  display: flex; gap: 1.5rem; align-items: center;
}
.masthead nav a { color: var(--muted); text-decoration: none; }
.masthead nav a:hover, .masthead nav a:focus-visible { color: var(--ink); }
.masthead nav a.subscribe {
  color: var(--accent-ink); background: var(--accent);
  padding: 0.35rem 0.7rem; border-radius: 3px;
}

article { max-width: 620px; margin: 0 auto; padding: 3.5rem 1.5rem 2rem; }
.kicker {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 1.1rem;
}
h1.headline {
  font-family: "Fraunces", Georgia, serif; font-optical-sizing: auto;
  font-weight: 500; font-size: clamp(2rem, 5.5vw, 2.9rem); line-height: 1.12;
  letter-spacing: -0.01em; margin: 0 0 1.4rem; text-wrap: balance;
}
.dek {
  font-size: 1.08rem; color: var(--ink-soft); font-style: italic;
  margin: 0 0 1.8rem; max-width: 56ch;
}
.byline {
  display: flex; align-items: center; gap: 0.7rem;
  padding-bottom: 2.2rem; margin-bottom: 2.2rem;
  border-bottom: 1px solid var(--rule);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.78rem; color: var(--muted);
}
.byline .name { color: var(--ink-soft); font-weight: 500; }
.byline .sep { color: var(--rule); }

.prose { font-size: 1.13rem; }
.prose p { margin: 0 0 1.4rem; }
.prose p:first-of-type::first-letter {
  font-family: "Fraunces", Georgia, serif; font-weight: 600;
  font-size: 3.4em; float: left; line-height: 0.85;
  padding: 0.05em 0.08em 0 0; color: var(--accent);
}

.archive { max-width: 760px; margin: 2.5rem auto 0; padding: 0 1.5rem 4rem; }
.archive-head {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.72rem; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--muted); border-top: 1px solid var(--rule);
  padding-top: 2.5rem; margin-bottom: 1.5rem;
}
.archive-list { display: flex; flex-direction: column; }
.archive-item {
  display: grid; grid-template-columns: 6.5rem 1fr; gap: 1.25rem;
  padding: 1.15rem 0; border-bottom: 1px solid var(--rule);
  text-decoration: none; color: inherit;
}
.archive-item:last-child { border-bottom: none; }
.archive-item .date {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.74rem; color: var(--muted); padding-top: 0.2rem;
}
.archive-item .title {
  font-family: "Fraunces", Georgia, serif; font-weight: 500;
  font-size: 1.12rem; color: var(--ink); margin: 0 0 0.3rem; text-wrap: balance;
  transition: color 0.15s ease;
}
.archive-item .excerpt { font-size: 0.92rem; color: var(--muted); margin: 0; }
.archive-item:hover .title, .archive-item:focus-visible .title { color: var(--accent); }

.hero {
  max-width: 760px; margin: 0 auto; padding: 3rem 1.5rem 0;
}
.hero .dek { max-width: 60ch; }

footer.colophon {
  max-width: 760px; margin: 0 auto; padding: 2rem 1.5rem 3rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.7rem; letter-spacing: 0.04em; color: var(--muted);
  border-top: 1px solid var(--rule);
}

@media (max-width: 520px) {
  .archive-item { grid-template-columns: 1fr; gap: 0.3rem; }
}

/* Context-size pass 2026-09-01: tighten chrome on narrow phones so it
   doesn't just look like a shrunk-down desktop layout (the real cause of
   that was the missing viewport meta, fixed in _DOC_OPEN below -- this is
   the remaining polish on top of a page that now actually lays out
   natively at phone widths). */
@media (max-width: 420px) {
  .masthead { padding: 0.85rem 1.1rem; }
  .wordmark-link img { height: 32px; }
  .masthead nav { gap: 0.9rem; font-size: 0.66rem; }
  article { padding: 2.5rem 1.1rem 1.5rem; }
  .hero { padding: 2rem 1.1rem 0; }
  .archive { padding: 0 1.1rem 3rem; }
}
"""

# Real document shell -- 2026-09-01: this previously had no doctype/html/
# head/body at all, just floated straight into <title>/<meta>/<style> and
# relied on every browser's HTML5 error-recovery to invent the rest. That
# meant no declared <html lang>, which a screen reader needs to pick the
# right pronunciation/voice. theme-color/manifest/apple-* meta make this a
# real installable PWA, matching the runner app's approach (see
# src/runner/frontend/index.html) with its own icon set instead of copying
# that app's icons wholesale.
_DOC_OPEN = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#F3EFE5">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0a1628">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{site_title}">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" sizes="32x32" href="/icons/favicon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png">
<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Newsreader:ital,wght@0,400;0,500;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{css}</style>
</head>
<body>
<a class="sr-only" href="#main">Skip to content</a>"""

_DOC_CLOSE = """
<script>
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js"));
}
</script>
</body>
</html>
"""

_MASTHEAD = """<header class="masthead">
  <div class="masthead-inner">
    <a href="/" class="wordmark-link"><img src="/{wordmark}" alt="The Executive Standard"></a>
    <nav>
      <a href="/">Home</a>
      <a href="{subscribe_url}" class="subscribe">Subscribe</a>
    </nav>
  </div>
</header>"""


def _fmt_date(date_iso: str) -> str:
    dt = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
    return dt.strftime("%B %-d, %Y")


def render_post(post: Post, archive_teaser: list[Post]) -> str:
    """Full post page: masthead, article, subscribe block, archive teaser."""
    teaser_html = "\n".join(
        f'''    <a href="/{p.slug}.html" class="archive-item">
      <span class="date">{_fmt_date(p.date_iso).replace(", ", " ").split()[0][:3]} {_fmt_date(p.date_iso).split()[1].rstrip(",")}</span>
      <span>
        <p class="title">{p.title}</p>
        <p class="excerpt">{p.dek}</p>
      </span>
    </a>'''
        for p in archive_teaser
    )
    return f"""{_DOC_OPEN.format(title=f"{post.title} — {SITE_TITLE}", css=_CSS, site_title=SITE_TITLE)}

{_MASTHEAD.format(wordmark=WORDMARK_PATH, subscribe_url=SUBSCRIBE_URL)}

<main id="main">
<article>
  <p class="kicker">{post.kicker}</p>
  <h1 class="headline">{post.title}</h1>
  <p class="dek">{post.dek}</p>
  <div class="byline">
    <span class="name">the operator</span>
    <span class="sep">·</span>
    <span>{_fmt_date(post.date_iso)}</span>
  </div>

  <div class="prose">
{post.body_html}
  </div>
</article>

<div class="archive">
  <p class="archive-head">More memos</p>
  <div class="archive-list">
{teaser_html}
  </div>
</div>
</main>

<footer class="colophon">The Executive Standard · self-hosted at executivestandard.example.com · also on Substack</footer>
{_DOC_CLOSE}"""


_SITE_URL = "https://executivestandard.example.com"


def render_markdown(post: "Post") -> str:
    """Markdown representation for Accept: text/markdown content
    negotiation (2026-09-02, per developers.cloudflare.com/fundamentals/
    reference/markdown-for-agents/ and isitagentready.com's markdown-
    negotiation skill). YAML frontmatter + body, the same "predictable
    three-part layout" those specs describe minus the optional JSON-LD
    block (nothing on this site emits JSON-LD to carry over).

    Falls back to a plain-text strip of body_html when body_md wasn't
    populated (shouldn't happen in practice -- both post sources set it,
    see executive_standard_sync.py -- but never emit an empty document).
    """
    dek = _html.unescape(post.dek)
    body = post.body_md.strip() if post.body_md else re.sub(r"<[^>]+>", "", post.body_html).strip()
    # Pi-native sources embed their own "# Title" line (see
    # load_pi_native_posts()'s documented file format) -- strip a leading
    # H1 so it isn't duplicated against the one this function adds below.
    # A no-op for Substack-sourced posts, whose html2text conversion
    # doesn't carry a title heading in the body to begin with.
    body = re.sub(r"^#\s+.+\n+", "", body, count=1)
    frontmatter = (
        "---\n"
        f"title: {title_yaml_escape(post.title)}\n"
        f"description: {title_yaml_escape(dek)}\n"
        f"date: {post.date_iso}\n"
        f"source: {_SITE_URL}/{post.slug}.html\n"
        "---\n"
    )
    return f"{frontmatter}\n# {post.title}\n\n{body}\n"


def title_yaml_escape(s: str) -> str:
    """Quote a scalar for single-line YAML frontmatter if it contains
    anything that would otherwise need escaping (colon, quote)."""
    if any(c in s for c in (':', '"', "'", "\n")):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def render_index_markdown(posts: list[Post]) -> str:
    """Markdown representation of the homepage/archive listing -- same
    content-negotiation path as render_markdown(), for the root URL."""
    posts_by_date = sorted(posts, key=lambda p: p.date_iso, reverse=True)
    lines = [
        "---",
        f"title: {title_yaml_escape(SITE_TITLE)}",
        f"source: {_SITE_URL}/",
        "---",
        "",
        f"# {SITE_TITLE}",
        "",
        SITE_TAGLINE,
        "",
    ]
    for p in posts_by_date:
        dek = _html.unescape(p.dek)
        lines.append(f"- [{p.title}]({_SITE_URL}/{p.slug}.html) ({_fmt_date(p.date_iso)}) -- {dek}")
    return "\n".join(lines) + "\n"


def render_llms_txt(posts: list[Post]) -> str:
    """AI-agent discoverability doc (llmstxt.org convention), same pattern
    as the parent site's llms.txt -- regenerated every sync so it always
    lists the current set of memos, never goes stale by hand."""
    posts_by_date = sorted(posts, key=lambda p: p.date_iso, reverse=True)
    lines = [
        f"# {SITE_TITLE}",
        "",
        f"> {SITE_TAGLINE} Self-hosted, canonical mirror of the "
        f"CorporateTravelDC Substack -- this is the original; Substack is "
        f"the syndicated copy.",
        "",
        "## Memos",
        "",
    ]
    for p in posts_by_date:
        # llms.txt is plain text, not HTML -- decode entities (Substack's
        # feed gives dek text with raw &#8212;/&#8217; etc.) so a plain-text
        # reader doesn't see literal entity garbage.
        dek = _html.unescape(p.dek)
        lines.append(f"- [{p.title}]({_SITE_URL}/{p.slug}.html): {dek}")
    lines += [
        "",
        "## Contact",
        "",
        "Parent firm: https://example.com/contact.html",
        "",
    ]
    return "\n".join(lines)


def render_sitemap_xml(posts: list[Post]) -> str:
    """Regenerated every sync -- always lists exactly the memos currently
    published, no manual upkeep."""
    posts_by_date = sorted(posts, key=lambda p: p.date_iso, reverse=True)
    urls = [f"""  <url>
    <loc>{_SITE_URL}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>"""]
    for p in posts_by_date:
        urls.append(f"""  <url>
    <loc>{_SITE_URL}/{p.slug}.html</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>""")
    body = "\n".join(urls)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}
</urlset>
"""


def render_index(posts: list[Post]) -> str:
    """Homepage/archive: masthead, hero blurb, full chronological list."""
    items_html = "\n".join(
        f'''    <a href="/{p.slug}.html" class="archive-item">
      <span class="date">{_fmt_date(p.date_iso).split()[0][:3]} {_fmt_date(p.date_iso).split()[1].rstrip(",")}</span>
      <span>
        <p class="title">{p.title}</p>
        <p class="excerpt">{p.dek}</p>
      </span>
    </a>'''
        for p in posts
    )
    return f"""{_DOC_OPEN.format(title=SITE_TITLE, css=_CSS, site_title=SITE_TITLE)}

{_MASTHEAD.format(wordmark=WORDMARK_PATH, subscribe_url=SUBSCRIBE_URL)}

<main id="main">
<h1 class="sr-only">{SITE_TITLE}</h1>
<div class="hero">
  <p class="kicker">The Desk</p>
  <p class="dek">{SITE_TAGLINE}</p>
</div>

<div class="archive">
  <p class="archive-head">All memos</p>
  <div class="archive-list">
{items_html}
  </div>
</div>
</main>

<footer class="colophon">The Executive Standard · self-hosted at executivestandard.example.com · also on Substack</footer>
{_DOC_CLOSE}"""
