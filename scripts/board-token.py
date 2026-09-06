#!/usr/bin/env python3
"""
board-token.py -- operator management of minted board tokens (dispatch side).

Run on the Pi:
    cd /opt/corporatetraveldc/private/ctdi-dispatch-internal
    PYTHONPATH=src python3 scripts/board-token.py mint-read --label cowork-scheduled-research [--ttl-days 30]
    PYTHONPATH=src python3 scripts/board-token.py list [--all]
    PYTHONPATH=src python3 scripts/board-token.py revoke --label cowork-scheduled-research
    PYTHONPATH=src python3 scripts/board-token.py revoke --hash-prefix 3f9a1c

mint-read (2026-09-06): a READ-ONLY, long-lived, non-rotating token for a
consumer that cold-boots with no state -- Cowork's scheduled research run
cannot enroll via nonce or hold a daily-rotating token, so the operator mints
this once and pastes it into the scheduled task's environment out-of-band.
Scope board-read satisfies only GET /api/v1/board (gated threads) and
/api/v1/vault/research{,/list}; it can never post, and /refresh refuses it.
The plaintext is printed exactly ONCE here and stored hashed. Revoke with
`revoke --label ...` the moment it is no longer needed or suspected leaked;
re-mint on expiry (default 30 d -- operator decision) and re-paste.

Nothing here prints a stored secret: `list` shows hash prefixes only.
"""
import argparse
import datetime as _dt

from common import db


def _ts(t: float | None) -> str:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ") if t else "-"


def main() -> None:
    ap = argparse.ArgumentParser(description="Manage minted board tokens.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mint-read", help="mint a read-only token (printed once)")
    m.add_argument("--label", required=True, help="who/what holds it, e.g. cowork-scheduled-research")
    m.add_argument("--ttl-days", type=int, default=30, help="lifetime in days (default 30)")

    ls = sub.add_parser("list", help="inventory (hash prefixes only)")
    ls.add_argument("--all", action="store_true", help="include expired/revoked")

    rv = sub.add_parser("revoke", help="expire active token(s) now")
    g = rv.add_mutually_exclusive_group(required=True)
    g.add_argument("--label")
    g.add_argument("--hash-prefix")

    args = ap.parse_args()

    if args.cmd == "mint-read":
        r = db.board_mint_read_token(ttl_s=args.ttl_days * 86400, label=args.label)
        print(f"Read-only board token minted -- scope {r['scope']}, label {r['label']!r}, "
              f"expires {_ts(r['expires_at'])}.")
        print("Shown ONCE. Paste it into the consumer's environment out-of-band; it is")
        print("stored hashed here and cannot be recovered -- re-mint if lost.")
        print()
        print(f"  {r['token']}")
        print()
        print("The consumer sends it as the X-Board-Key header on GET /api/v1/board?thread=research")
        print("and GET /api/v1/vault/research{,/list}. It cannot POST and cannot be refreshed.")
        return

    if args.cmd == "list":
        rows = db.board_list_tokens(include_expired=args.all)
        if not rows:
            print("no tokens" + ("" if args.all else " active"))
            return
        print(f"{'hash':12} {'scope':12} {'label':32} {'created':17} {'expires':17} {'via':20} state")
        for r in rows:
            print(f"{r['hash_prefix']:12} {r['scope'] or '-':12} {(r['label'] or '-')[:32]:32} "
                  f"{_ts(r['created_at']):17} {_ts(r['expires_at']):17} {(r['via'] or '-')[:20]:20} "
                  f"{'active' if r['active'] else 'expired'}")
        return

    if args.cmd == "revoke":
        n = db.board_revoke_token(label=args.label, hash_prefix=args.hash_prefix)
        print(f"revoked {n} active token(s)")


if __name__ == "__main__":
    main()
