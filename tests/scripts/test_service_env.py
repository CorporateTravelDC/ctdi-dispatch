"""tests/scripts/test_service_env.py -- scripts/service-env/generate.py (duel M9)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "service-env" / "generate.py"


def _mod():
    spec = importlib.util.spec_from_file_location("svcgen", GEN)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


class Filtering(unittest.TestCase):
    def test_demo_archive_uses_its_own_source_when_all_profiles_generated(self):
        m = _mod()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "writer.allowlist").write_text("DISPATCH_PG_PASSWORD\n")
            names = ["DEMO_PG_HOST", "DEMO_PG_PORT", "DEMO_PG_DB", "DEMO_PG_USER", "DEMO_PG_PASSWORD"]
            (root / "demo-archive.allowlist").write_text("\n".join(names))
            prod = root / "production.env"
            prod.write_text("DISPATCH_PG_PASSWORD=fixture-production\n")
            demo = root / "demo.env"
            demo.write_text("DEMO_PG_HOST=/fixture/demo\nDEMO_PG_PORT=5432\n"
                            "DEMO_PG_DB=corporatetraveldc_demo\nDEMO_PG_USER=demo\n"
                            "DEMO_PG_PASSWORD=fixture-demo\nDISPATCH_ROLE=demo\n"
                            "DEMO_ADMIN_TOKEN=fixture-not-for-archive\n")
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(m, "HERE", root), mock.patch.object(m, "SECRETS", str(prod)), \
                 mock.patch.object(m, "SERVICE_SOURCES", {"demo-archive": str(demo)}), \
                 mock.patch.object(m.os, "geteuid", return_value=0), \
                 mock.patch.object(m, "write_file") as write:
                self.assertEqual(m.main(["--write"]), 0)
            written = {args[0].stem: args[1] for args, _ in write.call_args_list}
            self.assertEqual(set(written["demo-archive"]), set(names))
            self.assertNotIn("DEMO_ADMIN_TOKEN", written["demo-archive"])
            self.assertNotIn("DISPATCH_ROLE", written["demo-archive"])
            self.assertEqual(set(written["writer"]), {"DISPATCH_PG_PASSWORD"})

    def test_incomplete_demo_source_aborts_all_profile_writes(self):
        m = _mod()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "demo-archive.allowlist").write_text("DEMO_PG_PASSWORD\nDEMO_PG_DB\nDEMO_PG_USER\n")
            demo = root / "demo.env"
            demo.write_text("DEMO_PG_DB=corporatetraveldc_demo\nDEMO_PG_USER=demo_owner\n")
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(m, "HERE", root), \
                 mock.patch.object(m, "SERVICE_SOURCES", {"demo-archive": str(demo)}), \
                 mock.patch.object(m, "write_file") as write:
                self.assertEqual(m.main(["--write"]), 66)
                write.assert_not_called()

    def test_scoped_keeps_only_allowlisted_names_verbatim(self):
        m = _mod()
        src = {"A_TOKEN": "x y;z", "B_PASS": "'quoted'", "C_UNUSED": "nope"}
        present, missing = m.scoped(src, ["A_TOKEN", "B_PASS", "D_MISSING"])
        self.assertEqual(present, {"A_TOKEN": "x y;z", "B_PASS": "'quoted'"})
        self.assertEqual(missing, ["D_MISSING"])

    def test_allowlist_parsing_ignores_comments(self):
        m = _mod()
        with tempfile.NamedTemporaryFile("w", suffix=".allowlist", delete=False) as fh:
            fh.write("# header\nA_TOKEN  # consumer x\n\nB_PASS\n")
        try:
            self.assertEqual(m.read_allowlist(Path(fh.name)), ["A_TOKEN", "B_PASS"])
        finally:
            os.unlink(fh.name)

    def test_check_mode_prints_names_never_values(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "secrets.env"
            src.write_text("DISPATCH_PG_PASSWORD=sup3r-s3cret-v@lue\nOTHER=ignored-value\n")
            env = dict(os.environ, SERVICE_ENV_SOURCE=str(src))
            r = subprocess.run([sys.executable, str(GEN), "--check", "execstandard-verifier"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("execstandard-verifier", r.stdout)
            self.assertNotIn("sup3r-s3cret-v@lue", r.stdout + r.stderr)
            self.assertNotIn("ignored-value", r.stdout + r.stderr)

    def test_write_refuses_without_root(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "secrets.env"; src.write_text("DISPATCH_PG_PASSWORD=x\n")
            env = dict(os.environ, SERVICE_ENV_SOURCE=str(src), SERVICE_ENV_OUT=str(Path(d) / "out"))
            r = subprocess.run([sys.executable, str(GEN), "--write", "execstandard-verifier"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 77)
            self.assertFalse((Path(d) / "out").exists())

    def test_verifier_pilot_allowlist_is_minimal(self):
        m = _mod()
        self.assertEqual(m.read_allowlist(GEN.parent / "execstandard-verifier.allowlist"), ["DISPATCH_PG_PASSWORD"])

    def test_verifier_quadlet_uses_scoped_file(self):
        q = (ROOT / ".config/containers/systemd/corporatetraveldc-execstandard-verifier.container").read_text()
        self.assertIn("EnvironmentFile=/etc/corporatetraveldc/svc/execstandard-verifier.env", q)
        self.assertNotIn("EnvironmentFile=/etc/corporatetraveldc/dispatch-secrets.env", q)


if __name__ == "__main__":
    unittest.main()
