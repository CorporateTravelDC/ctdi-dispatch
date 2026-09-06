#!/usr/bin/env python3
"""scripts/import-ladd-filter.py

Manual weekly import for the FAA LADD (Limiting Aircraft Data Displayed)
privacy filter lists. The automated path (faa_registry.py's
_FAA_LADD_URL, https://registry.faa.gov/database/LADD_Aircraft.zip) has
redirected to an FAA office page since June 2026 and no longer produces
data -- the FAA now distributes current LADD data as two CUI (SP-PRVCY)
marked filter files instead: an "FAA Source" list and a broader
"Industry" list. Confirmed live 2026-08-31: the Industry file is a
strict superset of the FAA Source file (every FAA Source entry is also
in Industry), so importing the union is defensive, not redundant.

Both files are one-identifier-per-line, no header. Entries are NOT
exclusively N-numbers -- the real, current LADD dataset mixes US
N-numbers, foreign registration marks (A6-, A7-, C6-, CF-, D2-, etc.
prefixes), and flight-ID/callsign strings assigned to operations that
don't broadcast a tail-derived ident (confirmed live in the 2026-08-25
files: entries like "AIR1", "BMW41", "DCM2000"). faa_ladd_aircraft's
`n_number` column is a same-shape membership check regardless of which
of those three kinds a given value is -- see db.faa_is_ladd()'s callers
for the two consumers: the per-tail registry-lookup badge (checks a
resolved N-number) and demo/public-mirror scrubbing (checks raw
broadcast idents/callsigns too, see demo/scrub_rules.py).

This script contains no CUI content itself -- only parsing/import logic.
The actual filter files are supplied at runtime and never committed to
the repo (see docs/LADD_CUI_HANDLING.md).

Usage:
    PYTHONPATH=src python3 scripts/import-ladd-filter.py \\
        <faa_source_filter.txt> <industry_filter.txt>

    (a single file also works, e.g. for weeks the operator only has one)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import db  # noqa: E402


def _load_idents(path: Path) -> set[str]:
    idents: set[str] = set()
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ident = line.strip().upper()
            if ident:
                idents.add(ident)
    return idents


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    paths = [Path(p) for p in sys.argv[1:]]
    for p in paths:
        if not p.is_file():
            print(f"XX not a file: {p}", file=sys.stderr)
            sys.exit(1)

    db.init_db_v11()
    before = db.faa_ladd_count()

    combined: set[str] = set()
    for p in paths:
        idents = _load_idents(p)
        print(f"[import-ladd-filter] {p.name}: {len(idents)} unique identifiers")
        combined |= idents

    if len(paths) > 1:
        print(f"[import-ladd-filter] union across {len(paths)} file(s): "
              f"{len(combined)} unique identifiers")

    after = db.faa_upsert_ladd(sorted(combined))

    print(f"[import-ladd-filter] faa_ladd_aircraft: {before} -> {after} entries")
    if after == before and before != 0:
        print("[import-ladd-filter] WARNING: count unchanged -- verify this "
              "week's files actually differ from last week's, or that this "
              "isn't a re-run against the same data.", file=sys.stderr)


if __name__ == "__main__":
    main()
