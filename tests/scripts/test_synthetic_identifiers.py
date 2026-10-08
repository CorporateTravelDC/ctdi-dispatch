"""
2026-10-08 (security review 06): the synthetic Q registry is the default for
every illustrative, GA or privacy-listed aircraft identifier in this
repository (README "Synthetic aircraft identifiers"). A sweep that day found
46 real registrations -- 9 of them on the FAA LADD privacy list -- in tracked
fixtures, code comments, tests and a skill doc, and a LADD-listed aircraft in
the published permanent watchlist. These tests keep it from coming back.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# FAA N-number shape. N0... never exists, so it stays allowed for format illustrations.
N_REG = re.compile(r"(?<![A-Za-z0-9_-])N[1-9][0-9]{0,4}[A-Z]{0,2}(?![A-Za-z0-9_])")
# Operational files that must carry real identifiers. Each is private: dropped
# from the public mirror by scripts/scrub-public-tree.py DROP_FILES.
OPERATIONAL = {"watchlists/permanent_flights.json"}


def load_scrub():
    spec = importlib.util.spec_from_file_location("scrub_public_tree", REPO / "scripts" / "scrub-public-tree.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


class SyntheticRegistryIsTheDefault(unittest.TestCase):
    def test_no_us_registration_in_tracked_text(self):
        files = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True).stdout.split(b"\0")
        offenders = {}
        for f in (x.decode() for x in files if x):
            if f in OPERATIONAL:
                continue
            p = REPO / f
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8192]:
                continue                                   # binary: OOXML parts are covered by the leak gate
            hits = {m.group(0) for m in N_REG.finditer(data.decode("utf-8", "replace")) if len(m.group(0)) >= 4}
            if hits:
                offenders[f] = len(hits)                   # counts only: a hit may be LADD-listed
        self.assertEqual(offenders, {}, "use the synthetic Q registry (README 'Synthetic aircraft identifiers') "
                                        "instead of US registrations; counts per file: " + repr(offenders))

    def test_operational_files_never_reach_the_public_mirror(self):
        scrub = load_scrub()
        for f in OPERATIONAL:
            self.assertIn(Path(f).name, scrub.DROP_FILES, f)
        self.assertTrue((REPO / "watchlists" / "permanent_flights.example.json").is_file())

    def test_the_leak_gate_refuses_a_registration_without_printing_it(self):
        scrub = load_scrub()
        reg = "N" + "4" + "21" + "QX"[::-1]                  # assembled at runtime; not a literal in this file
        text = f"tail {reg} on approach".encode()
        hits = [m.group(0) for m in scrub.N_REGISTRATION_RE.finditer(text) if len(m.group(0)) >= 4]
        self.assertEqual(len(hits), 1)
        for ok in (b"Q7K2M on approach", b"N0123 format example", b"38.9N 77.0W", b"N1 rpm", b"NOTAM N2"):
            self.assertEqual([m for m in scrub.N_REGISTRATION_RE.finditer(ok) if len(m.group(0)) >= 4], [], ok)

    def test_parsers_recognise_the_synthetic_registry(self):
        sys.path.insert(0, str(REPO / "src"))
        from ingest.parsers import fdps_parser
        self.assertTrue(fdps_parser._TAIL_NUMBER_RE.match("Q7K2M"))
        self.assertTrue(fdps_parser._TAIL_NUMBER_RE.match("N2" + "1QX"))
        self.assertFalse(fdps_parser._TAIL_NUMBER_RE.match("QXA123"))


if __name__ == "__main__":
    unittest.main()
