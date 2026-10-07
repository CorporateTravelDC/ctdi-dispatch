"""
tests/scripts/test_secrets_subset.py

scripts/agent-segmentation/secrets-subset.py builds the ctdc-agent secrets
subset: allowlisted NAMES only, values copied VERBATIM (podman --env-file
semantics via scripts/lib/with_dispatch_env.py), 0640, never printing a
value. All inputs here are temp files with fake values; nothing under /etc
is touched.
"""
from __future__ import annotations

import importlib.util
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "agent-segmentation" / "secrets-subset.py"


def _load():
    spec = importlib.util.spec_from_file_location("secrets_subset", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FAKE_SECRET_WITH_META = "ab;cdEFG hij$k"   # the shape that broke shell-sourcing on 2026-10-03


class SecretsSubset(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.env1 = d / "dispatch.env"
        self.env2 = d / "dispatch-secrets.env"
        self.allow = d / "allow.txt"
        self.out = d / "sub" / "agent-secrets.env"
        self.env1.write_text(
            "# config\nNEXTCLOUD_ADMIN_USER=alice\nDISPATCH_PG_HOST=127.0.0.1\nUNRELATED_CFG=x\n"
        )
        self.env2.write_text(
            "DISPATCH_ADMIN_TOKEN=fake-token-1\n"
            f"NWWS_PASSWORD={FAKE_SECRET_WITH_META}\n"
            "SWIM_NMS_PASS_FDPS=never-copied\n"
            'QUOTED="keep quotes literally"\n'
            "NEXTCLOUD_APP_PASSWORD=fake app pw with spaces\n"
        )
        self.allow.write_text(
            "# allow\nDISPATCH_ADMIN_TOKEN\nNEXTCLOUD_ADMIN_USER\nNEXTCLOUD_APP_PASSWORD\n"
            "DISPATCH_PG_HOST\nQUOTED\nDISPATCH_PG_RO_PASSWORD?\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = self.mod.main([
                "--sources", str(self.env1), str(self.env2),
                "--allowlist", str(self.allow), "--out", str(self.out),
                "--no-chown", *extra,
            ])
        return rc, out.getvalue(), err.getvalue()

    def test_writes_only_allowlisted_names_verbatim(self):
        rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        text = self.out.read_text()
        kv = dict(l.split("=", 1) for l in text.splitlines() if l and not l.startswith("#"))
        self.assertEqual(set(kv), {"DISPATCH_ADMIN_TOKEN", "NEXTCLOUD_ADMIN_USER",
                                   "NEXTCLOUD_APP_PASSWORD", "DISPATCH_PG_HOST", "QUOTED"})
        self.assertEqual(kv["NEXTCLOUD_APP_PASSWORD"], "fake app pw with spaces")
        self.assertEqual(kv["QUOTED"], '"keep quotes literally"')   # no quote stripping
        self.assertNotIn("NWWS_PASSWORD", text)
        self.assertNotIn("SWIM_NMS_PASS_FDPS", text)
        self.assertNotIn("UNRELATED_CFG", text)

    def test_mode_is_0640(self):
        rc, _, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o640)

    def test_never_prints_values(self):
        for flags in ((), ("--check",), ("--dry-run",)):
            rc, out, err = self._run(*flags)
            blob = out + err
            for v in ("fake-token-1", FAKE_SECRET_WITH_META, "fake app pw", "keep quotes"):
                self.assertNotIn(v, blob, f"value leaked with flags {flags}")

    def test_check_reports_optional_and_missing(self):
        rc, out, _ = self._run("--check")
        self.assertEqual(rc, 0)
        self.assertIn("optional, absent: DISPATCH_PG_RO_PASSWORD", out)
        self.assertFalse(self.out.exists(), "--check must not write")
        # a required name missing -> rc 1 and no write
        self.allow.write_text("DISPATCH_ADMIN_TOKEN\nNOT_IN_ANY_FILE\n")
        rc, out, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("MISSING : NOT_IN_ANY_FILE", out)
        self.assertFalse(self.out.exists())

    def test_later_source_overrides_earlier(self):
        self.env1.write_text("DISPATCH_PG_HOST=old\n")
        self.env2.write_text("DISPATCH_PG_HOST=new\nDISPATCH_ADMIN_TOKEN=t\nNEXTCLOUD_ADMIN_USER=u\nNEXTCLOUD_APP_PASSWORD=p\nQUOTED=q\n")
        rc, _, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertIn("DISPATCH_PG_HOST=new\n", self.out.read_text())

    def test_bad_allowlist_name_rejected(self):
        self.allow.write_text("lowercase_name\n")
        with self.assertRaises(SystemExit):
            self._run("--check")

    # --- profiles (2026-10-04 team segmentation: humans and agents) -------
    def test_profiles_default_to_their_own_allowlist_out_and_group(self):
        P = self.mod.PROFILES
        self.assertEqual(set(P), {"agent", "ops"})
        self.assertTrue(P["agent"]["allowlist"].endswith("secrets-allowlist-agent.txt"))
        self.assertTrue(P["ops"]["allowlist"].endswith("secrets-allowlist-ops.txt"))
        self.assertEqual(P["agent"]["out"], "/etc/ctdc-agent/agent-secrets.env")
        self.assertEqual(P["ops"]["out"], "/etc/ctdc-ops/ops-secrets.env")
        self.assertEqual(P["agent"]["group"], "ctdc-agents")
        self.assertEqual(P["ops"]["group"], "ctdc-ops")
        self.assertEqual(self.mod.DEFAULT_PROFILE, "agent")

    def test_ops_profile_with_real_allowlist_excludes_admin_token_and_pg_password(self):
        """The shipped ops allowlist must never hand a human the shared admin
        token, the board key, the production PG password or any feed cred."""
        out_file = Path(self.tmp.name) / "ops-secrets.env"
        self.env2.write_text(
            "NTFY_TOKEN=fake-ntfy\nDISPATCH_ADMIN_TOKEN=fake-admin\nBOARD_KEY=fake-board\n"
            "DISPATCH_PG_PASSWORD=fake-pg\nSWIM_NMS_PASS_FDPS=never\nNWWS_PASSWORD=never\n"
        )
        self.env1.write_text("DISPATCH_PG_HOST=h\nDISPATCH_PG_PORT=5432\nDISPATCH_PG_DB=d\nOLLAMA_BASE_URL=http://x:1\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = self.mod.main(["--profile", "ops", "--sources", str(self.env1), str(self.env2),
                                "--out", str(out_file), "--no-chown"])
        self.assertEqual(rc, 0, err.getvalue())
        text = out_file.read_text()
        names = {l.split("=", 1)[0] for l in text.splitlines() if l and not l.startswith("#")}
        # 2026-10-04 (duel X1): the shared NTFY_TOKEN is an admin-role ntfy user
        # that can read the approval-gate topic -- no longer handed out.
        self.assertEqual(names, {"DISPATCH_PG_HOST", "DISPATCH_PG_PORT", "DISPATCH_PG_DB", "OLLAMA_BASE_URL"})
        for banned in ("DISPATCH_ADMIN_TOKEN", "BOARD_KEY", "DISPATCH_PG_PASSWORD", "SWIM_NMS", "NWWS",
                       "NEXTCLOUD_APP_PASSWORD", "NTFY_TOKEN="):
            self.assertNotIn(banned, text)
        self.assertIn("--profile ops (group ctdc-ops)", text)       # header names the profile
        self.assertIn("profile : ops", out.getvalue())

    def test_agent_profile_with_real_allowlist_never_includes_pg_password(self):
        out_file = Path(self.tmp.name) / "agent-secrets.env"
        self.env2.write_text(
            "NTFY_TOKEN=a\nDISPATCH_ADMIN_TOKEN=b\nBOARD_KEY=c\nNEXTCLOUD_APP_PASSWORD=d\n"
            "DISPATCH_PG_PASSWORD=never\nSWIM_NMS_PASS_FDPS=never\n"
        )
        self.env1.write_text("NEXTCLOUD_ADMIN_USER=u\nDISPATCH_PG_HOST=h\nDISPATCH_PG_PORT=5432\nDISPATCH_PG_DB=d\nOLLAMA_BASE_URL=http://x:1\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = self.mod.main(["--profile", "agent", "--sources", str(self.env1), str(self.env2),
                                "--out", str(out_file), "--no-chown"])
        self.assertEqual(rc, 0, err.getvalue())
        text = out_file.read_text()
        self.assertNotIn("DISPATCH_PG_PASSWORD", text)
        self.assertNotIn("SWIM_NMS", text)
        # 2026-10-04 (duel X1/H4 + operator): no admin API token, no master board
        # key, no admin-role ntfy token, no Nextcloud admin login for agents.
        for banned in ("DISPATCH_ADMIN_TOKEN", "BOARD_KEY", "NTFY_TOKEN=", "NEXTCLOUD_APP_PASSWORD", "NEXTCLOUD_ADMIN_USER"):
            self.assertNotIn(banned, text)
        self.assertIn("OLLAMA_BASE_URL=http://x:1\n", text)
        for v in ("never",):
            self.assertNotIn(v, out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
