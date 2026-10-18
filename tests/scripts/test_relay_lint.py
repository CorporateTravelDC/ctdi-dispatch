"""2026-10-15: relay-lint catches every relay bug that bit on 2026-10-14/15 (standing rule: bug once, lint forever)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/relay-lint.py"


def lint(body: str):
    spec = importlib.util.spec_from_file_location("relay_lint", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return [why for _, why, _ in m.lint("bash <<'RELAY'\nset -euo pipefail\n" + body + "\nRELAY_DONE=1\nRELAY\n")]


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
    good = ("install -m 644 .config/containers/systemd/x.container \"$HOME/.config/containers/systemd/x.container\"\nscripts/sign-manifest.sh; git add -A"
            "\nREBASELINE=1 bash scripts/stack-refresh.sh --tripwire --dry-run --now")
    assert not lint(good)


def test_unit_install_requires_tripwire_rebaseline():
    """2026-10-16: a relay that installs a unit must re-baseline the stack-refresh tripwire."""
    relay = 'install -m 644 .config/systemd/user/x.timer "$HOME/.config/systemd/user/x.timer"\nscripts/sign-manifest.sh'
    assert any("audit baselines" in why for why in lint(relay))
    assert not any("audit baselines" in why for why in lint(relay + "\nREBASELINE=1 bash scripts/stack-refresh.sh --tripwire --dry-run --now"))


def test_demo_quadlet_never_gets_production_access(tmp_path, monkeypatch):
    """2026-10-16 (demo/production database split): a relay line that hands a demo quadlet the
    production socket volume or dispatch.env is a finding, and installing a tracked demo quadlet
    that carries them is one too (dispatch.env only once the quadlet declares DISPATCH_ROLE=demo)."""
    spec = importlib.util.spec_from_file_location("relay_lint", SCRIPT)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    bad_line = ("echo 'Volume=corporatetraveldc-pgsql-sock:/var/run/postgresql' >> "
                ".config/containers/systemd/corporatetraveldc-demo-api.container")
    assert any("production access" in why for _, why, _ in m.lint("bash <<'RELAY'\nset -euo pipefail\n" + bad_line + "\nRELAY\n"))
    bad_env = ("sed -i 's/^X/EnvironmentFile=\\/etc\\/corporatetraveldc\\/dispatch.env/' "
               ".config/containers/systemd/corporatetraveldc-runner-demo.container")
    assert any("production access" in why for _, why, _ in m.lint("bash <<'RELAY'\nset -euo pipefail\n" + bad_env + "\nRELAY\n"))

    qd = tmp_path / "quadlets"; qd.mkdir()
    monkeypatch.setattr(m, "QUADLET_DIR", qd)
    install = ("install -m 644 .config/containers/systemd/corporatetraveldc-demo-api.container "
               "\"$HOME/.config/containers/systemd/corporatetraveldc-demo-api.container\"\n"
               "REBASELINE=1 bash scripts/stack-refresh.sh --tripwire --dry-run --now")
    relay = "bash <<'RELAY'\nset -euo pipefail\n" + install + "\nRELAY\n"
    (qd / "corporatetraveldc-demo-api.container").write_text(
        "[Container]\nEnvironment=DISPATCH_ROLE=demo\nVolume=corporatetraveldc-pgsql-sock:/var/run/postgresql\n")
    assert any("carries production access" in why for _, why, _ in m.lint(relay))
    (qd / "corporatetraveldc-demo-api.container").write_text(
        "[Container]\nEnvironment=DISPATCH_ROLE=demo\nEnvironmentFile=/etc/corporatetraveldc/dispatch.env\n")
    assert any("carries production access" in why for _, why, _ in m.lint(relay))
    # relay-1 shape: no demo role yet, dispatch.env still legitimately loaded, no socket
    (qd / "corporatetraveldc-demo-api.container").write_text(
        "[Container]\nEnvironmentFile=/etc/corporatetraveldc/dispatch.env\nVolume=corporatetraveldc-pgsql-demo-sock:/var/run/postgresql-demo\n")
    assert not any("production access" in why for _, why, _ in m.lint(relay))


def test_podman_without_devnull():
    assert any("podman reads stdin" in w for w in lint("podman healthcheck run x >/dev/null 2>&1"))
    assert not any("podman reads stdin" in w for w in lint("podman healthcheck run x >/dev/null 2>&1 </dev/null"))


def test_completion_sentinel():
    spec = importlib.util.spec_from_file_location("relay_lint", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    bad = "bash <<'RELAY'\nset -euo pipefail\ntrap 'echo finished OK' EXIT\necho hi\nRELAY\n"
    whys = [w for _, w, _ in m.lint(bad)]
    assert any("RELAY_DONE=1" in w for w in whys) and any("trap reports OK" in w for w in whys)


def test_heavy_step_needs_load_wait():
    assert any("load wait" in w for w in lint("PYTHONPATH=src python3 -m pytest -q tests"))
    assert not any("load wait" in w for w in lint("scripts/relay-wait-load.sh\nPYTHONPATH=src python3 -m pytest -q tests"))
    assert any("load wait" in w for w in lint("bash scripts/serialized-rollout.sh"))


def test_pg_maintenance_builds_need_load_wait():
    # 2026-10-18: CONCURRENTLY index builds read whole tables; `status` is read-only.
    assert any("load wait" in w for w in lint("bash scripts/pg-maintenance.sh create-indexes-concurrently"))
    assert any("load wait" in w for w in lint("bash scripts/pg-maintenance.sh reindex-flight-events-updated-at"))
    assert not any("load wait" in w for w in lint(
        "bash scripts/relay-wait-load.sh\nbash scripts/pg-maintenance.sh create-indexes-concurrently idx_nas_programs_active"))
    assert not any("load wait" in w for w in lint("bash scripts/pg-maintenance.sh status"))


def test_relay_wait_load_script(tmp_path):
    import subprocess
    f = tmp_path / "loadavg"
    f.write_text("12.0 10.0 9.0 1/1 1\n")
    env = {"PATH": "/usr/bin:/bin", "LOADAVG_FILE": str(f), "RELAY_LOAD_POLL_S": "0", "RELAY_LOAD_WAIT_MAX_S": "0"}
    script = SCRIPT.parent / "relay-wait-load.sh"
    assert subprocess.run(["bash", str(script)], env=env).returncode == 0
    f.write_text("31.0 30.0 29.0 1/1 1\n")
    assert subprocess.run(["bash", str(script)], env=env, capture_output=True).returncode == 1
