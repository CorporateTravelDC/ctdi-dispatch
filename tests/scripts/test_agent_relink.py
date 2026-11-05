"""
2026-11-05 -- scripts/agent-relink.sh: one link/relink flow for any on-box Claude Code agent
(operator: generalize the claude-code relink that worked 11-05 02:08Z).

Exercised in --dry-run with a fake agent-gateway.sh (AGENT_RELINK_GW), so no connector, approval,
sudo or login is touched. Covers the connector choice: an existing connector is relinked, none ->
an initial link with a derived slug, two enabled -> refuse without --slug, a disabled extra is
ignored, a foreign slug is refused, bad names are rejected.
"""
import os
import pwd
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/agent-relink.sh"
ACCOUNT = "ctdc-agent-anthropic-claude"


def _account_ready():
    try:
        pwd.getpwnam(ACCOUNT)
    except KeyError:
        return False
    return any(p.is_dir() and p.owner() == ACCOUNT for p in Path("/srv").glob("ctdc-*")
               if _safe_owner(p))


def _safe_owner(p):
    try:
        p.owner()
        return True
    except (KeyError, PermissionError, OSError):
        return False


needs_account = pytest.mark.skipif(not _account_ready(), reason="agent account / its /srv folder not on this host")


def _run(tmp_path, connectors_out, *args):
    gw = tmp_path / "gw.sh"
    gw.write_text("#!/usr/bin/env bash\nif [[ $1 == connectors ]]; then cat <<'X'\n"
                  + connectors_out + "\nX\nfi\n")
    gw.chmod(0o755)
    env = dict(os.environ, AGENT_RELINK_GW=str(gw))
    return subprocess.run(["bash", str(SCRIPT), *args, "--dry-run"], capture_output=True, text=True,
                          env=env, timeout=30)


def test_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    for w in (ROOT / "scripts/relink").glob("*.sh"):
        assert subprocess.run(["bash", "-n", str(w)]).returncode == 0, w


@pytest.mark.parametrize("bad", ["root", "corporatetraveldc", "ctdc-agent-x;rm", "../ctdc-agent-x"])
def test_rejects_non_agent_names(tmp_path, bad):
    r = _run(tmp_path, "", bad)
    assert r.returncode != 0


@needs_account
def test_relinks_the_one_existing_connector(tmp_path):
    r = _run(tmp_path, f"claude-code   {ACCOUNT}   anthropic\nclaude   {ACCOUNT}   anthropic  DISABLED", ACCOUNT)
    assert r.returncode == 0, r.stderr
    assert "relinking existing connector claude-code" in r.stdout
    assert "/mcp/claude-code" in r.stdout and "add-connector" not in r.stdout


@needs_account
def test_initial_link_derives_the_slug(tmp_path):
    r = _run(tmp_path, "cowork  ctdc-agent-anthropic-cowork  anthropic", ACCOUNT)
    assert r.returncode == 0, r.stderr
    assert f"DRY: {tmp_path}/gw.sh add-connector claude {ACCOUNT} anthropic" in r.stdout


@needs_account
def test_two_enabled_connectors_need_a_slug(tmp_path):
    out = f"claude-code  {ACCOUNT}  anthropic\nclaude  {ACCOUNT}  anthropic"
    r = _run(tmp_path, out, ACCOUNT)
    assert r.returncode != 0 and "--slug" in r.stderr
    r2 = _run(tmp_path, out, ACCOUNT, "--slug", "claude-code")
    assert r2.returncode == 0 and "/mcp/claude-code" in r2.stdout


@needs_account
def test_refuses_another_accounts_slug(tmp_path):
    r = _run(tmp_path, "cowork  ctdc-agent-anthropic-cowork  anthropic", ACCOUNT, "--slug", "cowork")
    assert r.returncode != 0 and "not an enabled connector" in r.stderr
