"""tests/scripts/test_team_liveness.py -- verdict logic of scripts/team-liveness.sh
under LIVENESS_FAKE_ROOT (no root, no real accounts, no privileged command runs).

Fake root layout the script reads:
  accounts.txt            "name group shell [kind login_mode preloaded]" per line
                          (the optional trio mirrors /etc/ctdc-accounts.conf)
  home/<name>/.claude/.credentials.json   (fake expiry fields only)
  home/<name>/.ssh/authorized_keys
  passwd-S/<name>         "P" or "L"
  expire/<name>           "never" or a date
  lastlogin/<name>        epoch seconds
  created/<name>          epoch seconds
  signers/<name>          "active|inactive|none role kind"  (board_signers stand-in;
                          the directory itself missing = registry unavailable)
  signers/<name>.pub      the registered public key line
  revoked/<name>          epoch of the last token revocation
  var/lib/corporatetraveldc/team-liveness/orders/<target>/  kill orders
  actions.log             every privileged command the script WOULD run

2026-10-04 (authorisation model): liveness is an AND of login, key, token and
kill-order factors; kill orders are verified with real ed25519 keys made by
ssh-keygen in the temp dir and executed by quorum.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "team-liveness.sh"
KILL = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "kill-order.sh"
DAY = 86400
NOW = int(time.time())
FAKE_KEY = "ssh-ed25519 AAAATESTKEYNOTREAL0000000000000000000000000000000000000000000000"


class FakeRoot:
    def __init__(self, tmp):
        self.root = pathlib.Path(tmp)
        for d in ("home", "passwd-S", "expire", "lastlogin", "created", "signers", "revoked"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        (self.root / "accounts.txt").write_text("")
        self.orders = self.root / "var/lib/corporatetraveldc/team-liveness/orders"

    def account(self, name, group, shell="/bin/bash", role="member", kind=None, signer=True, mode=None, preloaded=0):
        """mode: None (not in the registry -> group default), "claude", "ssh" or "none"
        (service). The registry kind follows the mode: none -> service, else the signer kind."""
        with open(self.root / "accounts.txt", "a") as fh:
            if mode is None:
                fh.write(f"{name} {group} {shell}\n")
            else:
                rkind = "service" if mode == "none" else (kind or ("agent" if group == "ctdc-agents" else "human"))
                fh.write(f"{name} {group} {shell} {rkind} {mode} {preloaded}\n")
        (self.root / "home" / name).mkdir(parents=True, exist_ok=True)
        (self.root / "passwd-S" / name).write_text("P")
        (self.root / "expire" / name).write_text("never")
        (self.root / "created" / name).write_text(str(NOW - 30 * DAY))
        if signer:   # the AND model: every account normally has an active registered key
            self.signer(name, "active", role, kind or ("service" if mode == "none" else "agent" if group == "ctdc-agents" else "human"))
        return self

    def signer(self, name, state="active", role="member", kind="agent", pubkey=None):
        (self.root / "signers" / name).write_text(f"{state} {role} {kind}\n")
        if pubkey:
            (self.root / "signers" / f"{name}.pub").write_text(pubkey + "\n")
        return self

    def revoked(self, name, epoch):
        (self.root / "revoked" / name).write_text(str(epoch))

    def keypair(self, name):
        """Real ed25519 key for `name`; registers its public half as the signer key."""
        k = self.root / "keys" / name; k.parent.mkdir(exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{name}@test", "-f", str(k)], check=True)
        (self.root / "signers" / f"{name}.pub").write_text((self.root / "keys" / f"{name}.pub").read_text())
        return str(k)

    def issue(self, issuer, target, reason="rogue", key=None):
        env = dict(os.environ, KILL_ORDERS_DIR=str(self.orders), KILL_ORDER_ISSUER=issuer,
                   KILL_ORDER_KEY=key or str(self.root / "keys" / issuer))
        return subprocess.run(["bash", str(KILL), "issue", target, reason], env=env, capture_output=True, text=True, timeout=60)

    def creds(self, name, expires_at_s, refresh_expires_s):
        d = self.root / "home" / name / ".claude"; d.mkdir(parents=True, exist_ok=True)
        (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "FAKE", "expiresAt": expires_at_s * 1000,
            "refreshToken": "FAKE", "refreshTokenExpiresAt": refresh_expires_s * 1000}}))

    def codex(self, name, last_refresh_s, refresh_token=True, session="active"):
        """Fake ~/.codex/auth.json (timestamp + presence of a refresh token only) and daemon state."""
        import datetime
        d = self.root / "home" / name / ".codex"; d.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.fromtimestamp(last_refresh_s, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
        (d / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
            "tokens": {"id_token": "FAKE", "access_token": "FAKE", "refresh_token": "FAKE" if refresh_token else "",
                       "account_id": "00000000-0000-0000-0000-000000000001"}, "last_refresh": ts}))
        if session:
            u = self.root / "codex-unit"; u.mkdir(exist_ok=True); (u / name).write_text(session + "\n")
        return self

    def keys(self, name, comments):
        d = self.root / "home" / name / ".ssh"; d.mkdir(parents=True, exist_ok=True)
        (d / "authorized_keys").write_text("".join(f"{FAKE_KEY} {c}\n" for c in comments))

    def last_login(self, name, epoch):
        (self.root / "lastlogin" / name).write_text(str(epoch))

    def created(self, name, epoch):
        (self.root / "created" / name).write_text(str(epoch))

    def run(self, *args, env=None):
        env = dict(os.environ, LIVENESS_FAKE_ROOT=str(self.root), **(env or {}))
        p = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def actions(self):
        f = self.root / "actions.log"
        return f.read_text() if f.exists() else ""


class Verdicts(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(); self.fr = FakeRoot(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # -- agents ---------------------------------------------------------
    def test_live_agent(self):
        self.fr.account("bot1", "ctdc-agents").creds("bot1", NOW + 20 * 3600, NOW + 300 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("bot1 (ctdc-agents): live", out)

    def test_agent_refresh_expired(self):
        self.fr.account("bot1", "ctdc-agents").creds("bot1", NOW + 3600, NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("refresh token expired", out)

    def test_agent_access_token_stale_8_days(self):
        self.fr.account("bot1", "ctdc-agents").creds("bot1", NOW - 8 * DAY, NOW + 300 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("last refreshed", out)

    def test_agent_access_token_6_days_is_still_live(self):
        self.fr.account("bot1", "ctdc-agents").creds("bot1", NOW - 6 * DAY, NOW + 300 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("live", out)

    def test_agent_no_credentials(self):
        self.fr.account("bot1", "ctdc-agents")
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("no readable credentials", out)

    # -- humans ---------------------------------------------------------
    def test_live_human(self):
        self.fr.account("alice", "ctdc-ops").keys("alice", ["alice@laptop"]); self.fr.last_login("alice", NOW - 2 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("alice (ctdc-ops): live", out)

    def test_human_nologin_shell(self):
        self.fr.account("alice", "ctdc-ops", shell="/usr/sbin/nologin").keys("alice", ["alice@laptop"]); self.fr.last_login("alice", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("login shell", out)

    def test_human_devnull_shell(self):
        self.fr.account("alice", "ctdc-ops", shell="/dev/null").keys("alice", ["alice@laptop"]); self.fr.last_login("alice", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1)

    def test_human_two_keys(self):
        self.fr.account("alice", "ctdc-ops").keys("alice", ["alice@laptop", "alice@phone"]); self.fr.last_login("alice", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("need exactly 1", out)

    def test_human_wrong_comment(self):
        self.fr.account("alice", "ctdc-ops").keys("alice", ["bob@laptop"]); self.fr.last_login("alice", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("comment", out)

    def test_human_idle_15_days(self):
        self.fr.account("alice", "ctdc-ops").keys("alice", ["alice@laptop"]); self.fr.last_login("alice", NOW - 15 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("no login for", out)

    def test_new_account_within_grace(self):
        self.fr.account("alice", "ctdc-ops").keys("alice", ["alice@laptop"]); self.fr.created("alice", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("grace", out)

    def test_new_account_past_grace_never_logged_in(self):
        self.fr.account("alice", "ctdc-ops").keys("alice", ["alice@laptop"]); self.fr.created("alice", NOW - 5 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("past the new-account grace", out)

    # -- account kinds (2026-10-04 17:00 ET): ssh-mode agents, services, preloaded ------
    def test_ssh_agent_live(self):
        self.fr.account("cowork", "ctdc-agents", mode="ssh").keys("cowork", ["cowork@corporatetraveldc-dispatch"]); self.fr.last_login("cowork", NOW - 2 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("cowork (ctdc-agents): live", out); self.assertIn("kind=agent/ssh", out)
        self.assertNotIn("credentials", out)   # no Claude login factor for an ssh-mode agent

    def test_ssh_agent_idle_8_days_is_stale(self):
        # agents idle out at 7 days (LIVENESS_SSH_MAX_IDLE), humans at 14
        self.fr.account("cowork", "ctdc-agents", mode="ssh").keys("cowork", ["cowork@corporatetraveldc-dispatch"]); self.fr.last_login("cowork", NOW - 8 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("no login for 8.0d (> 7.0d)", out)
        self.fr.last_login("cowork", NOW - 6 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0)

    def test_ssh_agent_key_rules_apply(self):
        self.fr.account("cowork", "ctdc-agents", mode="ssh").keys("cowork", ["ctdc-agent@corporatetraveldc-dispatch"]); self.fr.last_login("cowork", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("comment", out)

    def test_ssh_agent_custom_idle_window(self):
        self.fr.account("cowork", "ctdc-agents", mode="ssh").keys("cowork", ["cowork@laptop"]); self.fr.last_login("cowork", NOW - 2 * DAY)
        env = dict(os.environ, LIVENESS_FAKE_ROOT=str(self.fr.root), LIVENESS_SSH_MAX_IDLE=str(DAY))
        p = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1); self.assertIn("no login for 2.0d (> 1.0d)", p.stdout + p.stderr)

    # -- codex-mode agents (2026-10-08) ----------------------------------
    def test_codex_agent_live(self):
        self.fr.account("codexbot", "ctdc-agents", mode="codex").codex("codexbot", NOW - 5 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("codexbot (ctdc-agents): live", out)
        self.assertIn("kind=agent/codex", out); self.assertIn("session=active", out)
        self.assertNotIn("credentials", out)            # never judged by Claude credentials

    def test_codex_login_stale_after_14_days(self):
        self.fr.account("codexbot", "ctdc-agents", mode="codex").codex("codexbot", NOW - 15 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("Codex login last refreshed 15.0d ago", out)

    def test_codex_without_refresh_token_is_stale(self):
        self.fr.account("codexbot", "ctdc-agents", mode="codex").codex("codexbot", NOW - DAY, refresh_token=False)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("no usable Codex login", out)

    def test_codex_fresh_login_but_no_daemon_is_stale(self):
        self.fr.account("codexbot", "ctdc-agents", mode="codex").codex("codexbot", NOW - DAY, session=None)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("neither its remote-control unit nor a codex process", out)

    def test_codex_new_account_without_login_gets_grace(self):
        self.fr.account("codexbot", "ctdc-agents", mode="codex"); self.fr.created("codexbot", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("codex login --device-auth", out)

    def test_codex_agent_with_claude_creds_only_is_stale(self):
        # the failure the codex mode exists to prevent, mirrored: Claude creds do not keep a codex agent alive
        self.fr.account("codexbot", "ctdc-agents", mode="codex").creds("codexbot", NOW + 3600, NOW + 300 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("no usable Codex login", out)

    def test_service_live_without_any_login(self):
        self.fr.account("llama", "ctdc-agents", shell="/usr/sbin/nologin", role="service", mode="none")
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("llama (ctdc-agents): live", out)
        self.assertIn("login=n/a(service)", out); self.assertIn("key=active", out)

    def test_service_inactive_signer_is_inert(self):
        self.fr.account("llama", "ctdc-agents", shell="/usr/sbin/nologin", role="service", mode="none"); self.fr.signer("llama", "inactive", "service", "service")
        rc, out = self.fr.run("--execute", "--i-am-the-operator"); self.assertEqual(rc, 1); self.assertIn("board signer deactivated", out)
        acts = self.fr.actions()
        self.assertIn("chage -E 0 llama", acts); self.assertIn("revoke-tokens llama", acts)
        # a service has no sessions, no user manager, no linger -- those steps are not attempted
        self.assertNotIn("terminate-user llama", acts); self.assertNotIn("systemctl --user", acts); self.assertNotIn("disable-linger llama", acts)
        self.assertTrue((self.fr.root / "var/lib/corporatetraveldc/team-liveness/inert/llama").exists())

    def test_service_token_revocation_is_inert(self):
        self.fr.account("llama", "ctdc-agents", shell="/usr/sbin/nologin", role="service", mode="none"); self.fr.revoked("llama", NOW - 3600)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("revoked within", out)

    def test_service_reactivate_skips_linger(self):
        self.fr.account("llama", "ctdc-agents", shell="/usr/sbin/nologin", role="service", mode="none"); self.fr.signer("llama", "inactive", "service", "service")
        self.fr.run("--execute", "--i-am-the-operator")
        rc, out = self.fr.run("--reactivate", "llama", "--i-am-the-operator")
        self.assertEqual(rc, 0); self.assertIn("activate llama", self.fr.actions()); self.assertNotIn("enable-linger llama", self.fr.actions())
        self.assertIn("service identity", out)

    def test_preloaded_is_skipped_not_killed(self):
        # expired, no creds, inactive signer -- everything that would make a live account inert
        self.fr.account("codex", "ctdc-agents", mode="claude", preloaded=1); self.fr.signer("codex", "inactive")
        (self.fr.root / "expire" / "codex").write_text("Jan 01, 1970")
        self.fr.account("dispatch", "ctdc-agents", shell="/usr/sbin/nologin", mode="none", preloaded=1); self.fr.signer("dispatch", "inactive", "service", "service")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertEqual(rc, 0, out); self.assertIn("preloaded", out); self.assertIn("plan.sh --activate codex", out)
        self.assertEqual(self.fr.actions(), ""); self.assertFalse((self.fr.root / "var").exists())

    def test_status_shows_kind_and_mode(self):
        self.fr.account("cowork", "ctdc-agents", mode="ssh").keys("cowork", ["cowork@x"]); self.fr.last_login("cowork", NOW - DAY)
        self.fr.account("llama", "ctdc-agents", shell="/usr/sbin/nologin", mode="none")
        self.fr.account("codex", "ctdc-agents", mode="claude", preloaded=1); self.fr.signer("codex", "inactive")
        self.fr.account("bot1", "ctdc-agents").creds("bot1", NOW + 3600, NOW + 300 * DAY)   # not in the registry -> defaults
        rc, out = self.fr.run("--status")
        head = out.splitlines()[0]
        for col in ("ACCOUNT", "GROUP", "KIND", "MODE", "STATE"):
            self.assertIn(col, head)
        rows = {l.split()[0]: l.split() for l in out.splitlines()[1:] if l.strip()}
        self.assertEqual(rows["cowork"][2:5], ["agent", "ssh", "live"])
        self.assertEqual(rows["llama"][2:5], ["service", "none", "live"])
        self.assertEqual(rows["codex"][2:5], ["agent", "claude", "preloaded"])
        self.assertEqual(rows["bot1"][2:5], ["agent", "claude", "live"])

    # -- actions ---------------------------------------------------------
    def test_dry_run_performs_no_action(self):
        self.fr.account("bot1", "ctdc-agents")
        self.fr.run(); self.assertEqual(self.fr.actions(), "")
        self.assertFalse((self.fr.root / "var").exists())

    def test_execute_records_inert_actions_and_marker(self):
        self.fr.account("bot1", "ctdc-agents")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        acts = self.fr.actions()
        for needle in ("loginctl terminate-user bot1", "usermod -L bot1", "chage -E 0 bot1", "loginctl disable-linger bot1", "ntfy 4"):
            self.assertIn(needle, acts)
        self.assertTrue((self.fr.root / "var/lib/corporatetraveldc/team-liveness/inert/bot1").exists())
        # second run: already inert -> skipped silently, no new actions
        n = acts.count("usermod -L"); self.fr.run("--execute", "--i-am-the-operator")
        self.assertEqual(self.fr.actions().count("usermod -L"), n)

    def test_reactivate_reverses(self):
        self.fr.account("bot1", "ctdc-agents"); self.fr.run("--execute", "--i-am-the-operator")
        rc, out = self.fr.run("--reactivate", "bot1", "--i-am-the-operator")
        self.assertEqual(rc, 0); self.assertIn("usermod -U bot1", self.fr.actions()); self.assertIn("chage -E -1 bot1", self.fr.actions())
        self.assertFalse((self.fr.root / "var/lib/corporatetraveldc/team-liveness/inert/bot1").exists())
        self.assertIn("sudo -u bot1 -i claude", out)

    def test_execute_requires_operator_flag(self):
        self.fr.account("bot1", "ctdc-agents")
        rc, out = self.fr.run("--execute"); self.assertEqual(rc, 3)

    def test_operator_and_root_never_evaluated(self):
        # the fake accounts list is authoritative in tests; the real accounts()
        # filters corporatetraveldc/root -- assert the status table omits them when present in a group
        self.fr.account("alice", "ctdc-ops").keys("alice", ["alice@laptop"]); self.fr.last_login("alice", NOW - DAY)
        rc, out = self.fr.run("--status"); self.assertIn("alice", out); self.assertNotIn("corporatetraveldc ", out)


class AndSemantics(unittest.TestCase):
    """Liveness is an AND: login alive is not enough."""
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(); self.fr = FakeRoot(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _live_agent(self, name="bot1", **kw):
        self.fr.account(name, "ctdc-agents", **kw).creds(name, NOW + 20 * 3600, NOW + 300 * DAY)

    def test_live_login_but_inactive_signer_is_inert(self):
        self._live_agent(); self.fr.signer("bot1", "inactive")
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("key revoked", out)

    def test_no_signer_past_grace_is_inert(self):
        self._live_agent(signer=False)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("no board signer registered", out)

    def test_no_signer_within_grace_is_tolerated(self):
        self._live_agent(signer=False); self.fr.created("bot1", NOW - DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0); self.assertIn("unregistered(grace)", out)

    def test_token_revoked_in_lookback_is_inert(self):
        self._live_agent(); self.fr.revoked("bot1", NOW - 3600)
        rc, out = self.fr.run(); self.assertEqual(rc, 1); self.assertIn("revoked within", out)

    def test_token_revoked_outside_lookback_is_fine(self):
        self._live_agent(); self.fr.revoked("bot1", NOW - 3 * DAY)
        rc, out = self.fr.run(); self.assertEqual(rc, 0)

    def test_registry_unavailable_holds_never_kills_never_passes(self):
        # duel M7: an unreachable registry is a HOLD (exit 2), not "skip the key factor"
        self._live_agent()
        import shutil; shutil.rmtree(self.fr.root / "signers")
        rc, out = self.fr.run(); self.assertEqual(rc, 2); self.assertIn("HOLD", out); self.assertNotIn(": live", out)
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertEqual(rc, 2); acts = self.fr.actions()
        self.assertNotIn("chage -E 0", acts); self.assertIn("ntfy 4", acts)

    def test_inert_revokes_signer_and_all_tokens(self):
        self._live_agent(); self.fr.signer("bot1", "inactive")
        self.fr.run("--execute", "--i-am-the-operator"); acts = self.fr.actions()
        self.assertIn("deactivate bot1", acts); self.assertIn("revoke-tokens bot1", acts)

    def test_reactivate_does_not_restore_tokens(self):
        self._live_agent(); self.fr.signer("bot1", "inactive"); self.fr.run("--execute", "--i-am-the-operator")
        rc, out = self.fr.run("--reactivate", "bot1", "--i-am-the-operator")
        self.assertIn("activate bot1", self.fr.actions()); self.assertIn("NOT restored", out)


@unittest.skipIf(__import__("shutil").which("ssh-keygen") is None, "ssh-keygen required")
class KillOrders(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(); self.fr = FakeRoot(self._tmp.name)
        self.fr.orders.mkdir(parents=True)
        # target: a live agent
        self.fr.account("bot1", "ctdc-agents").creds("bot1", NOW + 20 * 3600, NOW + 300 * DAY)
        # humans with real keys
        for h, role in (("alice", "admin"), ("bob", "member"), ("carol", "member"), ("dave", "admin")):
            self.fr.account(h, "ctdc-ops", role=role, kind="human").keys(h, [f"{h}@laptop"]); self.fr.last_login(h, NOW - DAY)
            self.fr.keypair(h)

    def tearDown(self):
        self._tmp.cleanup()

    def _arch(self, kind, target):
        return self.fr.root / "var/lib/ctdc-liveness/orders-archive" / kind / target

    def _executed(self, target="bot1"):
        d = self._arch("executed", target); return d.exists() and any(d.glob("*.json"))

    def test_issue_writes_json_and_sig(self):
        r = self.fr.issue("alice", "bot1"); self.assertEqual(r.returncode, 0, r.stderr)
        files = sorted(p.name for p in (self.fr.orders / "bot1").iterdir())
        self.assertTrue(any(f.endswith(".json") for f in files) and any(f.endswith(".sig") for f in files))
        self.assertTrue((self.fr.orders / ".new").exists())

    def test_one_admin_order_executes(self):
        self.fr.issue("alice", "bot1", "rogue")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertIn("kill order executed: alice", out); self.assertIn("ntfy 5", self.fr.actions())
        self.assertTrue(self._executed()); self.assertTrue((self.fr.root / "var/lib/corporatetraveldc/team-liveness/inert/bot1").exists())

    def test_one_non_admin_order_does_not_execute(self):
        self.fr.issue("bob", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertEqual(rc, 0); self.assertIn("pending: 1 of 2 non-admin", out); self.assertFalse(self._executed())

    def test_two_non_admin_orders_execute(self):
        self.fr.issue("bob", "bot1"); self.fr.issue("carol", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertIn("kill order executed:", out); self.assertIn("bob", out); self.assertIn("carol", out); self.assertTrue(self._executed())

    def test_same_issuer_twice_is_one_vote(self):
        self.fr.issue("bob", "bot1"); time.sleep(1.1); self.fr.issue("bob", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertFalse(self._executed()); self.assertIn("1 of 2", out)

    def test_admin_target_needs_quorum(self):
        self.fr.issue("bob", "dave")          # one member against an admin
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertFalse(self._executed("dave")); self.assertIn("admin target", out)
        self.fr.issue("alice", "dave")        # second distinct issuer -> quorum 2
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertTrue(self._executed("dave")); self.assertIn("kill order executed:", out)

    def test_order_against_operator_rejected(self):
        self.fr.issue("alice", "corporatetraveldc")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertIn("never a kill-order target", out)
        self.assertTrue(any(self._arch("rejected", "corporatetraveldc").glob("*.why")))
        self.assertNotIn("INERT corporatetraveldc", out)

    def test_bad_signature_rejected(self):
        self.fr.issue("alice", "bot1", key=str(self.fr.root / "keys" / "bob"))   # signed with bob's key, claims alice
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertIn("signature does not verify", out); self.assertFalse(self._executed())

    def test_stale_order_rejected(self):
        self.fr.issue("alice", "bot1")
        j = next((self.fr.orders / "bot1").glob("*.json"))
        import json as _j
        d = _j.loads(j.read_text()); d["ts"] = NOW - 8 * DAY
        j.write_text(_j.dumps(d, sort_keys=True, separators=(",", ":")))   # signature now mismatches too, but ts check comes first
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertIn("timestamp stale", out); self.assertFalse(self._executed())

    def test_issuer_cannot_target_itself(self):
        r = self.fr.issue("alice", "alice"); self.assertNotEqual(r.returncode, 0)

    def test_inert_issuer_cannot_kill(self):
        self.fr.signer("bob", "inactive", "member", "human"); self.fr.signer("carol", "active", "member", "human")
        self.fr.issue("bob", "bot1"); self.fr.issue("carol", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertIn("issuer bob has no active signer", out); self.assertFalse(self._executed())

    def test_dry_run_evaluates_orders_but_acts_on_nothing(self):
        self.fr.issue("alice", "bot1")
        rc, out = self.fr.run(); self.assertIn("would make inert", out); self.assertEqual(self.fr.actions(), ""); self.assertFalse(self._executed())


@unittest.skipIf(__import__("shutil").which("ssh-keygen") is None, "ssh-keygen required")
class OrderStaging(KillOrders):
    """2026-10-04 duel H5/X2/M8/M7: root stages orders into a root-only tree,
    refuses symlinks/oversize/squats, caps a run, never mutates in dry-run,
    and reactivation archives pending orders."""

    def _staging(self, target="bot1"):
        return self.fr.root / "var/lib/ctdc-liveness/staging" / target

    def test_dry_run_moves_nothing(self):
        self.fr.issue("alice", "bot1")
        before = sorted(p.name for p in (self.fr.orders / "bot1").iterdir())
        rc, out = self.fr.run()
        self.assertEqual(sorted(p.name for p in (self.fr.orders / "bot1").iterdir()), before)
        self.assertFalse((self.fr.root / "var/lib/ctdc-liveness/staging").exists())
        self.assertFalse((self.fr.root / "var/lib/ctdc-liveness/orders-archive").exists())

    def test_root_only_trees_outside_container_mount(self):
        """2026-10-04 20:05 incident: a root 0700 dir under the :z-mounted
        /var/lib/corporatetraveldc broke every rootless container start."""
        self.fr.issue("alice", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        mounted = self.fr.root / "var/lib/corporatetraveldc"
        for p in mounted.rglob("*"):
            if p.is_dir():
                self.assertNotEqual(p.name, "staging", p)
                self.assertNotEqual(p.name, "orders-archive", p)
        self.assertTrue((self.fr.root / "var/lib/ctdc-liveness/orders-archive").exists())

    def test_status_moves_nothing_even_with_bad_orders(self):
        (self.fr.orders / "bot1").mkdir(exist_ok=True)
        (self.fr.orders / "bot1" / "bob.1.json").write_text("{}")   # unparseable, no sig
        self.fr.run("--status")
        self.assertTrue((self.fr.orders / "bot1" / "bob.1.json").exists())

    def test_execute_stages_into_root_only_tree(self):
        self.fr.issue("bob", "bot1")   # one non-admin vote: stays pending, but staged
        self.fr.run("--execute", "--i-am-the-operator")
        self.assertFalse(any((self.fr.orders / "bot1").glob("*.json")))
        self.assertTrue(any(self._staging().glob("*.json")))

    def test_symlink_order_rejected_target_untouched(self):
        victim = self.fr.root / "victim.txt"; victim.write_text("keep")
        d = self.fr.orders / "bot1"; d.mkdir(exist_ok=True)
        (d / "alice.1.json").symlink_to(victim); (d / "alice.1.sig").write_text("x")
        rc, out = self.fr.run("--execute", "--i-am-the-operator")
        self.assertIn("symlink or non-regular", out); self.assertEqual(victim.read_text(), "keep")
        self.assertFalse(self._executed())

    def test_squatted_target_dir_rejected(self):
        self.fr.issue("alice", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator", env={"LIVENESS_FAKE_ROOT_UID": "999999"})
        self.assertIn("not created by root (squat)", out); self.assertFalse(self._executed())
        self.assertFalse((self.fr.root / "var/lib/corporatetraveldc/team-liveness/inert/bot1").exists())

    def test_per_run_cap(self):
        self.fr.issue("bob", "bot1"); self.fr.issue("carol", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator", env={"LIVENESS_ORDER_MAX_FILES": "1"})
        self.assertIn("per-run cap", out); self.assertIn("kill-order flood", self.fr.actions())
        self.assertFalse(self._executed())   # only one vote got through: no non-admin quorum

    def test_oversize_rejected(self):
        self.fr.issue("alice", "bot1")
        rc, out = self.fr.run("--execute", "--i-am-the-operator", env={"LIVENESS_ORDER_MAX_BYTES": "10"})
        self.assertIn("larger than 10 bytes", out); self.assertFalse(self._executed())

    def test_reactivate_archives_pending_orders(self):
        self.fr.issue("bob", "bot1")                       # pending (one non-admin)
        self.fr.run("--execute", "--i-am-the-operator")    # staged, not executed
        self.fr.signer("bot1", "inactive"); self.fr.run("--execute", "--i-am-the-operator")   # inert for another reason
        self.fr.signer("bot1", "active")
        self.fr.run("--reactivate", "bot1", "--i-am-the-operator")
        self.assertFalse(any(self._staging().glob("*.json")))
        self.assertTrue(any(self._arch("superseded", "bot1").glob("*.json")))


class LoginFactors(unittest.TestCase):
    """2026-10-04 duel U5: lastlog2 parsing, the PAM state file, and claude
    session corroboration of the self-attested credentials file."""
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(); self.fr = FakeRoot(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _ssh_agent(self, name="cow"):
        self.fr.account(name, "ctdc-agents", mode="ssh").keys(name, [f"{name}@corporatetraveldc-dispatch"])
        (self.fr.root / "lastlogin" / name).unlink(missing_ok=True)

    def _lastlog2(self, name, latest):
        d = self.fr.root / "lastlog2"; d.mkdir(exist_ok=True)
        (d / name).write_text(f"Username Port From Latest\n{name} pts/1 192.0.2.7 {latest}\n")

    def test_lastlog2_recent_is_live(self):
        self._ssh_agent()
        self._lastlog2("cow", time.strftime("%a %b %e %H:%M:%S %z %Y", time.localtime(NOW - DAY)))
        rc, out = self.fr.run(); self.assertIn("cow (ctdc-agents): live", out)

    def test_lastlog2_never_past_grace_is_stale(self):
        self._ssh_agent()
        (self.fr.root / "lastlog2").mkdir(exist_ok=True)
        (self.fr.root / "lastlog2" / "cow").write_text("Username Port From Latest\ncow **Never logged in**\n")
        rc, out = self.fr.run(); self.assertIn("never logged in", out)

    def test_pam_state_file_preferred(self):
        self._ssh_agent()
        self._lastlog2("cow", "Mon Sep  1 10:00:00 -0400 2026")   # old
        d = self.fr.root / "var/lib/corporatetraveldc/team-liveness/lastlogin"; d.mkdir(parents=True)
        (d / "cow").write_text(str(NOW - 3600))
        rc, out = self.fr.run(); self.assertIn("cow (ctdc-agents): live", out)

    def _claude_agent(self, unit_state=None):
        self.fr.account("bot1", "ctdc-agents").creds("bot1", NOW + 20 * 3600, NOW + 300 * DAY)
        if unit_state is not None:
            d = self.fr.root / "rc-unit"; d.mkdir(exist_ok=True); (d / "bot1").write_text(unit_state)

    def test_claude_creds_plus_active_unit_is_live(self):
        self._claude_agent("active"); rc, out = self.fr.run()
        self.assertIn("session=active", out); self.assertIn("bot1 (ctdc-agents): live", out)

    def test_claude_creds_but_unit_inactive_is_stale(self):
        self._claude_agent("inactive"); rc, out = self.fr.run()
        self.assertIn("remote-control unit is not active", out); self.assertEqual(rc, 1)

    def test_claude_without_unit_falls_back_to_creds(self):
        self._claude_agent(None); rc, out = self.fr.run()
        self.assertIn("session=no-unit", out); self.assertIn("bot1 (ctdc-agents): live", out)


if __name__ == "__main__":
    unittest.main()
