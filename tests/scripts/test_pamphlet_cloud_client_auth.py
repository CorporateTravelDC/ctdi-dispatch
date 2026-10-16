"""
tests/scripts/test_pamphlet_cloud_client_auth.py

(parity audit 2026-10-15) scripts/agent-segmentation/publish-pamphlet-to-vault.sh
told cloud agents their credential was an X-Board-Key board token refreshed
every 12 h via /api/v1/board/refresh. Cloud agents reach the platform through
the agent gateway's MCP connector (src/web/routes/agent_gateway.py). The
cloud-client preamble must name the connector tools and never X-Board-Key
as the agent's credential.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "agent-segmentation" / "publish-pamphlet-to-vault.sh"
sys.path.insert(0, str(REPO / "src"))


def _preamble() -> str:
    text = SCRIPT.read_text()
    m = re.search(r'cat >"\$tmp" <<EOF\n(.*?)\nEOF\n', text, re.S)
    assert m, "cloud-client heredoc not found"
    return m.group(1)


class PamphletCloudClientAuth(unittest.TestCase):
    def test_no_board_key_credential(self):
        pre = _preamble()
        self.assertNotIn("/api/v1/board/refresh", pre)
        self.assertNotRegex(pre, r"sent as \\`X-Board-Key")

    def test_names_every_gateway_tool(self):
        from web.routes.agent_gateway import TOOLS
        pre = _preamble()
        for t in TOOLS:
            with self.subTest(tool=t["name"]):
                self.assertIn(f"\\`{t['name']}\\`", pre)


if __name__ == "__main__":
    unittest.main()
