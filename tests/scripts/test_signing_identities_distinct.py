"""
docs/AGENT_TRUST_MODEL.md, 2026-10-07: the manifest verifier pins TWO keys --
the operator's (passphrase-protected) and the agent signing key -- and a
signature's key is the record of which path produced it. The two must never
be the same key. Fingerprints are compared, never printed.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _pins(text: str) -> dict:
    out = {}
    for k in ("SIGNING_KEY_FINGERPRINT", "AGENT_SIGNING_KEY_FINGERPRINT"):
        m = re.search(rf"^{k}=['\"]?([0-9A-Fa-f]+)", text, re.M)
        out[k] = m.group(1).upper() if m else None
    return out


class SigningIdentitiesDistinct(unittest.TestCase):
    def test_tracked_pin_has_two_distinct_keys(self):
        p = _pins((REPO / "security" / "signing.env").read_text())
        self.assertTrue(p["SIGNING_KEY_FINGERPRINT"] and p["AGENT_SIGNING_KEY_FINGERPRINT"])
        self.assertNotEqual(p["SIGNING_KEY_FINGERPRINT"], p["AGENT_SIGNING_KEY_FINGERPRINT"])

    def test_live_root_pin_matches_the_tracked_pin_when_present(self):
        live = Path("/etc/corporatetraveldc/signing-pin")
        if not live.exists():
            self.skipTest("no root-owned pin on this host")
        self.assertEqual(_pins(live.read_text()), _pins((REPO / "security" / "signing.env").read_text()))
