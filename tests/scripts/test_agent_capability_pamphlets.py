"""Registry-derived pamphlets: no cloud-only Codex claim or implied grants."""
import importlib.util
import os
import pwd
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[2]
SEG = REPO / "scripts/agent-segmentation"
spec = importlib.util.spec_from_file_location("capability_render", SEG / "render-agent-capabilities.py")
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)


@pytest.mark.parametrize("kind,mode,skill_root", [
    ("agent", "codex", ".agents/skills"),
    ("agent", "claude", ".claude/skills"),
    ("agent", "ssh", ".claude/skills"),
    ("service", "none", None),
])
def test_account_owned_paths_and_capability_boundaries(tmp_path, kind, mode, skill_root):
    account = "ctdc-agent-fixture"
    registry = tmp_path / "accounts"
    registry.write_text(f"{account} {kind} {mode} 0\n")
    with patch.object(cap.pwd, "getpwnam", return_value=SimpleNamespace(pw_dir="/fixture/home")):
        text = cap.render(account, registry)
    assert f"Account: `{account}`" in text
    assert "`/fixture/home/.config/ctdc/api-token`" in text
    assert f"added_by={account}" in text
    assert "Its existence and scopes\nare not implied" in text
    assert "Never self-mint, broaden scope" in text
    if skill_root:
        assert f"/fixture/home/{skill_root}/" in text
    else:
        assert "No home skill" in text
        assert "physical location" in text


def test_duplicate_or_unsupported_registry_fails_closed(tmp_path):
    registry = tmp_path / "accounts"
    registry.write_text("ctdc-agent-fixture agent codex 0\n" * 2)
    with pytest.raises(ValueError):
        cap.render("ctdc-agent-fixture", registry)
    registry.write_text("ctdc-agent-fixture service codex 0\n")
    with pytest.raises(ValueError):
        cap.render("ctdc-agent-fixture", registry)


def publisher_fixture(tmp_path):
    seg = tmp_path / "scripts/agent-segmentation"
    seg.mkdir(parents=True)
    target = seg / "publish-pamphlet-to-vault.sh"
    shutil.copyfile(SEG / target.name, target)
    (seg / "render-onboarding.sh").write_text(
        '#!/bin/bash\n'
        'test -z "${DEPLOYMENT_PROFILE_OVERRIDE:-}" || exit 88\n'
        'printf "fixture rendered for %s\\n" "$1"\n'
    )
    (tmp_path / "scripts/with-dispatch-env.sh").write_text(
        '#!/bin/bash\ntouch "$CREDENTIAL_CANARY"\nexit 99\n'
    )
    (tmp_path / "scripts/with-dispatch-env.sh").chmod(0o755)
    registry = tmp_path / "accounts"
    env = dict(os.environ, CTDC_ACCOUNTS_FILE=str(registry),
               CREDENTIAL_CANARY=str(tmp_path / "credential-access"),
               DEPLOYMENT_PROFILE_OVERRIDE="cloud-only-stale-value")
    return target, registry, env


@pytest.mark.parametrize("account,kind,mode,expected", [
    ("ctdc-agent-fixture-codex", "agent", "codex", "on-box account, login mode codex"),
    ("ctdc-agent-fixture-service", "service", "none", "integration may run locally or in a cloud client"),
])
def test_publisher_preview_preserves_account_context_without_credentials(
        tmp_path, account, kind, mode, expected):
    target, registry, env = publisher_fixture(tmp_path)
    registry.write_text(f"{account} {kind} {mode} 0\n")
    run = subprocess.run(["bash", str(target), account, "--render-only"],
                         env=env, capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert expected in run.stdout
    assert "You run outside the dispatch Pi" not in run.stdout
    assert "fixture rendered for " + account in run.stdout
    assert not Path(env["CREDENTIAL_CANARY"]).exists()


def test_weekly_refresh_uses_registry_and_skips_preloaded(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    refresh = scripts / "agent-pamphlet-weekly-refresh.sh"
    shutil.copyfile(REPO / "scripts" / refresh.name, refresh)
    wrapper = scripts / "with-dispatch-env.sh"
    wrapper.write_text('#!/bin/bash\nprintf "%s\\n" "${@: -1}" >>"$TEST_CALLS"\n')
    wrapper.chmod(0o755)
    registry = tmp_path / "accounts"
    registry.write_text("ctdc-agent-fixture-codex agent codex 0\n"
                        "ctdc-agent-fixture-service service none 0\n"
                        "ctdc-agent-fixture-future service none 1\n")
    calls = tmp_path / "calls"
    run = subprocess.run(["bash", str(refresh)], env=dict(os.environ,
                         CTDC_ACCOUNTS_FILE=str(registry), TEST_CALLS=str(calls)),
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert calls.read_text().splitlines() == ["ctdc-agent-fixture-codex", "ctdc-agent-fixture-service"]
    assert "corporatetraveldc-agent-pamphlet-install.service" in run.stdout


@pytest.mark.parametrize("kind,mode", [("agent", "codex"), ("service", "none"), ("human", "ssh")])
def test_full_template_binding_in_isolated_renderer(tmp_path, kind, mode):
    """Exercise the real shell/Python binding; stub only external lookups."""
    account = "ctdc-agent-fixture"
    fixture_home = str(tmp_path / "fixture-home")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "getent").write_text(
        '#!/bin/sh\nprintf "%s:x:12345:12345:fixture:%s:/bin/sh\\n" "$2" "$TEST_FIXTURE_HOME"\n')
    (bin_dir / "id").write_text(
        '#!/bin/sh\ncase "$1" in -u) echo 12345 ;; *) echo ctdc-dev ;; esac\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    seg = tmp_path / "scripts/agent-segmentation"
    seg.mkdir(parents=True)
    (tmp_path / "scripts/lib").mkdir()
    for name in ("onboarding-template.md", "render-agent-capabilities.py"):
        shutil.copyfile(SEG / name, seg / name)
    capability = seg / "render-agent-capabilities.py"
    capability.write_text(capability.read_text().replace(
        "home = pwd.getpwnam(account).pw_dir", "home = " + repr(fixture_home)))
    target = seg / "render-onboarding.sh"
    source = (SEG / target.name).read_text().replace("/opt/corporatetraveldc/private", str(tmp_path / "empty-repos"))
    target.write_text(source)
    (tmp_path / "scripts/board-signer-ctl.sh").write_text('#!/bin/bash\nprintf "fixture member\\n"\n')
    (tmp_path / "scripts/board-signer-ctl.sh").chmod(0o755)
    for name in ("scripts/lib/skill_grants.py", "scripts/render-glossary.py", "scripts/agent-segmentation/render-profiles.py"):
        (tmp_path / name).write_text('print("fixture reference data")\n')
    registry = tmp_path / "accounts"
    registry.write_text(f"{account} {kind} {mode} 0\n")
    run = subprocess.run(["bash", str(target), account], env=dict(os.environ,
                         CTDC_ACCOUNTS_FILE=str(registry), TEST_FIXTURE_HOME=fixture_home,
                         PATH=str(bin_dir) + os.pathsep + os.environ["PATH"]), capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    assert "{{CAPABILITY_ADDENDUM}}" not in run.stdout
    if kind == "human":
        assert "## Your action capabilities after segmentation" not in run.stdout
    else:
        assert f"Account: `{account}`. Kind: `{kind}`. Login mode: `{mode}`." in run.stdout
        assert "## Your action capabilities after segmentation" in run.stdout
