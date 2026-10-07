"""
tests/scripts/test_segmentation_plan_dryrun.py

scripts/agent-segmentation/plan.sh and rollback.sh are DRY-RUN BY DEFAULT and
must print the expected step list for both account kinds -- humans (ctdc-ops,
humans.slice) and agents (ctdc-agents, agents.slice) -- without touching the
box. These tests run the real scripts as the current user (never root, never
--execute) and assert on the printed plan plus the absence of side effects.
"""
from __future__ import annotations

import os
import pwd
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLAN = REPO / "scripts" / "agent-segmentation" / "plan.sh"
ROLLBACK = REPO / "scripts" / "agent-segmentation" / "rollback.sh"


def run(script, *args):
    p = subprocess.run(["bash", str(script), *args], capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


def steps(out):
    return [l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("== step ")]


class PlanDryRun(unittest.TestCase):
    def test_shared_only(self):
        rc, out, err = run(PLAN, "--dry-run")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 3)
        self.assertIn("groups: ctdc-dev", s[0]); self.assertIn("ctdc-ops", s[0]); self.assertIn("ctdc-agents", s[0])
        self.assertIn("repo: group READ only", s[1])
        self.assertIn("secrets subsets", s[2]); self.assertIn("--profile ops", out); self.assertIn("--profile agent", out)
        self.assertIn("(dry-run: nothing was executed)", out)

    def test_add_agent_ctdc_agent_full_hand_over(self):
        rc, out, err = run(PLAN, "--dry-run", "--add-agent", "ctdc-agent")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 11)
        self.assertIn("agent account ctdc-agent", s[3])
        self.assertIn("agents.slice", s[4]); self.assertIn("skill-grants.sh grant ctdc-agent '*'", out); self.assertIn("skill-grants.sh apply --execute", out)
        self.assertNotIn("SKILLS_LIVE_ROOT", out)   # 2026-10-04: skills are granted (root-owned), never copied by the account
        self.assertIn("attribution", s[6])
        # the cowork key becomes ctdc-agent's: comment rewritten, removed from the operator, no keygen
        self.assertIn('ctdc-agent@corporatetraveldc-dispatch', out)
        self.assertIn("claude-cowork-dispatch$/d", out)
        # 2026-10-04 16:44: ctdc-agent inherits the cowork pubkey for INBOUND ssh but still
        # generates its own on-box SIGNING key (the cowork private half is off-box)
        self.assertIn("ssh-keygen -q -t ed25519 -N '' -C 'ctdc-agent@corporatetraveldc-dispatch' -f /home/ctdc-agent/.ssh/ctdc-agent_ed25519", out)
        self.assertIn("register the SIGNING key", out)
        self.assertIn("git config --global user.name 'ctdc-agent'", out)
        self.assertIn("sudoers", s[7]); self.assertIn("remote-control", s[8]); self.assertIn("rotate", s[10])
        self.assertIn("ctdc-agents", out); self.assertIn("useradd", out)

    def test_add_second_agent_generates_its_own_key(self):
        rc, out, err = run(PLAN, "--dry-run", "--add-agent", "ctdc-codex")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 8)
        self.assertIn("ssh-keygen -q -t ed25519 -N '' -C 'ctdc-codex@corporatetraveldc-dispatch'", out)
        self.assertIn("/home/ctdc-codex/.ssh/ctdc-codex_ed25519.pub /home/ctdc-codex/.ssh/authorized_keys", out)
        self.assertNotIn("cowork_ed25519", out)          # never a copy of another account's key
        self.assertNotIn("remote-control unit moves", out)
        self.assertNotIn("sudoers", "".join(s))
        self.assertIn("git config --global user.name 'ctdc-codex'", out)

    def test_add_human_uses_own_key_and_humans_slice(self):
        rc, out, err = run(PLAN, "--dry-run", "--add-human", "alice", "--ssh-pubkey-file", "/tmp/alice.pub")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 6)
        self.assertIn("human account alice", s[3]); self.assertIn("ctdc-ops", out)
        self.assertIn("/tmp/alice.pub /home/alice/.ssh/authorized_keys", out)
        cmds = "\n".join(l for l in out.splitlines() if l.startswith("   $ "))   # commands only, not titles
        self.assertNotIn("cowork", cmds); self.assertNotIn("ssh-keygen", cmds)
        self.assertIn("humans.slice", s[5]); self.assertIn("WITH_DISPATCH_ENV_FILES=/etc/ctdc-ops/ops-secrets.env", out)
        self.assertNotIn("sudoers", out); self.assertNotIn("remote-control", "".join(s))

    # ---- account kinds (2026-10-04 17:00 ET): ssh-mode agents, preload, services, activate, rehome
    def test_add_agent_login_mode_ssh_cowork(self):
        rc, out, err = run(PLAN, "--dry-run", "--add-agent", "ctdc-agent-cowork", "--login-mode", "ssh", "--ssh-pubkey-file", "/tmp/cowork.pub")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 8)
        self.assertIn("agent account ctdc-agent-cowork (login-mode ssh)", s[3])
        self.assertNotIn("PRELOADED", s[3])
        # registry line: name kind login_mode preloaded
        self.assertIn("printf '%s %s %s %s\\n' 'ctdc-agent-cowork' 'agent' 'ssh' '0' >> /etc/ctdc-accounts.conf", out)
        self.assertIn("chmod 0644 /etc/ctdc-accounts.conf", out)
        # the client's pubkey becomes the single inbound key, comment rewritten to the account; signing key still generated
        self.assertIn("""awk '{$NF="ctdc-agent-cowork@corporatetraveldc-dispatch"; print}' /tmp/cowork.pub > /home/ctdc-agent-cowork/.ssh/authorized_keys""", out)
        self.assertIn("ssh-keygen -q -t ed25519 -N '' -C 'ctdc-agent-cowork@corporatetraveldc-dispatch' -f /home/ctdc-agent-cowork/.ssh/ctdc-agent-cowork_ed25519", out)
        self.assertIn("no remote-control unit, the client connects over SSH", s[7])
        self.assertNotIn("corporatetraveldc-claude-remote-control.service", out)
        self.assertIn("loginctl enable-linger ctdc-agent-cowork", out)

    def test_add_agent_preload(self):
        rc, out, err = run(PLAN, "--dry-run", "--add-agent", "ctdc-agent-codex", "--preload")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 8)
        self.assertIn("login-mode claude, PRELOADED", s[3])
        self.assertIn("chage -E 0 ctdc-agent-codex", out)
        self.assertNotIn("loginctl enable-linger ctdc-agent-codex", out)
        self.assertIn("'ctdc-agent-codex' 'agent' 'claude' '1' >> /etc/ctdc-accounts.conf", out)
        self.assertIn("register the SIGNING key INACTIVE", out)
        self.assertIn("deactivate ctdc-agent-codex preloaded", out)
        self.assertIn("plan.sh --activate ctdc-agent-codex", out)

    def test_add_service(self):
        for name, preload in (("ctdc-agent-llama", False), ("ctdc-agent-dispatch", True)):
            args = ["--dry-run", "--add-service", name] + (["--preload"] if preload else [])
            rc, out, err = run(PLAN, *args)
            self.assertEqual(rc, 0, err)
            s = steps(out)
            self.assertEqual(len(s), 6, name)
            self.assertIn(f"service account {name} (kind service, login-mode none", s[3])
            self.assertIn(f"--shell /usr/sbin/nologin --groups ctdc-dev,ctdc-agents --comment 'service identity, never logs in, no sudo' {name}", out)
            self.assertIn(f"'{name}' 'service' 'none' '{1 if preload else 0}' >> /etc/ctdc-accounts.conf", out)
            self.assertIn(f"rm -f /home/{name}/.ssh/authorized_keys", out)          # no inbound key
            self.assertIn(f"-f /home/{name}/.ssh/{name}_ed25519", out)                # but a signing key
            self.assertIn(f"WITH_DISPATCH_ENV_FILES=/etc/ctdc-agent/agent-secrets.env' > /home/{name}/.config/ctdc-env.sh", out)
            self.assertIn("--kind service --role service", out)
            self.assertNotIn("loginctl enable-linger", out)
            self.assertNotIn("agents.slice", out); self.assertNotIn("remote-control", "".join(s)); self.assertNotIn("sudoers", "".join(s))
            if preload:
                self.assertIn("PRELOADED", s[3]); self.assertIn(f"chage -E 0 {name}", out); self.assertIn(f"deactivate {name} preloaded", out)
            else:
                self.assertNotIn("PRELOADED", s[3]); self.assertNotIn("chage -E 0", out); self.assertNotIn("deactivate", out)

    def test_activate(self):
        rc, out, err = run(PLAN, "--dry-run", "--activate", "ctdc-agent-codex")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 4)
        self.assertIn("activate preloaded account ctdc-agent-codex", s[3])
        self.assertIn("chage -E -1 ctdc-agent-codex", out)
        self.assertIn("usermod -U ctdc-agent-codex 2>/dev/null || usermod -p '*' ctdc-agent-codex", out)
        self.assertIn("loginctl enable-linger ctdc-agent-codex", out)
        self.assertIn("'ctdc-agent-codex' 'agent' 'claude' '0' >> /etc/ctdc-accounts.conf", out)
        self.assertIn("board-signer-ctl.sh activate ctdc-agent-codex", out)
        self.assertIn("sudo -u ctdc-agent-codex -i claude", out)

    def test_rehome_inbound_key(self):
        rc, out, err = run(PLAN, "--dry-run", "--rehome-inbound-key", "ctdc-agent", "ctdc-agent-cowork")
        self.assertEqual(rc, 0, err)
        s = steps(out)
        self.assertEqual(len(s), 4)
        self.assertIn("rehome inbound key", s[3])
        self.assertIn("""awk '{$NF="ctdc-agent-cowork@corporatetraveldc-dispatch"; print}' /home/ctdc-agent/.ssh/authorized_keys > /home/ctdc-agent-cowork/.ssh/authorized_keys""", out)
        self.assertIn("install -m 0600 -o ctdc-agent -g ctdc-agent /home/ctdc-agent/.ssh/ctdc-agent_ed25519.pub /home/ctdc-agent/.ssh/authorized_keys", out)
        self.assertNotIn("userdel", out); self.assertNotIn("useradd", out)

    def test_kind_refusals(self):
        self.assertNotEqual(run(PLAN, "--dry-run", "--add-agent", "x", "--login-mode", "ssh")[0], 0)            # ssh mode needs a pubkey file
        self.assertNotEqual(run(PLAN, "--dry-run", "--add-agent", "x", "--login-mode", "bogus")[0], 0)
        self.assertNotEqual(run(PLAN, "--dry-run", "--activate", "x", "--preload")[0], 0)
        self.assertNotEqual(run(PLAN, "--dry-run", "--add-human", "h", "--preload")[0], 0)                       # humans are never preloaded
        self.assertNotEqual(run(PLAN, "--dry-run", "--rehome-inbound-key", "a")[0], 0)                            # needs FROM and TO
        self.assertNotEqual(run(PLAN, "--dry-run", "--add-service", "x", "--add-agent", "y")[0], 0)              # one operation per invocation

    def test_rollback_remove_service(self):
        rc, out, err = run(ROLLBACK, "--dry-run", "--remove-service", "ctdc-agent-llama")
        self.assertEqual(rc, 0, err)
        self.assertIn("userdel --remove ctdc-agent-llama", out)
        self.assertIn("/^ctdc-agent-llama /d' /etc/ctdc-accounts.conf", out)
        self.assertNotIn("groupdel", out)

    def test_only_filters_steps(self):
        rc, out, _ = run(PLAN, "--dry-run", "--add-human", "bob", "--only=4,5")
        self.assertEqual(rc, 0)
        self.assertEqual(len(steps(out)), 2)

    def test_refusals(self):
        self.assertNotEqual(run(PLAN, "--execute")[0], 0)                                   # no operator flag
        self.assertNotEqual(run(PLAN, "--execute", "--i-am-the-operator")[0], 0)           # not root
        self.assertNotEqual(run(PLAN, "--dry-run", "--add-human", "root")[0], 0)
        self.assertNotEqual(run(PLAN, "--dry-run", "--add-human", "a", "--add-agent", "b")[0], 0)
        self.assertNotEqual(run(PLAN, "--dry-run", "--add-agent", "Bad Name")[0], 0)

    def test_rollback_dry_runs(self):
        rc, out, err = run(ROLLBACK, "--dry-run", "--remove-agent", "ctdc-agent", "--shared")
        self.assertEqual(rc, 0, err)
        self.assertIn("remote-control unit back under the operator", out)
        self.assertIn("userdel --remove ctdc-agent", out)
        self.assertIn("groupdel ctdc-ops", out)
        rc, out, _ = run(ROLLBACK, "--dry-run", "--remove-human", "alice")
        self.assertEqual(rc, 0); self.assertIn("userdel --remove alice", out); self.assertNotIn("groupdel", out)
        self.assertNotEqual(run(ROLLBACK, "--dry-run")[0], 0)                                # nothing to do

    def test_no_side_effects(self):
        self._groups_before = {g: subprocess.run(["getent", "group", g], capture_output=True, text=True).stdout.strip() for g in ("ctdc-ops", "ctdc-agents", "ctdc-dev")}
        # ctdc-agent exists for real since stage A (2026-10-04); only names that
        # must never exist prove the dry-run had no side effects.
        # the five target accounts (cowork/codex/llama/dispatch) may exist for real once the
        # operator has run the runbook, so the kind dry-runs are repeated here with names
        # that never exist and those are the ones asserted absent
        for args in (("--add-agent", "zz-ssh-test", "--login-mode", "ssh", "--ssh-pubkey-file", "/tmp/zz.pub"),
                     ("--add-agent", "zz-preload-test", "--preload"), ("--add-service", "zz-svc-test"),
                     ("--add-service", "zz-svc-preload-test", "--preload"), ("--activate", "zz-preload-test"),
                     ("--rehome-inbound-key", "zz-ssh-test", "zz-preload-test")):
            self.assertEqual(run(PLAN, "--dry-run", *args)[0], 0, args)
        for name in ("alice", "bob", "ctdc-codex", "zz-ssh-test", "zz-preload-test", "zz-svc-test", "zz-svc-preload-test"):
            self.assertFalse(Path(f"/home/{name}").exists(), f"/home/{name} must not exist after a dry-run")
            with self.assertRaises(KeyError):
                pwd.getpwnam(name)
        # The shared groups and /etc/ctdc-ops exist for real since stage A
        # (2026-10-04); a dry-run must simply not CHANGE them -- compare the
        # group membership lists before and after instead of asserting absence.
        after = {g: subprocess.run(["getent", "group", g], capture_output=True, text=True).stdout.strip() for g in ("ctdc-ops", "ctdc-agents", "ctdc-dev")}
        self.assertEqual(after, self._groups_before, "dry-run changed group membership")
        # the registry is written only by --execute; a dry-run never creates it
        reg = Path("/etc/ctdc-accounts.conf")
        if reg.exists():
            for name in ("zz-ssh-test", "zz-preload-test", "zz-svc-test", "zz-svc-preload-test"):
                self.assertNotIn(name + " ", reg.read_text(), f"dry-run must not register {name}")


if __name__ == "__main__":
    unittest.main()
