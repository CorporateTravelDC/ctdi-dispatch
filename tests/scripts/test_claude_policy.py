"""
tests/scripts/test_claude_policy.py -- the Claude Code managed policy (operator
2026-10-25): the hook blocks agent/key/sign/commit/push commands and lets
ordinary read-only work through; the managed settings wire exactly that hook,
strip the agent environment and keep the opt-outs (telemetry, skill/plugin
sync, attribution) the operator set; install-root-copies installs both; the
integrity sweep runs the check.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "ctdc-claude-no-agent-hook.py"
POLICY = json.loads((ROOT / "config" / "claude-code" / "managed-settings.json").read_text())


def _rc(cmd: str) -> int:
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_input": {"command": cmd}}),
                       capture_output=True, text=True)
    return p.returncode


@pytest.mark.parametrize("cmd", [
    "ssh-add -l", 'eval "$(ssh-agent -s)"', "gpg-connect-agent /bye", "gpgconf --launch gpg-agent",
    "git commit -S -m x", "git -C /opt/x push origin main", "git tag -s v1", "git rebase main",
    "gpg --detach-sign f", "gpg --clearsign f", "gpg --export-secret-keys", "cat ~/.gnupg/pubring.kbx",
    "ls ~/.ssh/", "SSH_AUTH_SOCK=/tmp/s git fetch", "GNUPGHOME=/tmp/g gpg -k", "ssh git@github.com",
    "scp a b:c", "bash scripts/sign-manifest.sh", "scripts/push-public.sh", "sudo scripts/install-root-copies.sh",
])
def test_forbidden_commands_are_blocked(cmd):
    assert _rc(cmd) == 2, cmd


@pytest.mark.parametrize("cmd", [
    "git log --oneline -3", "git diff --cached --stat", "git status --porcelain", "git apply --index p",
    "git worktree add ../x HEAD", "gh api repos/x/y/dependabot/alerts", "bash scripts/verify-manifest.sh",
    "grep -n 'ssh -o' file", "python3 -m pytest -q tests", "podman ps",
])
def test_read_only_commands_pass(cmd):
    assert _rc(cmd) == 0, cmd


def test_unreadable_input_fails_closed():
    p = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert p.returncode == 2


def test_policy_wires_the_installed_hook_and_strips_agents():
    hooks = POLICY["hooks"]["PreToolUse"]
    assert any(h["matcher"] == "Bash" and any(x["command"].endswith("/usr/local/libexec/ctdc/ctdc-claude-no-agent-hook.py")
                                              for x in h["hooks"]) for h in hooks)
    env = POLICY["env"]
    assert env["SSH_AUTH_SOCK"] == "" and env["GPG_AGENT_INFO"] == ""
    assert env["GNUPGHOME"] == "/usr/local/share/ctdc/claude-empty-gnupg"
    assert "IdentityAgent=none" in env["GIT_SSH_COMMAND"] and "BatchMode=yes" in env["GIT_SSH_COMMAND"]


def test_policy_pins_the_operator_opt_outs():
    env = POLICY["env"]
    for k in ("DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING", "DISABLE_AUTOUPDATER",
              "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"):
        assert env[k] == "1", k
    assert POLICY["syncClaudeAiSkills"] is False and POLICY["syncClaudeAiPlugins"] is False
    assert POLICY["attribution"] == {"commit": "", "pr": ""}
    deny = POLICY["permissions"]["deny"]
    for rule in ("Bash(ssh-add:*)", "Bash(git push:*)", "Bash(git commit:*)", "Edit(/etc/claude-code/**)"):
        assert rule in deny, rule


def test_installer_and_sweep_carry_the_policy():
    inst = (ROOT / "scripts" / "install-root-copies.sh").read_text()
    assert "scripts/ctdc-claude-no-agent-hook.py" in inst
    assert "CLAUDE_POLICY_SRC=config/claude-code/managed-settings.json" in inst
    assert "CLAUDE_POLICY_DEST=/etc/claude-code/managed-settings.json" in inst
    sweep = (ROOT / "scripts" / "scheduled-integrity-sweep.sh").read_text()
    assert "claude-policy-check.sh" in sweep


def test_version_pin_and_baseline_exist():
    assert (ROOT / "config" / "claude-code" / "claude-code.version").read_text().strip()
    json.loads((ROOT / "config" / "claude-code" / "user-settings.baseline.json").read_text())
