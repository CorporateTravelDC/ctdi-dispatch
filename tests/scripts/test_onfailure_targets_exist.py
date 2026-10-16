"""
tests/scripts/test_onfailure_targets_exist.py

(parity audit 2026-10-15) ntfy.container named
OnFailure=ntfy-container-alert.service, a unit that never existed, so the
hook silently did nothing. Every OnFailure= target named in a tracked
quadlet or user unit must be a unit (or template) tracked in this repo.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNIT_DIRS = (REPO / ".config/systemd/user", REPO / ".config/containers/systemd")
QUADLET_SUFFIXES = {".container": ".service", ".pod": "-pod.service", ".network": "-network.service",
                    ".volume": "-volume.service", ".kube": ".service"}


def _known_units() -> set[str]:
    names: set[str] = set()
    for d in UNIT_DIRS:
        for p in d.rglob("*"):
            if not p.is_file() or "retired" in p.parent.name:
                continue
            names.add(p.name)
            if p.suffix in QUADLET_SUFFIXES:
                names.add(p.stem + QUADLET_SUFFIXES[p.suffix])
    return names


def _template_of(unit: str) -> str:
    # foo@%n.service / foo@bar.service -> foo@.service
    m = re.match(r"^([^@]+@)[^.]*(\.\w+)$", unit)
    return m.group(1) + m.group(2) if m else unit


class OnFailureTargetsExist(unittest.TestCase):
    def test_every_onfailure_target_is_tracked(self):
        known = _known_units()
        seen = 0
        for d in UNIT_DIRS:
            for p in sorted(d.rglob("*")):
                if not p.is_file() or "retired" in p.parent.name:
                    continue
                for line in p.read_text(errors="replace").splitlines():
                    if not line.startswith("OnFailure="):
                        continue
                    for target in line.split("=", 1)[1].split():
                        seen += 1
                        with self.subTest(unit=p.name, target=target):
                            self.assertIn(_template_of(target), known,
                                          f"{p.name}: OnFailure={target} names no tracked unit")
        self.assertGreater(seen, 0)


if __name__ == "__main__":
    unittest.main()
