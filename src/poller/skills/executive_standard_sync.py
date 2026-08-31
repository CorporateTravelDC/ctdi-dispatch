"""
executive-standard-sync -- pulls the operator's Substack RSS feed and
regenerates the self-hosted static mirror at
executivestandard.example.com (nginx serves this directory
directly, see nginx/conf.d/executivestandard.example.com.conf).

Design: corporatetraveldc.substack.com/feed carries the operator's full
publication (confirmed live 2026-08-31: 14 posts total, matches the
Substack archive API exactly -- this IS the full archive, not a
recent-N-items window) with full post HTML in <content:encoded> for
free posts. One post ("Skills Are Not Systems") is paid-tier and comes
through truncated in the public feed -- the operator supplied the full
original text directly for that one; this skill does not attempt to
scrape around the paywall.

Idempotent: always regenerates every file from the current feed state
(cheap -- 14 posts, no incremental-diff complexity needed at this
volume). Schedule: not yet wired to a timer -- run manually
(`python3 src/poller/skills/executive_standard_sync.py`) until the
operator wants it recurring.

SELinux note (2026-08-31, confirmed live): this box runs SELinux
Enforcing. `/var/lib/corporatetraveldc/` is `container_file_t` (correct
-- it's bind-mounted into podman containers elsewhere), but nginx runs
as a native systemd service and can only read `httpd_sys_content_t`.
One-time fix (operator, needs root -- this script/skill has neither
sudo nor any business requesting it):
    sudo semanage fcontext -a -t httpd_sys_content_t \
        '/var/lib/corporatetraveldc/executive-standard-site(/.*)?'
    sudo restorecon -Rv /var/lib/corporatetraveldc/executive-standard-site
The `semanage fcontext` rule persists as policy, but individual file
labels can still drift back after this script rewrites every file on a
future run -- re-run `restorecon` (no need to re-add the fcontext rule)
after every sync until this gets wired into a privileged wrapper.
"""
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from common import config
from executive_standard.render import Post, render_post, render_index

log = logging.getLogger(__name__)

FEED_URL = "https://corporatetraveldc.substack.com/feed"
SITE_DIR_NAME = "executive-standard-site"
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
        post.body_html = _markdown_to_html(md_path.read_text())
        log.info("applied full-text override for %s", slug)


def build_site(posts: list[Post], site_dir: Path) -> None:
    posts_by_date = sorted(posts, key=lambda p: p.date_iso, reverse=True)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "assets").mkdir(exist_ok=True)

    # wordmark asset ships with the repo, copy into the generated site
    src_wordmark = (
        Path(__file__).resolve().parent.parent.parent
        / "executive_standard" / "assets" / "wordmark.png"
    )
    if src_wordmark.exists():
        (site_dir / "assets" / "wordmark.png").write_bytes(src_wordmark.read_bytes())

    (site_dir / "index.html").write_text(render_index(posts_by_date))

    for i, post in enumerate(posts_by_date):
        teaser = [p for p in posts_by_date if p.slug != post.slug][:5]
        (site_dir / f"{post.slug}.html").write_text(render_post(post, archive_teaser=teaser))

    log.info("executive-standard-sync: wrote %d posts + index to %s", len(posts_by_date), site_dir)


def main() -> None:
    posts = fetch_feed_posts()
    log.info("executive-standard-sync: fetched %d posts from feed", len(posts))

    apply_full_text_overrides(posts)

    site_dir = Path(config.state_dir()) / SITE_DIR_NAME
    build_site(posts, site_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
