"""tests/scripts/test_verify_manifest_pin.py -- duel C2 (2026-10-04).

verify-manifest.sh used to `source security/signing.env`, so whoever could
write that file got code executed by every verifier (operator sweeps,
container entrypoints, root). It now reads the two fingerprints literally and
prefers a root-owned /etc/corporatetraveldc/signing-pin. These tests exercise
the parsing in isolation (the pin_get function extracted from the script)."""
from __future__ import annotations

import pathlib
import re
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "verify-manifest.sh"


def _pin_get_src() -> str:
    s = SCRIPT.read_text()
    m = re.search(r"^pin_get\(\) \{.*?^\}", s, re.S | re.M)
    assert m, "pin_get() not found in verify-manifest.sh"
    return m.group(0)


def pin_get(pin_text: str, key: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as d:
        pin = pathlib.Path(d) / "pin"; pin.write_text(pin_text)
        marker = pathlib.Path(d) / "pwned"
        pin.write_text(pin_text.replace("@MARKER@", str(marker)))
        script = f'pin_src="{pin}"\n{_pin_get_src()}\npin_get {key}'
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
        return p.stdout, ("executed" if marker.exists() else "")


class PinParsing(unittest.TestCase):
    def test_script_no_longer_sources_the_pin(self):
        s = SCRIPT.read_text()
        self.assertIsNone(re.search(r'^\s*(source|\.)\s+"?\$\{?SIGNING_(ENV|PIN)', s, re.M))

    def test_reads_plain_value(self):
        out, ex = pin_get("SIGNING_KEY_FINGERPRINT=3B29752DACA3544CEA60D01A7B81F49CD96C1631\n", "SIGNING_KEY_FINGERPRINT")
        self.assertEqual(out, "3B29752DACA3544CEA60D01A7B81F49CD96C1631"); self.assertEqual(ex, "")

    def test_reads_quoted_value(self):
        out, _ = pin_get('AGENT_SIGNING_KEY_FINGERPRINT="ABCDEF0123"\n', "AGENT_SIGNING_KEY_FINGERPRINT")
        self.assertEqual(out, "ABCDEF0123")

    def test_command_substitution_is_not_executed(self):
        out, ex = pin_get("SIGNING_KEY_FINGERPRINT=$(touch @MARKER@)ABCD\n", "SIGNING_KEY_FINGERPRINT")
        self.assertEqual(ex, ""); self.assertNotIn("touch", out)

    def test_extra_lines_are_not_executed(self):
        out, ex = pin_get("touch @MARKER@\nSIGNING_KEY_FINGERPRINT=AB12\n", "SIGNING_KEY_FINGERPRINT")
        self.assertEqual(ex, ""); self.assertEqual(out, "AB12")

    def test_non_hex_is_stripped(self):
        out, _ = pin_get("SIGNING_KEY_FINGERPRINT=AB 12;rm\n", "SIGNING_KEY_FINGERPRINT")
        self.assertEqual(out, "AB12")   # 'rm' has no hex chars? r,m are not hex -> stripped

    def test_root_pin_preferred_over_tree_copy(self):
        s = SCRIPT.read_text()
        self.assertIn('SIGNING_PIN="${VERIFY_MANIFEST_PIN:-/etc/corporatetraveldc/signing-pin}"', s)
        self.assertIn('pin_owner}" == 0', s)


if __name__ == "__main__":
    unittest.main()
