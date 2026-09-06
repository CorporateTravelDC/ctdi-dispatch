#!/usr/bin/env python3
"""
podcast-feed-resolve.py -- resolve a podcast show NAME to its real,
subscribable RSS feed URL, checking Apple's public iTunes Search API first
(free, keyless, broadest coverage -- most shows with a real feed are
indexed there regardless of where someone actually listens), then
PodcastIndex.org as a fallback for shows iTunes misses.

Built for the exact case that prompted it (2026-08-16): a show tracked via
a Snipd share link (https://share.snipd.com/show/<uuid>) has no feed
exposed in its own page (JS-rendered SPA shell, no static <link> tag) --
Snipd is a listening/clipping app, not a feed host, and has no public API
to resolve against. Same reasoning ruled out Google Podcasts (shut down
2024) and Stitcher (shut down 2023) as lookup sources -- nothing to query.
Spotify's public API was also considered and rejected: it generally does
NOT return a subscribable external feed URL for arbitrary shows (a lot of
Spotify content is deliberately exclusive/walled), so it wouldn't solve
this problem even with API credentials.

PodcastIndex.org fallback (2026-08-16): a genuinely open, purpose-built
podcast directory API -- unlike iTunes, it requires a free API key+secret
pair (register at https://podcastindex.org/developer, never mint this
autonomously -- same credential-hygiene rule as every other secret this
session). Reads PODCASTINDEX_API_KEY and PODCASTINDEX_API_SECRET from the
environment; if either is unset, PodcastIndex is silently skipped (not an
error) and results are iTunes-only.

Usage:
    python3 scripts/podcast-feed-resolve.py "<show name to search for>"

Prints every match found (source-tagged) as JSON. Does NOT add anything to
rss_catalog.py automatically -- verify the feedUrl actually returns real
RSS/Atom XML from THIS network before adding it (see rss_catalog.py's own
repeated caution about bot-gated/broken feeds that look right from
elsewhere but 404/HTML here).
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request


def resolve_itunes(term: str, limit: int = 5) -> list[dict]:
    url = (
        "https://itunes.apple.com/search?"
        + urllib.parse.urlencode({"term": term, "media": "podcast", "limit": limit})
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return [
        {
            "source": "itunes",
            "name": r.get("collectionName"),
            "artist": r.get("artistName"),
            "feedUrl": r.get("feedUrl"),
            "genre": r.get("primaryGenreName"),
            "episode_count": r.get("trackCount"),
            "latest_release": r.get("releaseDate"),
            "show_url": r.get("collectionViewUrl"),
        }
        for r in data.get("results", [])
        if r.get("feedUrl")
    ]


def resolve_podcastindex(term: str, limit: int = 5) -> list[dict]:
    api_key = os.environ.get("PODCASTINDEX_API_KEY", "").strip()
    api_secret = os.environ.get("PODCASTINDEX_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return []

    epoch = str(int(time.time()))
    auth_hash = hashlib.sha1((api_key + api_secret + epoch).encode()).hexdigest()
    url = (
        "https://api.podcastindex.org/api/1.0/search/byterm?"
        + urllib.parse.urlencode({"q": term, "max": limit})
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "corporatetraveldc-dispatch/podcast-feed-resolve",
        "X-Auth-Date": epoch,
        "X-Auth-Key": api_key,
        "Authorization": auth_hash,
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return [
        {
            "source": "podcastindex",
            "name": f.get("title"),
            "artist": f.get("author"),
            "feedUrl": f.get("url"),
            "genre": ", ".join((f.get("categories") or {}).values()) or None,
            "episode_count": f.get("episodeCount"),
            "latest_release": f.get("newestItemPubdate"),
            "show_url": f.get("link"),
        }
        for f in data.get("feeds", [])
        if f.get("url")
    ]


def resolve(term: str, limit: int = 5) -> list[dict]:
    matches = resolve_itunes(term, limit)
    if not matches:
        try:
            matches = resolve_podcastindex(term, limit)
        except Exception as e:
            print(f"PodcastIndex fallback failed (non-fatal): {e}", file=sys.stderr)
    return matches


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("term", help="Show name to search for")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    matches = resolve(args.term, args.limit)
    if not matches:
        pi_configured = bool(os.environ.get("PODCASTINDEX_API_KEY") and
                              os.environ.get("PODCASTINDEX_API_SECRET"))
        print(f"No podcast match found for {args.term!r} in iTunes"
              f"{' or PodcastIndex' if pi_configured else ''} "
              f"(or it has no indexed feedUrl -- these indexes sometimes "
              f"omit it even for real shows)."
              f"{'' if pi_configured else ' PodcastIndex was skipped -- '}"
              f"{'' if pi_configured else 'PODCASTINDEX_API_KEY/SECRET not set.'}",
              file=sys.stderr)
        sys.exit(1)
    print(json.dumps(matches, indent=2))


if __name__ == "__main__":
    main()
