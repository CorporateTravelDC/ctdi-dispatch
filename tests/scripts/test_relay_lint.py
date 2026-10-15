"""2026-10-15: relay-lint catches every relay bug that bit on 2026-10-14/15 (standing rule: bug once, lint forever)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/relay-lint.py"


def lint(body: str):
    spec = importlib.util.spec_from_file_location("relay_lint", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return [why for _, why, _ in m.lint("bash <<'RELAY'\nset -euo pipefail\n" + body + "\nRELAY\n")]


def test_clean_relay_passes():
    assert lint("cd /opt/x\nsystemctl --user list-timers --all --no-pager x.timer || true\n"
                "read -rs -p 'URL: ' U </dev/tty\nscripts/push-public.sh </dev/null") == []


def test_sigpipe_head():
    assert any("SIGPIPE" in w for w in lint("systemctl --user list-timers --no-pager x.timer | head -2"))


def test_read_without_tty():
    assert any("reads the relay text" in w for w in lint('read -rs -p "URL: " HCURL; echo'))


def test_sudo_on_user_unit():
    assert any("user unit" in w for w in lint("sudo systemctl disable --now corporatetraveldc-net-failover-watchdog.timer"))
    assert not lint("sudo systemctl restart tailscaled")


def test_add_N_and_bans():
    assert any("drops deletions" in w for w in lint('git -C "$G" add -N -A'))
    assert any("screen" in w for w in lint("screen -S Remote-Admin -X quit"))
    assert any("ssh-agent" in w for w in lint('eval "$(ssh-agent -s)"; ssh-add'))


def test_secret_on_argv_and_unquoted_nested_heredoc():
    assert any("bearer" in w for w in lint('curl -H "Authorization: Bearer $TOK" http://x'))
    assert any("unquoted nested heredoc" in w for w in lint("python3 - <<EOF\nprint(1)\nEOF"))
    assert not lint("python3 - <<'PY'\nimport sys\nPY")


def test_missing_wrapper():
    spec = importlib.util.spec_from_file_location("relay_lint", SCRIPT)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert len(m.lint("echo hi\n")) == 2


def test_live_twin_install_must_precede_signing():
    bad = "scripts/sign-manifest.sh; git add -A\ninstall -m 644 .config/containers/systemd/x.container \"$HOME/.config/containers/systemd/x.container\""
    assert any("after sign-manifest" in w for w in lint(bad))
    good = "install -m 644 .config/containers/systemd/x.container \"$HOME/.config/containers/systemd/x.container\"\nscripts/sign-manifest.sh; git add -A"
    assert not lint(good)
