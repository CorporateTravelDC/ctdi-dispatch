#!/usr/bin/env python3
"""
board-presence-ingest.py -- record an ALREADY-GPG-VERIFIED weekly presence
attestation into the DB, and mint a fresh enrollment nonce for the new cycle.

Called by scripts/board-presence-attest.sh AFTER it has verified the
clearsigned attestation itself (isolated GNUPGHOME, same pattern as
verify-manifest.sh) -- this script does NOT re-verify the signature, it
trusts the caller's --issued-at/--valid-until/--key-fingerprint as already
authenticated. Never call this directly with unverified input.
"""
import argparse
from datetime import datetime, timezone

from common import db

BASE = "https://dispatch.example.com"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attestation-file", required=True)
    ap.add_argument("--issued-at", type=float, required=True)
    ap.add_argument("--valid-until", type=float, required=True)
    ap.add_argument("--key-fingerprint", required=True)
    ap.add_argument("--nonce-ttl-min", type=int, default=10)
    ap.add_argument("--label", default="cowork-board-presence")
    args = ap.parse_args()

    with open(args.attestation_file) as f:
        text = f.read()

    db.board_presence_set(text, args.issued_at, args.valid_until, args.key_fingerprint)
    valid_until_iso = datetime.fromtimestamp(args.valid_until, tz=timezone.utc).isoformat()
    print(f"Presence attestation recorded -- valid through {valid_until_iso}")

    r = db.board_mint_nonce(ttl_s=args.nonce_ttl_min * 60, label=args.label)
    url = f"{BASE}/api/v1/board/enroll?nonce={r['nonce']}"
    print()
    print(f"New enrollment nonce minted for this cycle -- single-use, expires in {args.nonce_ttl_min} min.")
    print("Hand THIS URL to Cowork out-of-band to start this week's chain:")
    print()
    print(f"  {url}")
    print()
    print("Cowork does: GET <url> -> {token, expires_at}, then self-rotates daily via")
    print("GET /api/v1/board/refresh (X-Board-Key: <current token>) for up to 7 days,")
    print("until the next weekly attestation.")


if __name__ == "__main__":
    main()
