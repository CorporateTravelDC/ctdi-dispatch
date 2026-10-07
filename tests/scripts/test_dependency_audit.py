"""
2026-10-07: scripts/dependency-audit.py -- the pre-push and daily known-
vulnerability audit. These tests cover the decision logic only (no network,
no pip-audit, no npm): which files are audited, and what blocks.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("dependency_audit", REPO / "scripts" / "dependency-audit.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class DependencyAudit(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_manifest_discovery_skips_vendored_trees(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel in ("requirements.txt", "api/requirements-dev.txt", "web/package-lock.json",
                        "web/node_modules/x/package-lock.json", ".venv/requirements.txt", "notes.txt"):
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("")
            got = sorted(str(p.relative_to(root)) for p in self.m.manifests(root))
        self.assertEqual(got, ["api/requirements-dev.txt", "requirements.txt", "web/package-lock.json"])

    def test_policy(self):
        s = self.m.summarize
        npm = lambda **c: {"repo": "r", "target": "package-lock.json",
                           "counts": {"low": 0, "moderate": 0, "high": 0, "critical": 0, **c},
                           "blocking": c.get("high", 0) + c.get("critical", 0)}
        py = lambda *fixes: {"repo": "r", "target": "requirements.txt",
                             "findings": [{"package": "p", "version": "1", "id": f"X-{i}", "fix": f} for i, f in enumerate(fixes)],
                             "blocking": sum(1 for f in fixes if f)}
        self.assertEqual(s([npm(), py()])[0], 0)
        self.assertEqual(s([npm(moderate=3, low=1)])[0], 0)          # reported, not blocking
        self.assertEqual(s([npm(high=1)])[0], 1)
        self.assertEqual(s([npm(critical=1)])[0], 1)
        self.assertEqual(s([py("2.0")])[0], 1)                        # a fix exists -> block
        self.assertEqual(s([py("")])[0], 0)                           # no fix yet -> report only
        err = {"repo": "r", "target": "requirements.txt", "error": "offline"}
        self.assertEqual(s([err])[0], 3)                              # could not run at all
        self.assertEqual(s([err, npm()])[0], 0)                       # partial run, nothing found
        self.assertEqual(s([err, npm(high=2)])[0], 1)

    def test_daily_list_covers_the_private_repos(self):
        listed = [l.split("#", 1)[0].strip() for l in (REPO / "scripts/lib/dependency-audit-repos.txt").read_text().splitlines()]
        for name in ("ctdi-dispatch-internal", "csexecutiveservices-website", "executivestandard-website"):
            self.assertTrue(any(l.endswith("/" + name) for l in listed if l), name)

    def test_pre_push_hooks_call_the_audit(self):
        for hook in ("scripts/pre-push", "scripts/git-hooks/site-pre-push"):
            text = (REPO / hook).read_text()
            self.assertIn("dependency-audit.py", text, hook)
            self.assertIn("DEPENDENCY_AUDIT_OVERRIDE", text, hook)


class DormantIsAudited(unittest.TestCase):
    """Operator rule 2026-10-07: disabled / dormant / not deployed is not a reason
    to keep a known-vulnerable dependency. Rollback tags, test tags and worktrees
    are audited like running code."""

    def setUp(self):
        self.m = load()

    def test_dormant_images_skip_only_running_ids_and_foreign_repos(self):
        from unittest import mock
        listing = ("localhost/app:latest sha256:aaa\nlocalhost/app:previous sha256:bbb\n"
                   "docker.io/library/nginx:alpine sha256:ccc\n<none>:<none> sha256:ddd\n")
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["podman", "images"]:
                return mock.Mock(stdout=listing, returncode=0)
            return mock.Mock(stdout="requests==2.32.3\n", returncode=0)

        with mock.patch.object(self.m.subprocess, "run", side_effect=run), \
             mock.patch.object(self.m, "pip_audit", return_value={"findings": [], "blocking": 0}):
            res = self.m.audit_dormant_images({"aaa"})
        self.assertEqual([r["target"] for r in res], ["image:localhost/app:previous (dormant)"])
        run_cmd = [c for c in calls if c[:2] == ["podman", "run"]][0]
        self.assertIn("none", run_cmd)                       # offline: --network none

    def test_images_mode_always_includes_dormant(self):
        src = (REPO / "scripts" / "dependency-audit.py").read_text()
        self.assertIn("return res + audit_dormant_images(running_ids)", src)

    def test_worktrees_exclude_the_main_checkout(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            main, wt = Path(d, "main"), Path(d, "feature")
            main.mkdir(); wt.mkdir()
            out = f"worktree {main}\nHEAD x\n\nworktree {wt}\nHEAD y\n"
            with mock.patch.object(self.m.subprocess, "run", return_value=mock.Mock(stdout=out)):
                self.assertEqual(self.m.worktrees(main), [wt])

    def test_no_watcher_ships_the_vulnerable_requests_pin(self):
        for w in ("acars", "ais", "utm"):
            runs = [l for l in (REPO / "src" / f"{w}_watcher" / "Containerfile").read_text().splitlines()
                    if l.startswith("RUN")]                  # SUPERSEDED comments keep the old pin
            self.assertFalse(any("requests==2.32.3" in l for l in runs), w)
