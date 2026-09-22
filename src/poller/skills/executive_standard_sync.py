"""
executive-standard-sync -- regenerates the self-hosted static mirror at
executivestandard.example.com (nginx serves this directory
directly, see nginx/conf.d/executivestandard.example.com.conf)
from TWO sources, merged:

1. corporatetraveldc.substack.com/feed -- the 14-post legacy archive this
   site was originally backfilled from (confirmed live 2026-08-31: this IS
   the full archive, not a recent-N-items window), plus a defensive
   fallback for anything that ever gets posted to Substack first again.
2. ORIGINALS_DIR (2026-09-01) -- posts authored directly for the Pi, which
   is now the canonical source per operator direction: cowork drafts an
   article, the operator hands the finished text to Claude, Claude writes
   it into ORIGINALS_DIR and re-runs this sync, and it's live immediately
   -- Substack cross-posting happens later (16-24h, manually, on the
   operator's own schedule), never the other way around. See
   load_pi_native_posts()'s docstring for the file format.

On a slug collision, the Pi-native original wins (it's canonical) and the
Substack version is dropped -- this only matters if something is ever
posted to Substack first by mistake, or during the transition period.

One post ("Skills Are Not Systems") is paid-tier and comes through
truncated in the public Substack feed -- the operator supplied the full
original text directly for that one; this skill does not attempt to
scrape around the paywall. Same OVERRIDES_DIR mechanism as before,
unrelated to ORIGINALS_DIR (overrides patch an existing Substack-sourced
post's body; originals are whole posts that never came from Substack
at all).

Idempotent: always regenerates every file from current source state
(cheap -- a few dozen posts, no incremental-diff complexity needed at
this volume). Schedule: not yet wired to a timer -- run manually
(`python3 src/poller/skills/executive_standard_sync.py`) until the
operator wants it recurring.

Site directory (2026-08-31): lives at /var/www/executivestandard.example.com,
same as example.com itself -- NOT under /var/lib/corporatetraveldc.
That tree already carries a container_file_t SELinux default (for the
podman bind-mounts elsewhere under it), which fought any httpd-served
subdirectory nested inside it: every file this script wrote inherited
container_file_t from the parent on creation, needing a manual
`restorecon` after every single sync. /var/www is root-owned
(operator pre-created and chowned it once) and covered by SELinux's own
stock httpd_sys_content_t default, so nothing here ever needs relabeling.
"""
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import html2text
import requests

from executive_standard.render import (
    Post, render_post, render_index, render_llms_txt, render_sitemap_xml,
    render_markdown, render_index_markdown,
)

# 2026-09-02: Accept: text/markdown content negotiation needs a real
# markdown source per post. Pi-native posts already have one (their own
# .md file, see load_pi_native_posts() below); Substack-sourced posts
# only ever had body_html, so those get converted once here at sync time
# rather than re-converted per-request (this is a static site -- there's
# no per-request handler to do it lazily in).
_h2t = html2text.HTML2Text()
_h2t.body_width = 0          # don't hard-wrap -- let it read as normal prose
_h2t.ignore_images = False
_h2t.unicode_snob = True     # keep real em-dashes/curly quotes, not ascii approximations


def _html_to_markdown(body_html: str) -> str:
    return _h2t.handle(body_html).strip()

log = logging.getLogger(__name__)

# Substack embeds its own subscribe-CTA widget directly into a post's stored
# HTML (sometimes more than once per post) -- confirmed live 2026-09-01 by
# inspecting the raw feed: a self-contained
# <div class="subscription-widget-wrap-editor">...</div> block, always
# closing with this exact tag sequence regardless of the caption text inside
# it. We render our own single top-nav Subscribe link instead, so every one
# of these gets stripped rather than rendered inline mid-article.
_SUBSCRIBE_WIDGET_RE = re.compile(
    r'<div class="subscription-widget-wrap-editor".*?</div></div></form></div></div>',
    re.DOTALL,
)

FEED_URL = "https://corporatetraveldc.substack.com/feed"
SITE_DIR = Path("/var/www/executivestandard.example.com")
_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


def _slug_from_link(link: str) -> str:
    # https://corporatetraveldc.substack.com/p/<slug> -> <slug>
    m = re.search(r"/p/([^/?]+)", link or "")
    return m.group(1) if m else re.sub(r"[^a-z0-9]+", "-", (link or "untitled").lower()).strip("-")


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def fetch_feed_posts() -> list[Post]:
    resp = requests.get(FEED_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    posts = []
    for item in root.find("channel").findall("item"):
        title = (item.find("title").text or "").strip()
        link = (item.find("link").text or "").strip()
        pub_date = item.find("pubDate").text or ""
        desc = _strip_html(item.find("description").text or "")
        enc = item.find("content:encoded", _NS)
        body_html = (enc.text or "").strip() if enc is not None else ""
        body_html = _SUBSCRIBE_WIDGET_RE.sub("", body_html)

        # RFC 2822 pubDate -> ISO 8601 (RSS gives e.g. "Sat, 22 Aug 2026 22:33:47 GMT")
        from email.utils import parsedate_to_datetime
        try:
            dt = parsedate_to_datetime(pub_date)
            date_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            date_iso = pub_date

        posts.append(Post(
            slug=_slug_from_link(link),
            title=title,
            dek=desc,
            date_iso=date_iso,
            body_html=body_html,
            body_md=_html_to_markdown(body_html),
        ))
    return posts


def _markdown_to_html(md: str) -> str:
    """Minimal converter for the one operator-supplied full-text post
    (simple paragraphs + *italic* emphasis only -- not a general-purpose
    markdown parser, deliberately narrow to what this one document uses)."""
    # drop the leading H1 (title is rendered separately by the template)
    md = re.sub(r"^#\s+.*\n+", "", md.strip())
    blocks = [b.strip() for b in md.split("\n\n") if b.strip()]
    out = []
    for b in blocks:
        if b == "---":
            continue
        b = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", b)
        b = b.replace("\n", " ")
        out.append(f"<p>{b}</p>")
    return "\n".join(out)


ORIGINALS_DIR = Path("/var/lib/corporatetraveldc/executive-standard-originals")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Tiny hand-rolled front-matter parser -- flat `key: value` lines only,
    no nesting/lists, deliberately narrow to what a post needs (title, dek,
    date, kicker). Not a YAML parser; don't reach for this outside this one
    file format."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("no --- frontmatter block found at top of file")
    fm_block, body = m.groups()
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, body


def load_pi_native_posts() -> list[Post]:
    """Posts authored directly for the Pi (see module docstring). One file
    per post: ORIGINALS_DIR/<slug>.md, slug = filename stem (same
    convention as OVERRIDES_DIR). Format:

        ---
        title: The Post Title
        dek: One-line subtitle/teaser
        date: 2026-09-01
        kicker: Desk Memo
        ---

        # The Post Title

        Body in the same plain-paragraphs-plus-*italic* markdown
        _markdown_to_html() already handles (same style as the existing
        operator-supplied override posts).

    `kicker` is optional (defaults to Post's own default). `date` accepts
    either a bare YYYY-MM-DD (noon UTC is assumed) or a full ISO 8601
    timestamp."""
    if not ORIGINALS_DIR.is_dir():
        return []
    posts = []
    for md_path in sorted(ORIGINALS_DIR.glob("*.md")):
        slug = md_path.stem
        try:
            fm, body_md = _parse_frontmatter(md_path.read_text())
            title = fm["title"]
            dek = fm["dek"]
            date = fm["date"]
        except (ValueError, KeyError) as e:
            log.error("originals/%s: skipping, malformed (%s)", md_path.name, e)
            continue
        date_iso = date if "T" in date else f"{date}T12:00:00Z"
        kwargs = {}
        if fm.get("kicker"):
            kwargs["kicker"] = fm["kicker"]
        posts.append(Post(
            slug=slug,
            title=title,
            dek=dek,
            date_iso=date_iso,
            body_html=_markdown_to_html(body_md),
            body_md=body_md.strip(),
            **kwargs,
        ))
        log.info("loaded Pi-native original: %s", slug)
    return posts


OVERRIDES_DIR = Path("/var/lib/corporatetraveldc/executive-standard-overrides")


def apply_full_text_overrides(posts: list[Post]) -> None:
    """Replace any truncated/paywalled post's body with operator-supplied
    full text, if a <slug>.md file exists in OVERRIDES_DIR. Matched by
    slug, not title (titles can drift). This directory -- not a Claude
    session upload path, which is ephemeral and won't exist on a future
    run -- is the stable home for these; drop a new <slug>.md here
    whenever a future paid post needs the same treatment."""
    if not OVERRIDES_DIR.is_dir():
        return
    by_slug = {p.slug: p for p in posts}
    for md_path in OVERRIDES_DIR.glob("*.md"):
        slug = md_path.stem
        post = by_slug.get(slug)
        if not post:
            log.warning("override file %s: no post with slug=%s found in feed", md_path.name, slug)
            continue
        override_md = md_path.read_text()
        post.body_html = _markdown_to_html(override_md)
        post.body_md = override_md.strip()
        log.info("applied full-text override for %s", slug)


def build_site(posts: list[Post], site_dir: Path) -> None:
    posts_by_date = sorted(posts, key=lambda p: p.date_iso, reverse=True)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "assets").mkdir(exist_ok=True)
    (site_dir / "icons").mkdir(exist_ok=True)

    # Static assets (wordmark, PWA manifest/service-worker, icon set) all
    # ship with the repo -- copy verbatim into the generated site on every
    # sync, same treatment as the wordmark always got.
    assets_root = Path(__file__).resolve().parent.parent.parent / "executive_standard" / "assets"

    src_wordmark = assets_root / "wordmark.png"
    if src_wordmark.exists():
        (site_dir / "assets" / "wordmark.png").write_bytes(src_wordmark.read_bytes())

    for name in ("manifest.json", "sw.js", "robots.txt", "llm.txt"):
        src = assets_root / name
        if src.exists():
            (site_dir / name).write_bytes(src.read_bytes())

    icons_src = assets_root / "icons"
    if icons_src.is_dir():
        for icon_file in icons_src.iterdir():
            (site_dir / "icons" / icon_file.name).write_bytes(icon_file.read_bytes())

    # GPG public keys (2026-09-03, operator directive) -- same verbatim-copy
    # treatment as the rest of assets/ above, so a fresh site_dir rebuild
    # never silently drops them. Canonical set of 5 -- see
    # docs/GPG_KEYS_PUBLISHED.md for what each one is and why. This loop
    # picks up whatever's actually present in assets/keys/ rather than
    # hardcoding filenames, so adding/retiring a key later is just a file
    # change here (plus updating that doc).
    keys_src = assets_root / "keys"
    if keys_src.is_dir():
        (site_dir / "keys").mkdir(exist_ok=True)
        for key_file in keys_src.iterdir():
            (site_dir / "keys" / key_file.name).write_bytes(key_file.read_bytes())

    # llms.txt / sitemap.xml regenerate from the live post list every sync --
    # never go stale by hand as new memos publish.
    (site_dir / "llms.txt").write_text(render_llms_txt(posts_by_date), encoding="utf-8")
    (site_dir / "sitemap.xml").write_text(render_sitemap_xml(posts_by_date), encoding="utf-8")

    (site_dir / "index.html").write_text(render_index(posts_by_date), encoding="utf-8")
    # Accept: text/markdown content negotiation (2026-09-02, operator
    # directive). _md/ subdir, not alongside the .html files -- matches
    # the already-proven pattern on csexecutiveservices-website's own
    # nginx config (see nginx/www.example.com.conf there):
    # nginx serves this directory only via an `internal` location reached
    # through the negotiated rewrite, never directly browsable at its own
    # URL regardless of Accept header. See nginx/conf.d/executivestandard
    # ...conf for the request-side negotiation, render_markdown()/
    # render_index_markdown() for the "why" of the format.
    md_dir = site_dir / "_md"
    md_dir.mkdir(exist_ok=True)
    (md_dir / "index.md").write_text(render_index_markdown(posts_by_date), encoding="utf-8")

    for i, post in enumerate(posts_by_date):
        teaser = [p for p in posts_by_date if p.slug != post.slug][:5]
        (site_dir / f"{post.slug}.html").write_text(render_post(post, archive_teaser=teaser), encoding="utf-8")
        (md_dir / f"{post.slug}.md").write_text(render_markdown(post), encoding="utf-8")

    log.info("executive-standard-sync: wrote %d posts + index to %s", len(posts_by_date), site_dir)


def main() -> None:
    try:
        posts = fetch_feed_posts()
        log.info("executive-standard-sync: fetched %d posts from feed", len(posts))
    except Exception:
        # A Pi-native publish (see load_pi_native_posts) shouldn't be
        # blocked by a transient Substack outage -- log and carry on with
        # whatever's Pi-native only this run rather than crash.
        log.exception("executive-standard-sync: feed fetch failed, continuing with Pi-native posts only")
        posts = []

    apply_full_text_overrides(posts)

    originals = load_pi_native_posts()
    by_slug = {p.slug: p for p in posts}
    for orig in originals:
        if orig.slug in by_slug:
            log.info("Pi-native original %s supersedes the Substack-feed version", orig.slug)
        by_slug[orig.slug] = orig  # Pi-native wins on collision -- it's canonical
    posts = list(by_slug.values())

    build_site(posts, SITE_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
