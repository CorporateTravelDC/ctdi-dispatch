"""
second_brain.client_entity_ingest — normalize vendor/client contact
exports into the second-brain index (same database as index_db.py:
vault_documents lives alongside a new `entities` table).

Current scope, honestly stated: this parses vCard (.vcf) exports --
matching the pattern already used for the one-off
vcards_20260720_044826.vcf upload to the vault -- into structured,
deduplicated, categorized entity records. It does NOT yet pull live
from LimoAnywhere/RingCentral/3CX; those integrations are credential-
gated and built separately (src/web/routes/webhooks.py). This module
is what turns whatever contact data DOES exist (a vCard export today,
a CRM sync later) into one consistent entity table, so the rest of the
second brain has a single place to query "who is this vendor" from.

No third-party vCard library used on purpose -- VCARD 2.1 (the format
these exports use) is simple enough to parse directly, and avoiding a
new pip dependency matters on this box given the Wi-Fi link's observed
bandwidth constraints under load.

Usage:
    python3 -m second_brain.client_entity_ingest /path/to/export.vcf
"""
import argparse
import re
import sqlite3
from datetime import datetime, timezone

from second_brain.index_db import INDEX_DB, init_db as init_vault_db

# Heuristic categorization from the contact name itself -- vendor/dispatch
# lines in these exports consistently carry a "(Dispatch)" or similar
# suffix; anything else is left as a generic contact. Extend this table
# as real patterns are observed, rather than guessing more categories
# up front.
_CATEGORY_PATTERNS: list[tuple[str, str]] = [
    (r"\(dispatch\)", "vendor-dispatch"),
    (r"\blimo(usine)?\b", "vendor-limo"),
    (r"\bcoach\b", "vendor-limo"),
    (r"\brestaurant|chinese|thai|grill|bistro\b", "vendor-restaurant"),
]


def init_entities_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            phones       TEXT,   -- comma-separated, as found in source
            category     TEXT NOT NULL,
            source_file  TEXT NOT NULL,
            ingested_at  TEXT NOT NULL,
            UNIQUE(name, source_file)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_category ON entities(category)")
    conn.commit()


def _categorize(name: str) -> str:
    lowered = name.lower()
    for pattern, category in _CATEGORY_PATTERNS:
        if re.search(pattern, lowered):
            return category
    return "contact"


def parse_vcf(path: str) -> list[dict]:
    """Minimal VCARD 2.1/3.0 parser -- extracts FN (formatted name) and
    all TEL lines per vcard block. Deliberately tolerant: unknown
    property lines are ignored rather than raising, since these exports
    come from a phone's contact app, not a validated data source."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    cards: list[dict] = []
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper() == "BEGIN:VCARD":
            current = {"name": None, "phones": []}
            continue
        if line.upper() == "END:VCARD":
            if current and current["name"]:
                cards.append(current)
            current = None
            continue
        if current is None:
            continue

        if line.upper().startswith("FN:") or line.upper().startswith("FN;"):
            current["name"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("TEL"):
            # TEL;CELL;PREF:+1-202-296-6688  or  TEL:5551234567
            if ":" in line:
                current["phones"].append(line.split(":", 1)[1].strip())

    return cards


def ingest_vcf(conn: sqlite3.Connection, path: str) -> dict:
    cards = parse_vcf(path)
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    updated_count = 0
    for card in cards:
        name = card["name"]
        phones = ",".join(card["phones"])
        category = _categorize(name)
        existing = conn.execute(
            "SELECT phones FROM entities WHERE name=? AND source_file=?", (name, path)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO entities(name, phones, category, source_file, ingested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, phones, category, path, now),
            )
            new_count += 1
        elif existing[0] != phones:
            conn.execute(
                "UPDATE entities SET phones=?, ingested_at=? WHERE name=? AND source_file=?",
                (phones, now, name, path),
            )
            updated_count += 1
    conn.commit()
    return {
        "cards_found": len(cards),
        "new": new_count,
        "updated": updated_count,
        "unchanged": len(cards) - new_count - updated_count,
    }


def entity_summary(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    by_category = dict(conn.execute(
        "SELECT category, COUNT(*) FROM entities GROUP BY category ORDER BY 2 DESC"
    ).fetchall())
    return {"total_entities": total, "by_category": by_category}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("vcf_path", help="path to a .vcf export to ingest")
    args = ap.parse_args()

    conn = sqlite3.connect(INDEX_DB)
    init_vault_db(conn)     # shares the same index db as index_db.py
    init_entities_table(conn)

    result = ingest_vcf(conn, args.vcf_path)
    print(f"ingest complete: {result}")
    print(f"entity summary: {entity_summary(conn)}")

    conn.close()


if __name__ == "__main__":
    main()
