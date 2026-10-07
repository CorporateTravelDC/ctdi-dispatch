"""tests/common/test_no_naive_dates.py -- guard (2026-10-04, backlog #9).

Fails on any bare date.today(), naive datetime.now() (no tz argument) or
datetime.utcnow() under src/poller/skills and src/ingest unless the exact
line is listed in naive_date_allowlist.txt with a reason. Day-keyed code
must use common.optime (op_today / op_day_window / resolve_target_date);
UTC instants must use datetime.now(timezone.utc).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN = [ROOT / "src" / "poller" / "skills", ROOT / "src" / "ingest"]
PATTERN = re.compile(r"\bdate\.today\(\)|\bdatetime\.now\(\s*\)|\bdatetime\.utcnow\(\)")


def _allowlist():
    out = []
    for line in (Path(__file__).parent / "naive_date_allowlist.txt").read_text().splitlines():
        line = line.split("  #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        path, _, needle = line.partition(":")
        out.append((path.strip(), needle.strip()))
    return out


class NoNaiveDates(unittest.TestCase):
    def test_no_unlisted_naive_calls(self):
        allowed = _allowlist()
        bad = []
        for base in SCAN:
            for f in base.rglob("*.py"):
                rel = str(f.relative_to(ROOT))
                for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if PATTERN.search(line) and not any(rel == p and nd in line for p, nd in allowed):
                        bad.append(f"{rel}:{n}: {line.strip()[:100]}")
        self.assertEqual(bad, [], "naive date/time calls -- use common.optime or datetime.now(timezone.utc):\n" + "\n".join(bad))

    def test_allowlist_entries_still_exist(self):
        stale = []
        for p, nd in _allowlist():
            f = ROOT / p
            if not f.exists() or nd not in f.read_text(errors="replace"):
                stale.append(f"{p}:{nd}")
        self.assertEqual(stale, [], "stale allowlist entries (remove them):\n" + "\n".join(stale))


if __name__ == "__main__":
    unittest.main()
