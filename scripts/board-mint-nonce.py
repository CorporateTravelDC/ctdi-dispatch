#!/usr/bin/env python3
"""
board-mint-nonce.py -- mint a single-use board enrollment nonce (dispatch side).

Run on the Pi:
    cd /opt/corporatetraveldc/private/ctdi-dispatch-internal
    PYTHONPATH=src python3 scripts/board-mint-nonce.py [--ttl-min 10] [--label cowork-opml]

Prints an enroll URL to hand OUT-OF-BAND to the session that needs board-write
access. The URL is NOT the secret -- the first GET consumes it and returns a
short-lived board-write token; any second GET (replay/leak) returns 410. The
nonce also expires on its own after --ttl-min (default 10) minutes.
"""
import argparse

from common import db

BASE = "https://dispatch.example.com"


def main() -> None:
    ap = argparse.ArgumentParser(description="Mint a single-use board enrollment nonce.")
    ap.add_argument("--ttl-min", type=int, default=10, help="nonce time-to-live in minutes (default 10)")
    ap.add_argument("--label", default=None, help="optional label recorded with the nonce/token (e.g. 'cowork-opml')")
    args = ap.parse_args()

    r = db.board_mint_nonce(ttl_s=args.ttl_min * 60, label=args.label)
    url = f"{BASE}/api/v1/board/enroll?nonce={r['nonce']}"

    print(f"Board enrollment nonce minted -- single-use, expires in {args.ttl_min} min.")
    print("Hand THIS URL to the session out-of-band (it is not the secret; the")
    print("first GET returns a short-lived board-write token, then it's dead):")
    print()
    print(f"  {url}")
    print()
    print("The session does:  GET <url>  ->  {token, expires_at, scope:'board-write'}")
    print("then sends that token as the X-Board-Key header on POST /api/v1/board.")
    print("Any second GET on the same URL -> 410. Re-run this script to enroll again.")


if __name__ == "__main__":
    main()
