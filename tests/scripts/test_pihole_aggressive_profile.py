"""2026-10-24: config/pihole/profiles/aggressive-encrypted-dns/apply.sh --
the OPT-IN encrypted-DNS/ECH profile's installer plans correctly and changes
nothing in dry-run. pihole / pihole-FTL / sqlite3 / systemctl / dig are PATH
stubs that log every invocation; the root check is bypassed with
PIHOLE_PROFILE_TEST_ALLOW_NONROOT=1, which the script honours only together
with --dry-run.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "config/pihole/profiles/aggressive-encrypted-dns"
APPLY = PROFILE / "apply.sh"
URL = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/doh.txt"
COMMENT = "Encrypted DNS bypass (DoH/DoT/DoQ) -- aggressive profile"

STUB = """#!/usr/bin/env bash
printf '%s\\n' "{name} $*" >> "$STUB_LOG"
{body}
"""
BODIES = {
    "pihole": "exit 0",
    "pihole-FTL": 'if [[ "${1:-}" == --config && $# -eq 2 ]]; then printf "%s\\n" "${STUB_FTL_LINES:-[]}"; fi; exit 0',
    "sqlite3": 'if [[ "${2:-}" == SELECT* ]]; then printf "%s\\n" "${STUB_SQLITE_COUNT:-0}"; fi; exit 0',
    "systemctl": "exit 0",
    "dig": 'case " $* " in *" HTTPS "*) ;; *) echo 0.0.0.0 ;; esac; exit 0',
}


@pytest.fixture
def stubs(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in BODIES.items():
        p = bindir / name
        p.write_text(STUB.format(name=name, body=body))
        p.chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("")

    def run(*args, env_extra=None, allow_nonroot=True):
        env = dict(os.environ)
        env.update({
            "PATH": f"{bindir}:{env['PATH']}",
            "STUB_LOG": str(log),
            "PIHOLE_GRAVITY_DB": str(tmp_path / "gravity.db"),
            "CTDC_ENV_FILE": str(tmp_path / "dispatch.env"),   # 2026-10-24: hermetic
        })
        env.pop("CTDC_AGGRESSIVE_PRIVACY", None)
        if allow_nonroot:
            env["PIHOLE_PROFILE_TEST_ALLOW_NONROOT"] = "1"
        else:
            env.pop("PIHOLE_PROFILE_TEST_ALLOW_NONROOT", None)
        env.update(env_extra or {})
        return subprocess.run(["bash", str(APPLY), *args], capture_output=True, text=True, env=env, timeout=60)

    return run, log


def calls(log: Path) -> list[str]:
    return [line for line in log.read_text().splitlines() if line]


def test_syntax_and_readme_opt_in_banner():
    assert subprocess.run(["bash", "-n", str(APPLY)], capture_output=True).returncode == 0
    readme = (PROFILE / "README.md").read_text().splitlines()
    assert "**OPT-IN -- a more aggressive measure. Not applied by default.**" in readme[:3]
    assert URL in (PROFILE / "README.md").read_text()


def test_dry_run_plans_insert_and_merge_and_changes_nothing(stubs):
    run, log = stubs
    r = run("--dry-run", env_extra={"STUB_FTL_LINES": "[ log-queries ]", "STUB_SQLITE_COUNT": "0"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "DRY-RUN (apply plan)" in out
    assert f"would run: sqlite3" in out and "INSERT INTO adlist (address, enabled, comment, type)" in out
    assert f"'{URL}'" in out and f"'{COMMENT}'" in out
    assert "would run: pihole -g" in out
    assert 'merged misc.dnsmasq_lines: ["log-queries","filter-rr=HTTPS"]' in out
    assert 'would run: pihole-FTL --config misc.dnsmasq_lines ["log-queries","filter-rr=HTTPS"]' in out
    assert "would run: systemctl restart pihole-FTL" in out
    assert "CHECK dns.google -> 0.0.0.0: PASS" in out and "CHECK cloudflare-ech.com HTTPS -> <empty>: PASS" in out
    executed = calls(log)
    # Only reads ran: one SELECT, one FTL getter, two digs. No write, no gravity, no restart.
    assert [c.split()[0] for c in executed] == ["sqlite3", "pihole-FTL", "dig", "dig"]
    assert all("INSERT" not in c and "DELETE" not in c for c in executed)
    assert not any(c.startswith("pihole ") or c.startswith("systemctl") for c in executed)
    assert not any(c.startswith("pihole-FTL") and c.count("--config") and len(c.split()) > 3 for c in executed)


def test_dry_run_is_a_noop_plan_when_already_applied(stubs):
    run, log = stubs
    r = run(env_extra={"STUB_FTL_LINES": "[ filter-rr=HTTPS ]", "STUB_SQLITE_COUNT": "1"})  # no flag = dry-run
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "adlist already present (1 row) -- no change" in out and "pihole -g not needed" in out
    assert "filter-rr=HTTPS already present -- no change" in out and "pihole-FTL restart not needed" in out
    assert "would run:" not in out and "RESULT: PASS" in out


def test_revert_plan_removes_only_filter_rr_from_a_multi_line_array(stubs):
    run, log = stubs
    r = run("--revert", "--dry-run",
            env_extra={"STUB_FTL_LINES": "[ log-queries, filter-rr=HTTPS, local-ttl=2 ]", "STUB_SQLITE_COUNT": "1"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "DRY-RUN (revert plan)" in out
    assert 'current misc.dnsmasq_lines: ["log-queries","filter-rr=HTTPS","local-ttl=2"]' in out
    assert 'merged misc.dnsmasq_lines: ["log-queries","local-ttl=2"]' in out
    assert "would run: sqlite3" in out and "DELETE FROM adlist WHERE address = '" + URL + "' AND type = 0;" in out
    assert "would run: pihole -g" in out and "would run: systemctl restart pihole-FTL" in out
    assert [c.split()[0] for c in calls(log)] == ["sqlite3", "pihole-FTL", "dig", "dig"]


def test_json_shaped_getter_output_is_parsed_too(stubs):
    run, log = stubs
    r = run("--dry-run", env_extra={"STUB_FTL_LINES": '["dhcp-option=6,10.x.x.x","log-queries"]', "STUB_SQLITE_COUNT": "1"})
    out = r.stdout + r.stderr
    assert 'merged misc.dnsmasq_lines: ["dhcp-option=6,10.x.x.x","log-queries","filter-rr=HTTPS"]' in out, out


@pytest.mark.skipif(os.geteuid() == 0, reason="root check cannot be exercised as root")
def test_non_root_is_refused_except_dry_run_with_the_test_hook(stubs):
    run, log = stubs
    r = run("--dry-run", allow_nonroot=False)
    assert r.returncode == 1 and "must run as root" in r.stderr and calls(log) == []
    r = run("--apply", allow_nonroot=True)      # the hook is NOT honoured for a real apply
    assert r.returncode == 1 and "must run as root" in r.stderr and calls(log) == []
    r = run("--revert", allow_nonroot=True)
    assert r.returncode == 1 and calls(log) == []
    r = run("--bogus")
    assert r.returncode == 2


# --- 2026-10-24: the whole profile needs the deployment setting (or --force-aggressive) to apply -----
# The gate runs after the root check, so these run as root inside an unprivileged user namespace
# (unshare -r); every command the script calls is a stub, so nothing real is touched.

import shutil, pytest

def _as_ns_root(stubs, *args, env_extra=None):
    if not shutil.which("unshare") or subprocess.run(["unshare", "-r", "true"], capture_output=True).returncode != 0:
        pytest.skip("unprivileged user namespaces not permitted here")
    run, log = stubs
    import unittest.mock as m
    real = subprocess.run
    def wrapped(cmd, *a, **k):
        return real(["unshare", "-r", *cmd], *a, **k)
    with m.patch.object(subprocess, "run", wrapped):
        return run(*args, env_extra=env_extra, allow_nonroot=False), log


def test_apply_with_setting_off_only_plans(stubs):
    r, log = _as_ns_root(stubs, "--apply")
    out = r.stdout + r.stderr
    assert "not applying: this deployment has CTDC_AGGRESSIVE_PRIVACY off (opt-in)" in out
    assert not any("INSERT INTO adlist" in c for c in calls(log))


def test_apply_with_setting_on_is_allowed(stubs):
    r, log = _as_ns_root(stubs, "--apply", env_extra={"CTDC_AGGRESSIVE_PRIVACY": "1"})
    assert "not applying" not in (r.stdout + r.stderr)


def test_force_aggressive_overrides_the_setting(stubs):
    r, log = _as_ns_root(stubs, "--apply", "--force-aggressive")
    assert "not applying" not in (r.stdout + r.stderr)
