"""
second_brain_rss -- RSS/Atom poller feeding corporatetraveldc/00-Inbox/rss/
in the second-brain vault.

2026-07-28: no longer a no-op. Seeded from shared.rss_catalog.all_feed_urls()
-- the same built-in catalog (all categories, including advanced_air_mobility
/ the FAA UTM feeds) and operator-added custom feeds (user_rss_feeds.json)
that the PWA Intel tab shows and churns on. Operator request: churn on "the
existing intel and anything added to the dispatch's rss pool" -- since this
reads the shared catalog live on every run rather than a static copy, any
feed added via the PWA or the built-in catalog going forward is picked up
automatically, no re-seeding needed.

Config:
  SECOND_BRAIN_RSS_FEEDS in dispatch.env -- optional comma-separated EXTRA
  feed URLs, additive on top of the shared catalog (not a replacement for
  it). Leave unset if the shared catalog covers everything you want ingested.

No new pip dependency on purpose (matching second_brain.client_entity_ingest's
stated rationale) -- stdlib xml.etree handles basic RSS 2.0 <item> parsing
fine for a feed reader this simple; extend to real Atom/RSS-edge-case
handling only if a feed actually needs it.
"""
import hashlib
import logging
import os
import sqlite3
from xml.etree import ElementTree as ET

import httpx

from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "second-brain-rss"

# Cap per-feed, per-run item count -- avoids flooding the inbox the first
# time a feed with years of backlog gets configured.
_MAX_ITEMS_PER_FEED = 10


def _feed_urls() -> list[str]:
    from shared.rss_catalog import all_feed_urls

    urls = list(all_feed_urls())
    raw = os.getenv("SECOND_BRAIN_RSS_FEEDS", "").strip()
    for u in (u.strip() for u in raw.split(",") if u.strip()):
        if u not in urls:
            urls.append(u)
    return urls


def _parse_rss_items(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title or link:
            items.append({"title": title, "link": link, "description": desc, "pubDate": pub})
    return items


def main() -> None:
    status = "error"
    feeds = _feed_urls()

    if not feeds:
        log.info(
            "%s: no feeds configured (SECOND_BRAIN_RSS_FEEDS empty) -- "
            "awaiting operator input, no-op", SKILL_NAME,
        )
        return

    conn = sqlite3.connect(INDEX_DB)
    init_vault_db(conn)
    written = 0

    try:
        for url in feeds:
            try:
                resp = httpx.get(url, timeout=20)
                resp.raise_for_status()
                items = _parse_rss_items(resp.content)
            except Exception as e:
                log.warning("%s: failed to fetch/parse %s: %s", SKILL_NAME, url, e)
                continue

            for it in items[:_MAX_ITEMS_PER_FEED]:
                try:
                    body = gate(
                        f"{it['title']}\n\n{it['description']}\n\n{it['link']}",
                        source=SKILL_NAME,
                    )
                except ScrubGateBlocked as e:
                    log.error("%s: item BLOCKED by scrub gate: %s", SKILL_NAME, e)
                    continue

                item_id = hashlib.sha256((it["link"] or it["title"]).encode()).hexdigest()[:16]
                rel_path = f"{webdav_client.BUSINESS_ROOT}/00-Inbox/rss/{item_id}.md"
                if webdav_client.get(rel_path) is not None:
                    continue  # already ingested

                frontmatter = (
                    "---\n"
                    f"source_feed: {url}\n"
                    f"pub_date: {it['pubDate']}\n"
                    "ingest_method: rss\n"
                    "triaged: false\n"
                    "---\n\n"
                )
                note = frontmatter + f"# {it['title']}\n\n{body}\n"
                webdav_client.put(rel_path, note)
                index_note(
                    conn, rel_path, title=it["title"], content=note,
                    tags="rss,untriaged", ingest_method="rss",
                )
                written += 1

        status = "ok"
        log.info("%s: %d new items written to 00-Inbox/rss/", SKILL_NAME, written)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
