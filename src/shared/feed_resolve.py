"""
shared/feed_resolve.py -- resolve an arbitrary source URL (YouTube channel
page, Rumble channel page, or a blog homepage) into an actual RSS/Atom feed
URL, so the Intel tile's "Add Feed" flow can accept "paste the channel/blog
link" instead of requiring the operator to already know or hand-construct a
feed URL.

Built 2026-08-03 per operator request: "can we add direct support for Blogs
(with and without rss support natively) and youtube/rumble etc?" Three
source types, three different resolution strategies:

  YouTube  -- native Atom feed, no bridge needed at all:
              https://www.youtube.com/feeds/videos.xml?channel_id=X
              The only work is turning a /@handle, /c/Name, or /user/Name
              URL into the underlying channel_id (a raw /channel/UC... URL
              needs no resolution). Done by fetching the channel page and
              reading its <link rel="canonical"> tag, which YouTube always
              renders server-side regardless of client JS -- confirmed
              live 2026-08-03 against a real channel page fetched with a
              plain UA string, no headless browser needed.

  Rumble   -- via the self-hosted RSS-Bridge instance (rss-bridge.container,
              Tailscale-only, see that Quadlet's header comment) and its
              built-in RumbleBridge (account + type params, confirmed live
              against RSS-Bridge's own ?action=list and a real ?action=display
              call). KNOWN LIMITATION, found during live testing: Rumble's
              own Cloudflare bot protection currently 403s RumbleBridge's
              scrape for every account tested from this Pi's IP (RumbleEvents,
              BjornAndreasBullHansen, TheDailyWire all failed the same way).
              This is an upstream/site-side issue, not a bug in this
              resolver or the RSS-Bridge deployment -- RSS-Bridge itself
              handles it gracefully (returns a valid RSS document with a
              single "Bridge returned error 403!" item rather than
              crashing), and there's an open upstream tracking issue
              (RSS-Bridge/rss-bridge#4474, "Rumble 2.0") about reworking
              this bridge for Rumble's current site behavior. This function
              still constructs and returns the URL -- whether it actually
              yields content depends on Rumble's bot-detection at fetch
              time, which can change without this code changing.

  Native RSS discovery -- for everything else (blogs that DO have RSS but
              don't put a big "Subscribe" link on the page): fetch the
              page and look for <link rel="alternate" type="application/
              (rss|atom)+xml"> tags, same mechanism every browser's
              built-in feed detection uses.

  Blogs with NO RSS at all -- deliberately NOT auto-resolved here. RSS-
              Bridge's CssSelectorBridge can build a feed for those, but it
              requires a hand-picked CSS selector per site (there is no
              generic "any blog, no config" scrape -- see CssSelectorBridge's
              own PARAMETERS, which require url_selector as a required
              field). resolve_source() returns resolved=False with a note
              pointing at RSS-Bridge's own UI for that case rather than
              pretending to fully automate something that structurally
              can't be.
"""
import logging
import os
import re
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

RSS_BRIDGE_BASE = os.environ.get("RSS_BRIDGE_BASE", "http://100.x.x.x:3001")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

_YT_CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")
_YT_CANONICAL_RE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"'
)
_YT_EXTERNAL_ID_RE = re.compile(r'"externalId":"(UC[A-Za-z0-9_-]{22})"')

_FEED_LINK_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]*type=["\']application/(?:rss|atom)\+xml["\'][^>]*href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# Same tag, attributes in the other order (type before rel) -- HTML doesn't
# guarantee attribute order, seen both ways in the wild.
_FEED_LINK_RE_ALT = re.compile(
    r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*rel=["\']alternate["\'][^>]*href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _is_youtube(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "youtube.com" in host or host == "youtu.be"


def _is_rumble(url: str) -> bool:
    return "rumble.com" in urlparse(url).netloc.lower()


async def resolve_youtube(url: str, client: httpx.AsyncClient) -> dict:
    """Given any youtube.com URL for a channel, return the videos.xml feed URL."""
    m = re.search(r"/channel/(UC[A-Za-z0-9_-]{22})", url)
    if m:
        channel_id = m.group(1)
        return {
            "resolved": True,
            "detected_type": "youtube",
            "feed_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            "note": f"Resolved directly from /channel/{channel_id} in the URL, no lookup needed.",
        }

    # /@handle, /c/Name, /user/Name -- need to fetch the page and read its
    # canonical channel URL (YouTube renders this server-side).
    try:
        resp = await client.get(url, headers={"User-Agent": _UA}, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        return {"resolved": False, "detected_type": "youtube",
                "note": f"Could not fetch the YouTube page to resolve its channel ID: {e}"}

    html = resp.text
    m = _YT_CANONICAL_RE.search(html) or _YT_EXTERNAL_ID_RE.search(html)
    if not m:
        return {"resolved": False, "detected_type": "youtube",
                "note": "Fetched the page but could not find a channel ID in it "
                        "(YouTube may have changed its page structure)."}

    channel_id = m.group(1)
    return {
        "resolved": True,
        "detected_type": "youtube",
        "feed_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        "note": f"Resolved {url} -> channel_id {channel_id} via the page's canonical link.",
    }


def resolve_rumble(url: str) -> dict:
    """Given a rumble.com/c/<account> or /user/<account> URL, build the
    RSS-Bridge RumbleBridge feed URL. See module docstring -- Rumble's own
    Cloudflare bot protection currently blocks the actual scrape for every
    account tested; this still returns the constructed URL since the block
    is site-side and may not apply to every account/time."""
    m = re.search(r"rumble\.com/(c|user)/([\w\-_.]+)", url)
    if not m:
        return {"resolved": False, "detected_type": "rumble",
                "note": "Could not find a /c/<name> or /user/<name> account in that Rumble URL."}

    kind, account = m.group(1), m.group(2)
    rtype = "channel" if kind == "c" else "user"
    feed_url = (
        f"{RSS_BRIDGE_BASE}/?action=display&bridge=RumbleBridge"
        f"&account={account}&type={rtype}&format=Mrss"
    )
    return {
        "resolved": True,
        "detected_type": "rumble",
        "feed_url": feed_url,
        "note": (
            f"Built via the self-hosted RSS-Bridge RumbleBridge for account '{account}'. "
            "KNOWN LIMITATION as of 2026-08-03: Rumble's own Cloudflare bot protection "
            "currently blocks this bridge's scrape for every account tested -- the feed "
            "URL is valid and will start working again if/when that changes upstream, "
            "or if RSS-Bridge/rss-bridge#4474 lands a fix. Adding it now is still useful "
            "as forward-compatible plumbing."
        ),
    }


async def discover_native_rss(url: str, client: httpx.AsyncClient) -> dict:
    """Fetch a page and look for its <link rel="alternate" type="application/
    (rss|atom)+xml"> feed-discovery tag -- the same mechanism browsers use."""
    try:
        resp = await client.get(url, headers={"User-Agent": _UA}, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        return {"resolved": False, "detected_type": "unresolved",
                "note": f"Could not fetch {url}: {e}"}

    html = resp.text
    m = _FEED_LINK_RE.search(html) or _FEED_LINK_RE_ALT.search(html)
    if not m:
        return {
            "resolved": False,
            "detected_type": "unresolved",
            "note": (
                "No RSS/Atom <link> tag found on that page. This site may not "
                "publish RSS at all -- for a true no-RSS blog, use RSS-Bridge's "
                f"own CSS-Selector bridge directly at {RSS_BRIDGE_BASE}/ "
                "(requires picking a CSS selector for article links by hand, "
                "there's no way to fully automate that for an arbitrary site)."
            ),
        }

    feed_url = urljoin(str(resp.url), m.group(1))
    return {
        "resolved": True,
        "detected_type": "native_rss",
        "feed_url": feed_url,
        "note": f"Found a native RSS/Atom feed link on the page: {feed_url}",
    }


async def resolve_source(url: str) -> dict:
    """Main entry point. Given any source URL an operator might paste in
    (a YouTube channel, a Rumble channel, or a blog homepage), return
    {resolved: bool, detected_type: str, feed_url: str|None, note: str}."""
    if not url.startswith(("http://", "https://")):
        return {"resolved": False, "detected_type": "unresolved",
                "note": "url must start with http:// or https://"}

    async with httpx.AsyncClient() as client:
        if _is_youtube(url):
            return await resolve_youtube(url, client)
        if _is_rumble(url):
            return resolve_rumble(url)
        return await discover_native_rss(url, client)
