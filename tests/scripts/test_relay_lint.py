"""2026-10-15: relay-lint catches every relay bug that bit on 2026-10-14/15 (standing rule: bug once, lint forever)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/relay-lint.py"


def lint(body: str, staged=()):
    spec = importlib.util.spec_from_file_location("relay_lint", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # 2026-10-23: staged= stands in for the guard's staged paths (hermetic; no git call)
    return [why for _, why, _ in m.lint("bash <<'RELAY'\nset -euo pipefail\n" + body + "\nRELAY_DONE=1\nRELAY\n",
                                        staged=list(staged))]


def test_clean_relay_passes():
    assert lint("cd /opt/x\nsystemctl --user list-timers --all --no-pager x.timer || true\n"
                "read -rs -p 'URL: ' U </dev/tty\nscripts/push-public.sh </dev/null") == []


def test_sigpipe_head():
    assert any("SIGPIPE" in w for w in lint("systemctl --user list-timers --no-pager x.timer | head -2"))


def test_read_without_tty():
    assert any("reads the relay text" in w for w in lint('read -rs -p "URL: " HCURL; echo'))


def test_sudo_on_user_unit():
    assert any("user unit" in w for w in lint("sudo systemctl disable --now corporatetraveldc-net-failover-watchdog.timer"))
    assert not lint("sudo systemctl restart tailscaled </dev/null")


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
    good = ("install -m 644 .config/containers/systemd/x.container \"$HOME/.config/containers/systemd/x.container\"\n"
            "scripts/relay-wait-load.sh\nscripts/sign-manifest.sh; git add -A"
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


def test_tree_wide_steps_need_load_wait():
    # 2026-10-20 (cleanup sweep): steps that read every transcript or hash / render the whole tree.
    for step in ("python3 scripts/leak-guard/transcript_leak_scan.py scan --require-ladd",
                 "scripts/with-dispatch-env.sh python3 scripts/leak-guard/transcript_leak_scan.py redact",
                 "scripts/sign-manifest.sh", "bash scripts/verify-manifest.sh",
                 "python3 scripts/scrub-public-tree.py --verify-only HEAD^{tree}",
                 "python3 scripts/render-glossary.py --check", "bash scripts/render-onboarding.sh",
                 "python3 scripts/demo-sqlite-retire-check.py --hours 24"):
        assert any("load wait" in w for w in lint(step)), step
        assert not any("load wait" in w for w in lint("scripts/relay-wait-load.sh\n" + step)), step
        assert not any("load wait" in w for w in lint("scripts/relay-wait-load.sh && " + step)), step
    # naming the scanner without scan / redact is not a heavy step
    assert not any("load wait" in w for w in lint("ls -l scripts/leak-guard/transcript_leak_scan.py"))


def test_gate_backfill_runs_need_load_wait():
    # 2026-10-20 (phase D): a real backfill is heavy; a dry run or the G-prefix report is not.
    for step in ("scripts/with-dispatch-env.sh python3 scripts/gate-backfill-tdes.py",
                 "scripts/with-dispatch-env.sh python3 scripts/gate-backfill-airframes.py --hours 72",
                 "systemd-run --user --unit=corporatetraveldc-gate-backfill-tdes --nice=19 --collect "
                 "scripts/with-dispatch-env.sh python3 scripts/gate-backfill-tdes.py"):
        assert any("load wait" in w for w in lint(step)), step
        assert not any("load wait" in w for w in lint("scripts/relay-wait-load.sh\n" + step)), step
    for step in ("scripts/with-dispatch-env.sh python3 scripts/gate-backfill-tdes.py --dry-run",
                 "scripts/with-dispatch-env.sh python3 scripts/gate-backfill-tdes.py --g-prefix-report",
                 "scripts/with-dispatch-env.sh python3 scripts/gate-backfill-airframes.py --dry-run --hours 72"):
        assert not any("load wait" in w for w in lint(step)), step


def test_relay_wait_load_script(tmp_path):
    import subprocess
    f = tmp_path / "loadavg"
    f.write_text("12.0 10.0 9.0 1/1 1\n")
    env = {"PATH": "/usr/bin:/bin", "LOADAVG_FILE": str(f), "RELAY_LOAD_POLL_S": "0", "RELAY_LOAD_WAIT_MAX_S": "0"}
    script = SCRIPT.parent / "relay-wait-load.sh"
    assert subprocess.run(["bash", str(script)], env=env).returncode == 0
    f.write_text("31.0 30.0 29.0 1/1 1\n")
    assert subprocess.run(["bash", str(script)], env=env, capture_output=True).returncode == 1


# 2026-10-19 (operator, Gate-1b) -------------------------------------------------------------

def test_leak_guard_relay_must_redact_before_its_first_push():
    redact = "python3 scripts/leak-guard/transcript_leak_scan.py redact"
    why = "push before `transcript_leak_scan.py redact`"
    assert any(why in w for w in lint("git add -A scripts/leak-guard/\ngit push origin main"))
    assert any(why in w for w in lint(f"git push origin main\n{redact}"))                 # redact after push
    assert any(why in w for w in lint("git commit -F - <<'MSG'\nTranscript leak scan learns LADD\nMSG\n"
                                      "scripts/push-public.sh </dev/null"))
    assert not any(why in w for w in lint(f"{redact}\ngit push origin main\nscripts/push-public.sh </dev/null"))
    assert not any(why in w for w in lint("git push origin main"))                       # no leak-scan change
    assert not any(why in w for w in lint("# leak-guard untouched\ngit push origin main"))   # comments don't count


def test_secret_read_needs_a_control_character_check_before_use():
    why = "without a control-character check"
    assert any(why in w for w in lint("read -rs -p 'Key: ' K </dev/tty\nprintf '%s' \"$K\" > \"$HOME/.secrets/x.key\""))
    assert any(why in w for w in lint("read -r -p 'Token: ' API_TOKEN </dev/tty\nprintf '%s\\n' \"${API_TOKEN}\" > f"))
    assert not any(why in w for w in lint("source scripts/lib/secret_input.sh\nread -rs -p 'Key: ' K </dev/tty\n"
                                          "secret_check K\nprintf '%s' \"$K\" > f"))
    assert not any(why in w for w in lint("read -rs -p 'Key: ' K </dev/tty; secret_check K\nprintf '%s' \"$K\" > f"))
    assert not any(why in w for w in lint("source scripts/lib/secret_input.sh\nsecret_read K 'Key: '\nprintf '%s' \"$K\" > f"))
    assert not any(why in w for w in lint("read -r -p 'Token: ' TOKEN </dev/tty\n"
                                          "[[ \"$TOKEN\" == *[[:cntrl:]]* ]] && exit 1\nprintf '%s' \"$TOKEN\" > f"))
    assert not any(why in w for w in lint("read -r -p 'Name: ' NAME </dev/tty\necho \"$NAME\""))   # not a secret
    assert not any(why in w for w in lint("read -rs -p 'URL: ' U </dev/tty"))                      # never used


def test_secret_input_helper_refuses_control_characters(tmp_path):
    import subprocess
    helper = SCRIPT.parent / "lib/secret_input.sh"
    tty = tmp_path / "tty"
    script = tmp_path / "t.sh"
    script.write_text(
        f"source {helper}\n"
        "for v in 'fake-ok-value' $'a\\x1bb' $'a\\rb' $'a\\tb' $'a\\x7fb' $'a\\x01b'; do\n"
        "  X=\"$v\"; if secret_check X 2>/dev/null; then echo pass; else echo \"refused ${#X}\"; fi\n"
        "done\n"
        f"SECRET_INPUT_TTY={tty} secret_read Y 'p: ' 2>/dev/null && echo \"read ${{#Y}}\" || echo \"read-refused ${{#Y}}\"\n")
    tty.write_bytes(b"fake-ok-value\n")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    assert r.stdout.split("\n")[:6] == ["pass"] + ["refused 0"] * 5 and "read 13" in r.stdout
    tty.write_bytes(b"fake\x1b[31mvalue\n")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    assert "read-refused 0" in r.stdout and "fake" not in r.stdout + r.stderr


# 2026-10-20 (Pi-hole read relay) ------------------------------------------------------------

def test_sqlite_sql_with_double_quoted_literals_is_flagged():
    why = "double-quoted text inside SQL passed to sqlite3"
    bad = 'echo "n: $(pihole-FTL sqlite3 -ni /etc/pihole/pihole-FTL.db "SELECT count(*) FROM queries WHERE timestamp > strftime(\\"%s\\",\\"now\\")" 2>&1)"'
    good = "echo \"n: $(pihole-FTL sqlite3 -ni /etc/pihole/pihole-FTL.db \"SELECT count(*) FROM queries WHERE timestamp > strftime('%s','now','-1 day')\" 2>&1)\""
    assert any(why in w for w in lint(bad))
    assert not any(why in w for w in lint(good))


# 2026-10-22 (operator): the 2026-10-21 hotfix relay hand-built images without receipts ---------

def test_bare_podman_build_needs_a_provenance_receipt():
    why = "without a later `provenance.py record`"
    build = "podman build -t localhost/corporatetraveldc-api:latest -f Containerfile . </dev/null"
    record = ("python3 scripts/build/provenance.py record --image localhost/corporatetraveldc-api:latest "
              "--containerfile Containerfile --context .")
    assert any(why in w for w in lint(build))
    assert not any(why in w for w in lint(build + "\n" + record))
    assert any(why in w for w in lint(record + "\n" + build))                    # record must come after
    other = record.replace("corporatetraveldc-api", "corporatetraveldc-web")
    assert any(why in w for w in lint(build + "\n" + other))                     # receipt for another image
    assert not any(why in w for w in lint("scripts/relay-wait-load.sh\nbash scripts/serialized-rollout.sh"))
    assert not any(why in w for w in lint("echo 'podman build is done by the rollout'"))


# 2026-10-23 (operator): sudo stdin, OK ping gated on its checks, hard-coded --on-calendar date,
# hot-reloaded data applied without a hold.

def test_sudo_must_redirect_stdin():
    # 2026-10-22: `sudo bash -c '...'` without </dev/null ate the rest of the relay
    assert any("sudo reads stdin" in w for w in lint("sudo bash -c 'cd /opt/x && bash scripts/install-root-copies.sh'"))
    assert any("sudo reads stdin" in w for w in lint('x="$(sudo -u pihole pihole-FTL --config k)"'))
    assert any("sudo reads stdin" in w for w in lint("true && sudo systemctl daemon-reload"))
    assert not lint("sudo bash -c 'cd /opt/x && bash scripts/install-root-copies.sh' </dev/null")
    assert not lint("sudo bash -s <<'ROOT'\nsystemctl daemon-reload\nROOT")
    assert not lint("printf 'x\\n' | sudo tee /etc/x >/dev/null")
    assert not lint("sudo -v")
    assert not lint('echo "== 1b. sudo (prompt 1 of 7)"\n# sudo bash -c without redirect, in a comment')


TRAP_OK = ("trap 'rc=$?; if [[ $rc == 0 && $RELAY_DONE == 1 ]]; then nudge \"X finished OK\" \"y\"; "
           "else nudge \"X FAILED rc=$rc\" \"y\"; fi' EXIT\n")


def test_ok_ping_must_be_gated_on_final_checks():
    bad = (TRAP_OK + "echo \"provenance FAILED images: $(scripts/build/provenance.py verify --running </dev/null "
           "2>&1 | grep -cE '^FAILED' || true) (expect 0)\"\n"
           "echo \"integrity sweep: $(systemctl --user show -p Result --value x.service)\"\necho done")
    assert sum("not enforced" in w for w in lint(bad)) == 2
    good = (TRAP_OK + "pf=$(scripts/build/provenance.py verify --running </dev/null 2>&1 | grep -cE '^FAILED' || true)\n"
            "echo \"provenance FAILED images: $pf (expect 0)\"\n"
            "[[ $pf == 0 ]] || { echo 'provenance check failed'; exit 1; }\n"
            "r=$(systemctl --user show -p Result --value x.service); echo \"integrity sweep: $r\"; "
            "[[ $r == success ]] || exit 1")
    assert not any("not enforced" in w for w in lint(good))
    # no OK-nudge trap: printing a result is just output
    assert not any("not enforced" in w for w in lint("echo \"integrity sweep: $(cat /tmp/x)\""))


def test_hardcoded_on_calendar_date():
    assert any("hard-coded date" in w for w in lint(
        'systemd-run --user --unit u --on-calendar "2026-10-22 23:50:00 America/New_York" /bin/true </dev/null'))
    assert any("hard-coded date" in w for w in lint("systemd-run --user --on-calendar=2026-10-22T23:50 /bin/true"))
    assert not any("hard-coded date" in w for w in lint(
        'systemd-run --user --unit u --on-calendar "$(TZ=America/New_York date -d \'23:50\' \'+%Y-%m-%d %H:%M:00\') '
        'America/New_York" /bin/true'))
    assert not any("hard-coded date" in w for w in lint('systemd-run --user --on-calendar "*-*-* 23:50:00" /bin/true'))


APPLY = 'git -C "$G" diff --cached --binary | git apply --index\n'
HOLD = "touch /var/lib/corporatetraveldc/hold-hot-reload\n"
ROLL = "systemctl --user restart corporatetraveldc-poller.service\n"
RELEASE = "rm -f /var/lib/corporatetraveldc/hold-hot-reload\n"


def test_hot_reloaded_data_needs_a_hold_around_the_rollout():
    staged = ["src/poller/main.py", "watchlists/permanent_flights.json"]
    assert sum("hot-reload" in w for w in lint(APPLY + ROLL, staged)) == 2
    assert any("does not hold the reload" in w for w in lint(APPLY + HOLD + ROLL + RELEASE, staged))  # hold too late
    assert any("never released" in w for w in lint(HOLD + APPLY + RELEASE + ROLL, staged))           # released early
    assert not any("hot-reload" in w for w in lint(HOLD + APPLY + ROLL + RELEASE, staged))
    # batch without a hot-reloaded file, or no guard apply: no rule
    assert not any("hot-reload" in w for w in lint(APPLY + ROLL, ["src/poller/main.py"]))
    assert not any("hot-reload" in w for w in lint(ROLL, staged))
