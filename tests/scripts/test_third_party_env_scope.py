"""
tests/scripts/test_third_party_env_scope.py

(operator 2026-10-19) Eight third-party images (ACARS Hub, acars_router,
dumpvdl2, UltraFeeder and the four feeder clients) loaded
/etc/corporatetraveldc/dispatch.env, handing every platform setting (database
host and user, SWIM hosts, internal URLs, the backup target name) to code that
parses untrusted radio input. None of them read a single one of those names.
A quadlet whose image is not built here (Image= not localhost/...) must never
load the platform's own env files; it gets its own per-container file instead
(for example piaware-secrets.env).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUADLETS = REPO / ".config/containers/systemd"
PLATFORM_ENV = re.compile(r"^EnvironmentFile=\s*-?/etc/corporatetraveldc/dispatch(-secrets)?\.env\s*$", re.M)


class ThirdPartyEnvScope(unittest.TestCase):
    def test_third_party_images_never_load_platform_env(self):
        offenders = []
        checked = 0
        for p in sorted(QUADLETS.rglob("*.container")):
            if "retired" in p.parent.name:
                continue
            text = p.read_text()
            m = re.search(r"^Image=\s*(\S+)", text, re.M)
            if not m or m.group(1).startswith("localhost/"):
                continue
            checked += 1
            if PLATFORM_ENV.search(text):
                offenders.append(p.name)
        self.assertGreater(checked, 0, "no third-party quadlets found: the scan is broken")
        self.assertEqual(offenders, [], f"third-party images loading platform env files: {offenders}")

    def test_pattern_catches_both_files(self):
        self.assertTrue(PLATFORM_ENV.search("EnvironmentFile=/etc/corporatetraveldc/dispatch.env\n"))
        self.assertTrue(PLATFORM_ENV.search("EnvironmentFile=-/etc/corporatetraveldc/dispatch-secrets.env\n"))
        self.assertFalse(PLATFORM_ENV.search("EnvironmentFile=/etc/corporatetraveldc/piaware-secrets.env\n"))


if __name__ == "__main__":
    unittest.main()
