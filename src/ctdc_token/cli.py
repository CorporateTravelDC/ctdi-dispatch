#!/usr/bin/env python3
"""
ctdc-token — Token management CLI for corporatetraveldc.

Usage:
  ctdc-token create --user <user> --tier <cert|shares|admin> [--label <device>] [--expires <days>]
  ctdc-token list [--all]
  ctdc-token revoke --prefix <ctdc_user_>
  ctdc-token show-cost        Monthly API cost review from SR-1 log.

Token format: ctdc_<user>_<32-char-random>
Plaintext shown once on create; never stored. Hash only in DB.
"""

import argparse
import csv
import collections
import sys
import time
from pathlib import Path

# Ensure src/ is on the path when run directly.
_src = Path(__file__).parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from common import db
from auth.auth import generate_token, revoke_by_prefix


def cmd_create(args: argparse.Namespace) -> None:
    db.init_db()

    expires_at = None
    if args.expires:
        expires_at = time.time() + args.expires * 86400

    token = generate_token(
        user=args.user,
        tier=args.tier,
        device_label=args.label,
        expires_at=expires_at,
    )

    print(f"\nToken created successfully.")
    print(f"  User:   {args.user}")
    print(f"  Tier:   {args.tier}")
    print(f"  Label:  {args.label or '(none)'}")
    if expires_at:
        import datetime
        exp = datetime.datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M UTC")
        print(f"  Expires: {exp}")
    print()
    print(f"Token (shown once — store it now):")
    print(f"  {token}")
    print()
    print("To use in API calls:")
    print(f"  Authorization: Bearer {token}")
    print()
    print("To configure in Cowork:")
    print(f"  CSEX_DISPATCH_TOKEN={token}")
    print()


def cmd_list(args: argparse.Namespace) -> None:
    db.init_db()
    tokens = db.list_tokens(active_only=not args.all)

    if not tokens:
        print("No tokens found.")
        return

    # Table header.
    fmt = "{:<5} {:<20} {:<8} {:<35} {:<12} {:<10}"
    print(fmt.format("ID", "User", "Tier", "Device", "Created", "Status"))
    print("-" * 95)

    import datetime
    for t in tokens:
        created = datetime.datetime.fromtimestamp(
            t["created_at"]
        ).strftime("%Y-%m-%d") if t["created_at"] else "—"

        status = "active"
        if t["revoked_at"]:
            status = "revoked"
        elif t["expires_at"] and t["expires_at"] < time.time():
            status = "expired"

        print(fmt.format(
            t["id"],
            t["user_label"][:20],
            t["tier"],
            (t["device_label"] or "—"),
            created,
            status,
        ))
    print()


def cmd_revoke(args: argparse.Namespace) -> None:
    db.init_db()
    count = revoke_by_prefix(args.prefix)
    if count:
        print(f"Revoked {count} token(s) matching prefix {args.prefix!r}.")
    else:
        print(f"No active tokens found matching prefix {args.prefix!r}.")


def cmd_show_cost(args: argparse.Namespace) -> None:
    """
    Monthly cost review from SR-1 api-usage.csv.
    Mirrors the monthly review script in architecture/06-skill-runtime.md.
    """
    log_path = Path("/var/lib/corporatetraveldc/api-usage.csv")
    if not log_path.exists():
        print(f"No usage log found at {log_path}")
        return

    rows = list(csv.DictReader(log_path.open()))
    if not rows:
        print("Usage log is empty.")
        return

    # Filter to last 30 days if requested.
    if getattr(args, "days", None):
        cutoff = time.time() - args.days * 86400
        # timestamp is ISO format.
        import datetime
        rows = [
            r for r in rows
            if datetime.datetime.fromisoformat(r["timestamp"]).timestamp() >= cutoff
        ]

    by_skill = collections.defaultdict(
        lambda: {"calls": 0, "in": 0, "out": 0, "skipped": 0, "errors": 0}
    )
    for r in rows:
        s = r["skill"]
        by_skill[s]["calls"] += 1
        by_skill[s]["in"] += int(r.get("input_tokens") or 0)
        by_skill[s]["out"] += int(r.get("output_tokens") or 0)
        if r.get("gate_result") == "skipped":
            by_skill[s]["skipped"] += 1
        if r.get("status") == "error":
            by_skill[s]["errors"] += 1

    print(f"\nAPI usage breakdown ({len(rows)} records):\n")

    # Haiku: $0.80/M in, $4.00/M out (haiku-4-5)
    # Sonnet: $3.00/M in, $15.00/M out (sonnet-4-6)
    # Use Sonnet rates as conservative default.
    header = f"{'Skill':<28} {'Calls':>6} {'Skip%':>6} {'Errors':>7} {'Est Cost':>10}"
    print(header)
    print("-" * len(header))

    total_cost = 0.0
    for skill, d in sorted(by_skill.items()):
        skip_rate = d["skipped"] / d["calls"] * 100 if d["calls"] else 0
        # Determine model from known skill map.
        haiku_skills = {"cps-recompute", "freshness-audit"}
        if any(h in skill for h in haiku_skills):
            cost = (d["in"] / 1_000_000 * 0.80) + (d["out"] / 1_000_000 * 4.00)
        else:
            cost = (d["in"] / 1_000_000 * 3.00) + (d["out"] / 1_000_000 * 15.00)
        total_cost += cost

        print(
            f"{skill:<28} {d['calls']:>6} {skip_rate:>5.0f}% "
            f"{d['errors']:>7} ${cost:>9.2f}"
        )

    print("-" * len(header))
    print(f"{'TOTAL':<28} {'':>6} {'':>6} {'':>7} ${total_cost:>9.2f}")
    print()

    total_calls = sum(d["calls"] for d in by_skill.values())
    total_skipped = sum(d["skipped"] for d in by_skill.values())
    if total_calls:
        overall_skip = total_skipped / total_calls * 100
        print(f"Overall gate suppression rate: {overall_skip:.1f}% "
              f"({total_skipped}/{total_calls} runs skipped)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ctdc-token",
        description="corporatetraveldc token management",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new auth token")
    p_create.add_argument("--user", required=True,
                          help="User label (e.g. cowork, admin, corey)")
    p_create.add_argument("--tier", required=True,
                          choices=["cert", "shares", "admin"],
                          help="Auth tier for this token")
    p_create.add_argument("--label", default=None,
                          help="Device/context label (e.g. admin-iphone, cowork-prod)")
    p_create.add_argument("--expires", type=int, default=None, metavar="DAYS",
                          help="Expiry in days (default: no expiry)")

    # list
    p_list = sub.add_parser("list", help="List tokens")
    p_list.add_argument("--all", action="store_true",
                        help="Include revoked and expired tokens")

    # revoke
    p_revoke = sub.add_parser("revoke", help="Revoke tokens by prefix")
    p_revoke.add_argument("--prefix", required=True,
                          help="Token prefix to revoke (e.g. ctdc_cowork_)")

    # show-cost
    p_cost = sub.add_parser("show-cost", help="Show API usage cost from SR-1 log")
    p_cost.add_argument("--days", type=int, default=30, metavar="DAYS",
                        help="How many days back to analyze (default: 30)")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "revoke":
        cmd_revoke(args)
    elif args.command == "show-cost":
        cmd_show_cost(args)


if __name__ == "__main__":
    main()
