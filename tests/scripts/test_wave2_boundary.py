"""
tests/scripts/test_wave2_boundary.py -- contract tests for the 2026-10-04
Wave 2 remainder:

  * fail2ban's root-run actions use the root-owned INSTALLED copies and the
    install record, never the checkout (duel C1 follow-up); the rate-limit
    jail never triggers the stack-wide lockdown (Aug-26 C-1, never deployed)
  * plan.sh re-asserts the duel containment (repo group READ only, no
    systemd-journal) instead of re-opening it on every run
  * the drift check sees failed SYSTEM units
  * no Allow tap anywhere: approvals are human-signed
  * the pamphlet tells every account: draft, never publish; never approve
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (REPO / rel).read_text()


# -- fail2ban -------------------------------------------------------------------

def test_fail2ban_actions_run_installed_copies_only():
    for rel in ("fail2ban/action.d/cloudflare-token-corporatetraveldc.conf",
                "fail2ban/action.d/corporatetraveldc-lockdown.conf"):
        actions = [ln for ln in read(rel).splitlines() if re.match(r"^action(ban|unban)\s*=", ln)]
        assert actions, rel
        for ln in actions:
            assert "/opt/corporatetraveldc" not in ln, f"{rel}: root action runs the checkout: {ln}"
            assert "verify-manifest.sh" not in ln, f"{rel}: root runs the checkout's verifier"
            assert ln.split("=", 1)[1].strip().startswith("/usr/local/libexec/ctdc/installed-check.sh"), ln


def test_rate_limit_jail_never_locks_the_stack_down():
    jail = read("fail2ban/jail.d/nginx-limit-req-corporatetraveldc.conf")
    body = "\n".join(ln for ln in jail.splitlines() if not ln.lstrip().startswith("#"))
    assert "corporatetraveldc-lockdown" not in body
    assert re.search(r"^ignoreip\s*=.*127\.0\.0\.1/8", body, re.M)


def test_honeypot_jail_passes_no_token_parameter():
    jail = read("fail2ban/jail.d/nginx-honeypot-corporatetraveldc.conf")
    assert "cftoken" not in "\n".join(ln for ln in jail.splitlines() if not ln.lstrip().startswith("#"))


def test_installer_covers_the_fail2ban_closure():
    inst = read("scripts/install-root-copies.sh")
    for rel in ("scripts/installed-check.sh", "scripts/cf-honeypot-ban.sh", "scripts/cf-honeypot-notes.sh",
                "scripts/lockdown.sh", "scripts/restore-network.sh"):
        assert rel in inst, rel
    for f in (REPO / "fail2ban").rglob("*.conf"):
        assert str(f.relative_to(REPO)) in inst, f"{f} is not installed by install-root-copies.sh"
    assert "fail2ban-client -t" in inst


def test_root_run_fail2ban_scripts_never_source_env():
    for rel in ("scripts/lockdown.sh", "scripts/restore-network.sh"):
        code = "\n".join(ln for ln in read(rel).splitlines() if not ln.lstrip().startswith("#"))
        assert not re.search(r"(^|\s)(source|\.)\s+\"?\$\{?ENV_FILE", code), rel
        assert "selfcheck" in code


def test_installed_check_refuses_a_record_not_owned_by_root(tmp_path):
    d = tmp_path / "libexec"
    d.mkdir()
    (d / "installed-check.sh").write_bytes((REPO / "scripts/installed-check.sh").read_bytes())
    (d / "lockdown.sh").write_text("echo hi\n")
    (d / ".ctdc-installed").write_text("0" * 64 + "  scripts/lockdown.sh\n")
    r = subprocess.run(["bash", str(d / "installed-check.sh"), "scripts/lockdown.sh"], capture_output=True, text=True)
    if os.geteuid() != 0:
        assert r.returncode == 2 and "not root-owned" in r.stderr


# -- segmentation plan ------------------------------------------------------------

def _dry(*args):
    return subprocess.run(["bash", str(REPO / "scripts/agent-segmentation/plan.sh"), "--dry-run", *args],
                          capture_output=True, text=True, check=True).stdout


def test_plan_reasserts_read_only_repo_on_every_run():
    for args in ((), ("--add-agent", "ctdc-agent-x"), ("--add-service", "ctdc-agent-y")):
        out = _dry(*args)
        assert "g+rwX" not in out and "sharedRepository group" not in out
        assert "chmod -R g+rX,g-w" in out and "core.sharedRepository false" in out


def test_plan_never_adds_systemd_journal():
    for args in (("--add-agent", "ctdc-agent-x"), ("--add-human", "alice")):
        for ln in _dry(*args).splitlines():
            if "useradd" in ln:
                assert "systemd-journal" not in ln, ln


def test_signer_registration_note_runs_as_the_operator():
    out = _dry("--add-service", "ctdc-agent-y")
    assert "sudo cat /home/ctdc-agent-y/.ssh/ctdc-agent-y_ed25519.pub" in out
    assert "NOT under sudo" in out


def test_verify_fails_an_account_that_can_write_the_repo():
    v = read("scripts/agent-segmentation/verify.sh")
    assert 'can WRITE the repo' in v and "6e" in v and "systemd-journal" in v


# -- drift check / approvals / pamphlet ---------------------------------------------

def test_drift_check_reports_failed_system_units():
    d = read("scripts/check-claude-md-drift.sh")
    assert re.search(r"systemctl list-units [^\n]*--state=failed", d)
    assert "SYSTEM unit" in d


def test_no_allow_tap_anywhere():
    gate = read("scripts/sudo-approval-gate.sh")
    assert '"label": "Allow"' not in gate and "action=allow" not in gate
    assert "approve.sh allow" in gate
    sweep = read("src/poller/skills/board_sweep.py")
    assert '"label": "Allow"' not in sweep and "action=allow" not in sweep


def test_pamphlet_draft_never_publish_never_approve():
    t = read("scripts/agent-segmentation/onboarding-template.md")
    assert "You draft; you never publish" in t and "You never approve anything" in t
    assert "(coming" not in t and "shared group write" not in t
    assert "workspace-contribute.sh" in t
