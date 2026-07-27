"""
second_brain_rss -- RSS/Atom poller feeding corporatetraveldc/00-Inbox/rss/
in the second-brain vault. Config-driven and currently a no-op: no feed
URLs are configured yet -- see docs/SECOND_BRAIN_STATUS.md, "Original plan
vs. what's built". This needs Corey to actually supply feed URLs before it
does anything; the code is real and ready, the feed list is not.

Config:
  SECOND_BRAIN_RSS_FEEDS in dispatch.env -- comma-separated feed URLs.
  Empty/unset -> logs and returns, same "awaiting_credentials"-style
  pattern already used by the eurocontrol/jasdat/notam fetchers for
  not-yet-configured feeds.

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
    raw = os.getenv("SECOND_BRAIN_RSS_FEEDS", "").strip()
    return [u.strip() for u in raw.split(",") if u.strip()]


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
