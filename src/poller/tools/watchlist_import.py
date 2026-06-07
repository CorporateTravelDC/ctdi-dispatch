"""
poller.tools.watchlist_import — Bulk watchlist import from JSON or CSV.

Usage:
  python3 -m poller.tools.watchlist_import \\
    --file /path/to/entries.json \\
    --tier transient|permanent \\
    --type flight|train|mixed \\
    [--dry-run]

Input formats:
  JSON: array of entry objects (same schema as batch API)
  CSV:  columns: identifier, origin, destination, scheduled_arrival,
                 auto_remove_at, notes, tier  (tier column optional, overrides --tier)

Dry-run: prints what would be added/skipped without writing anything.
Fires a single combined ntfy confirmation push on successful live import.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_entry_id(entry_type: str, identifier: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = identifier.lower().replace(" ", "-")
    return f"wl-{entry_type}-{slug}-{date_str}"


def _load_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("entries", data.get("watchlist", []))
    raise ValueError(f"Unexpected JSON structure in {path}")


def _load_csv(path: Path) -> list[dict]:
    entries = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entries.append({k.strip(): (v.strip() if v else None)
                            for k, v in row.items()})
    return entries


def _normalise_entry(raw: dict, tier: str, entry_type: str,
                     now: str) -> Optional[dict]:
    ident = (raw.get("identifier") or "").strip()
    if not ident:
        return None
    ident_upper = ident.upper() if entry_type == "flight" else ident

    row_tier = (raw.get("tier") or "").strip() or tier
    if row_tier not in ("transient", "permanent"):
        row_tier = tier

    entry_id = raw.get("id") or _make_entry_id(entry_type, ident_upper)

    return {
        "id": entry_id,
        "entry_type": entry_type,
        "tier": row_tier,
        "identifier": ident_upper,
        "origin": (raw.get("origin") or "").upper() or None,
        "destination": (raw.get("destination") or "").upper() or None,
        "route_name": raw.get("route_name") or raw.get("route") or None,
        "scheduled_departure": raw.get("scheduled_departure") or None,
        "scheduled_arrival": raw.get("scheduled_arrival") or None,
        "auto_remove_at": raw.get("auto_remove_at") or None,
        "added_at": now,
        "added_by": raw.get("added_by") or "watchlist_import",
        "notes": raw.get("notes") or None,
        "last_event_at": None,
        "last_event_summary": None,
    }


def run_import(
    file_path: str,
    tier: str = "transient",
    entry_type: str = "flight",
    dry_run: bool = False,
) -> dict:
    """
    Importable entry point. Returns dict with keys: added, skipped, errors.
    No side effects when dry_run=True.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Import file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw_entries = _load_csv(path)
    else:
        raw_entries = _load_json(path)

    now = _now_iso()
    to_add: list[dict] = []
    errors: list[str] = []

    for i, raw in enumerate(raw_entries):
        etype = entry_type
        if entry_type == "mixed":
            etype = (raw.get("entry_type") or raw.get("type") or "flight").lower()
            if etype not in ("flight", "train"):
                etype = "flight"

        try:
            entry = _normalise_entry(raw, tier, etype, now)
        except Exception as e:
            errors.append(f"row {i}: {e}")
            continue

        if entry is None:
            errors.append(f"row {i}: missing identifier — skipped")
            continue

        to_add.append(entry)

    if dry_run:
        print(f"[dry-run] Would import {len(to_add)} entries "
              f"({len(errors)} skipped/error)")
        for e in to_add:
            print(f"  + {e['entry_type']:6s} {e['tier']:10s} {e['identifier']}")
        for err in errors:
            print(f"  ! {err}")
        return {"added": 0, "skipped": len(errors), "errors": errors,
                "would_add": len(to_add)}

    # Live import
    from common import db as _db
    added = 0
    skipped = 0

    for entry in to_add:
        try:
            _db.upsert_watchlist_entry(entry)
            added += 1
        except Exception as e:
            errors.append(f"{entry['identifier']}: DB error: {e}")
            skipped += 1

    # Fire combined confirmation push
    if added:
        try:
            from shared.watchlist import _fire_ntfy_dual
            _fire_ntfy_dual(
                domain_topic="dispatch",
                title=f"Watchlist import: {added} entries added",
                detail_body=(f"watchlist_import: {added} entries imported "
                             f"from {path.name}. "
                             f"{skipped + len(errors)} skipped/errors."),
                dispatch_body=f"Watchlist import: {added} entries from {path.name}",
                priority=2,
            )
        except Exception:
            pass

    print(f"Imported {added} entries, {skipped} skipped, {len(errors)} errors.")
    for err in errors:
        print(f"  ! {err}")

    return {"added": added, "skipped": skipped, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", required=True, help="Path to JSON or CSV file")
    parser.add_argument("--tier", choices=["transient", "permanent"],
                        default="transient")
    parser.add_argument("--type", dest="entry_type",
                        choices=["flight", "train", "mixed"], default="flight")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_import(
        file_path=args.file,
        tier=args.tier,
        entry_type=args.entry_type,
        dry_run=args.dry_run,
    )
    sys.exit(0 if not result.get("errors") else 1)


if __name__ == "__main__":
    main()
