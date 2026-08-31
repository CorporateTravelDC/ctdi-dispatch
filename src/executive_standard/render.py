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

Pure functions only -- no I/O, no DB. The caller (a one-time backfill
script and/or a periodic sync skill) supplies post dicts and writes the
returned strings to disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Post:
    slug: str
    title: str
    dek: str
    date_iso: str          # e.g. "2026-08-20T12:54:01Z"
    body_html: str         # already-safe HTML (paragraphs), not markdown
    kicker: str = "Essay"


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

.subscribe-block {
  max-width: 620px; margin: 3rem auto 0; padding: 1.75rem;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 6px;
  box-shadow: var(--shadow);
  display: flex; align-items: center; justify-content: space-between;
  gap: 1.5rem; flex-wrap: wrap;
}
.subscribe-block p { margin: 0; font-size: 0.98rem; color: var(--ink-soft); max-width: 40ch; }
.subscribe-block a.btn {
  flex-shrink: 0;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.78rem; letter-spacing: 0.06em; text-transform: uppercase;
  text-decoration: none; color: var(--accent-ink); background: var(--accent);
  padding: 0.7rem 1.3rem; border-radius: 4px; white-space: nowrap;
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
"""

_HEAD = """<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Newsreader:ital,wght@0,400;0,500;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{css}</style>"""

_MASTHEAD = """<div class="masthead">
  <div class="masthead-inner">
    <a href="/" class="wordmark-link"><img src="/{wordmark}" alt="The Executive Standard"></a>
    <nav>
      <a href="/">Archive</a>
      <a href="{subscribe_url}" class="subscribe">Subscribe</a>
    </nav>
  </div>
</div>"""


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
    return f"""{_HEAD.format(title=f"{post.title} — {SITE_TITLE}", css=_CSS)}

{_MASTHEAD.format(wordmark=WORDMARK_PATH, subscribe_url=SUBSCRIBE_URL)}

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

  <div class="subscribe-block">
    <p>New essays land here first. Subscribe to get them by email — free, no spam, unsubscribe any time.</p>
    <a href="{SUBSCRIBE_URL}" class="btn">Subscribe on Substack</a>
  </div>
</article>

<div class="archive">
  <p class="archive-head">More essays</p>
  <div class="archive-list">
{teaser_html}
  </div>
</div>

<footer class="colophon">The Executive Standard · self-hosted at executivestandard.example.com · originals on Substack</footer>
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
    return f"""{_HEAD.format(title=SITE_TITLE, css=_CSS)}

{_MASTHEAD.format(wordmark=WORDMARK_PATH, subscribe_url=SUBSCRIBE_URL)}

<div class="hero">
  <p class="kicker">Living archive</p>
  <p class="dek">{SITE_TAGLINE}</p>
</div>

<div class="archive">
  <p class="archive-head">All essays</p>
  <div class="archive-list">
{items_html}
  </div>
</div>

<footer class="colophon">The Executive Standard · self-hosted at executivestandard.example.com · originals on Substack</footer>
"""
