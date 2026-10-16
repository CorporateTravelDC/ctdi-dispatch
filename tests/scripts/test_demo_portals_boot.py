"""tests/scripts/test_demo_portals_boot.py -- 2026-10-16: the two demo
portals (corporatetraveldc-demo-portal-client / -personal) start only after
the serialized boot rollout. Their quadlets carry no WantedBy= (the
2026-09-06 anti-boot-storm rule) and stack-boot-ctl.sh's ORDER never listed
them, so after every reboot both stayed down. corporatetraveldc-demo-
portals-boot.service (oneshot, after + requires corporatetraveldc-boot-
stagger.service, the end of the rollout) owns their start.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNIT = REPO / ".config/systemd/user/corporatetraveldc-demo-portals-boot.service"
QUADLETS = REPO / ".config/containers/systemd"
PORTALS = ("corporatetraveldc-demo-portal-client", "corporatetraveldc-demo-portal-personal")
STAGGER = "corporatetraveldc-boot-stagger.service"


def _sections(text: str) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[(\w+)\]$", s)
        if m:
            cur = out.setdefault(m.group(1), {})
            continue
        k, _, v = s.partition("=")
        cur.setdefault(k, []).append(v)
    return out


class DemoPortalsBoot(unittest.TestCase):
    def setUp(self):
        self.u = _sections(UNIT.read_text())

    def test_ordered_and_bound_to_the_boot_stagger(self):
        unit = self.u["Unit"]
        self.assertIn(STAGGER, " ".join(unit.get("After", [])).split())
        self.assertIn(STAGGER, " ".join(unit.get("Requires", [])).split())
        # the stagger itself runs after the serialized rollout, so this is after both
        stagger = _sections((REPO / ".config/systemd/user" / STAGGER).read_text())
        self.assertIn("corporatetraveldc-stack-boot-stagger.service", " ".join(stagger["Unit"]["After"]).split())

    def test_oneshot_that_starts_both_portals_without_blocking(self):
        svc = self.u["Service"]
        self.assertEqual(svc["Type"], ["oneshot"])
        self.assertEqual(svc["RemainAfterExit"], ["yes"])
        (exec_start,) = svc["ExecStart"]
        self.assertTrue(exec_start.startswith("/usr/bin/systemctl --user start --no-block "), exec_start)
        self.assertEqual(set(exec_start.split()[4:]), {f"{p}.service" for p in PORTALS})
        self.assertEqual(self.u["Install"]["WantedBy"], ["default.target"])
        self.assertNotIn("OnFailure", self.u["Unit"])

    def test_portal_quadlets_still_have_no_wanted_by(self):
        for name in PORTALS:
            p = QUADLETS / f"{name}.container"
            with self.subTest(unit=p.name):
                self.assertTrue(p.exists())
                active = [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
                self.assertFalse(any(l.startswith("WantedBy=") for l in active), f"{p.name} regained WantedBy=")

    def test_portals_are_not_also_in_the_stack_boot_order(self):
        """Double ownership would start them twice; ORDER starts them never."""
        text = (REPO / "scripts/stack-boot-ctl.sh").read_text()
        order = text[text.index("ORDER=("):]
        order = order[:order.index("\n)")]
        names = {l.strip().split(":")[0] for l in order.splitlines()[1:] if l.strip() and not l.strip().startswith("#")}
        for name in PORTALS:
            self.assertNotIn(name.removeprefix("corporatetraveldc-"), names)


if __name__ == "__main__":
    unittest.main()
