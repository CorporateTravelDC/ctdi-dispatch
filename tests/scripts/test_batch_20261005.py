"""
tests/scripts/test_batch_20261005.py -- contract tests for the 2026-10-05 batch:
argv-token sweep, NTS cert refresh as a root installed unit, the five
day-keyed optime migrations, per-service scoped env for every first-party
container, the LLAMA_BASE_URL rename, and plan.sh --rename-account.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (REPO / rel).read_text()


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


# -- argv sweep ----------------------------------------------------------------

def test_no_secret_header_or_password_in_argv():
    bad = []
    for p in sorted((REPO / "scripts").rglob("*.sh")):
        code = _code(p.read_text())
        for i, ln in enumerate(code.splitlines(), 1):
            if re.search(r'-H\s+"(Authorization: Bearer|X-Board-Key:)\s*\$', ln):
                bad.append(f"{p.relative_to(REPO)}:{i}: {ln.strip()[:90]}")
            if re.search(r'=\(-H "Authorization: Bearer', ln):
                bad.append(f"{p.relative_to(REPO)}:{i}: {ln.strip()[:90]}")
            if re.search(r'-e PGPASSWORD=', ln):
                bad.append(f"{p.relative_to(REPO)}:{i}: {ln.strip()[:90]}")
    assert not bad, "secret on a command line:\n" + "\n".join(bad)


def test_authhdr_keeps_the_token_out_of_argv(tmp_path):
    script = tmp_path / "t.sh"
    script.write_text(
        'authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; '
        'exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }\n'
        'a=(); authhdr a "S3CRET"; printf "%s\\n" "${a[@]}"; cat "${a[1]#@}"\n')
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0] == "-H" and out[1].startswith("@/dev/fd/") and "S3CRET" not in out[1]
    assert out[2] == "Authorization: Bearer S3CRET"


# -- NTS cert refresh ---------------------------------------------------------------

def test_nts_refresh_is_a_root_installed_unit_without_sudo():
    code = _code(read("scripts/tailscale-cert-refresh-nts.sh"))
    assert "sudo" not in code and "EUID == 0" in code
    assert "/var/lib/corporatetraveldc" not in code
    inst = read("scripts/install-root-copies.sh")
    assert "scripts/tailscale-cert-refresh-nts.sh" in inst
    assert "systemd/system/corporatetraveldc-nts-cert-refresh.timer" in inst
    assert "tailscale-cert-refresh-nts)" in inst            # ExecStart rewrite to libexec
    assert not (REPO / ".config/systemd/user/corporatetraveldc-nts-cert-refresh.service").exists()


# -- optime ------------------------------------------------------------------------

def test_day_keyed_skills_use_the_operational_day():
    for f in ("second_brain_weekly", "aam_weekly_watch", "dispatch_desk_memo",
              "second_brain_demo_archiver_daily", "executive_protection_daily_watch"):
        s = read(f"src/poller/skills/{f}.py")
        assert "today = optime.op_today()" in s and "date.today()" not in _code(s), f
    assert "migrate next" not in read("tests/common/naive_date_allowlist.txt")


# -- scoped env ------------------------------------------------------------------------

def test_no_first_party_container_mounts_the_full_secrets_file():
    full = [p.name for p in (REPO / ".config/containers/systemd").glob("*.container")
            if "\nEnvironmentFile=/etc/corporatetraveldc/dispatch-secrets.env" in p.read_text()]
    assert full == []
    svc = {re.search(r"^EnvironmentFile=/etc/corporatetraveldc/svc/(\S+)\.env", p.read_text(), re.M).group(1)
           for p in (REPO / ".config/containers/systemd").glob("*.container")
           if "/etc/corporatetraveldc/svc/" in p.read_text()}
    for s in svc:
        assert (REPO / f"scripts/service-env/{s}.allowlist").exists(), s


@pytest.mark.skipif(not os.access("/etc/corporatetraveldc/dispatch-secrets.env", os.R_OK),
                    reason="needs the secret NAMES (operator box)")
def test_allowlists_cover_the_import_closure():
    r = subprocess.run([sys.executable, str(REPO / "scripts/service-env/scan.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


# -- LLAMA_BASE_URL --------------------------------------------------------------------

def test_llama_pool_derives_from_llama_base_url(monkeypatch):
    sys.path.insert(0, str(REPO / "src"))
    import importlib
    monkeypatch.delenv("LLAMA_POOL_HOST", raising=False)
    monkeypatch.delenv("LLAMA_PORT", raising=False)
    monkeypatch.setenv("LLAMA_BASE_URL", "http://10.x.x.x:9999")
    import common.llama_pool as lp
    lp = importlib.reload(lp)
    assert (lp.HOST, lp.LLAMA_PORT) == ("10.x.x.x", 9999)
    monkeypatch.delenv("LLAMA_BASE_URL")
    lp = importlib.reload(lp)
    assert (lp.HOST, lp.LLAMA_PORT) == ("100.x.x.x", 8093)


def test_no_code_reads_only_the_old_name():
    for p in list((REPO / "src").rglob("*.py")):
        for ln in p.read_text().splitlines():
            if 'getenv("OLLAMA_BASE_URL"' in ln or 'environ.get("OLLAMA_BASE_URL"' in ln:
                assert "LLAMA_BASE_URL" in ln, f"{p}: {ln.strip()}"


# -- rename ---------------------------------------------------------------------------

def test_rename_account_dry_run_carries_every_name_keyed_piece():
    out = subprocess.run(["bash", str(REPO / "scripts/agent-segmentation/plan.sh"), "--dry-run",
                          "--rename-account", "ctdc-agent-x", "ctdc-agent-vendor-x"],
                         capture_output=True, text=True)
    if out.returncode == 5 or "no such account" in out.stderr:
        pytest.skip("dry-run does not check account existence; nothing to assert")
    o = out.stdout
    for needle in ("usermod -l ctdc-agent-vendor-x ctdc-agent-x", "groupmod -n ctdc-agent-vendor-x ctdc-agent-x",
                   "usermod -d /home/ctdc-agent-vendor-x -m", "ctdc-agent-vendor-x_ed25519",
                   "/etc/ctdc-accounts.conf", "/etc/ctdc-skill-grants.conf", "sudoers.d/50-ctdc-agent-x",
                   "(CTDC-AGENT-VENDOR-X) Corporate Travel Dispatch Remote Control",
                   "board-signer-ctl.sh rename ctdc-agent-x ctdc-agent-vendor-x",
                   "'s#\"/home/ctdc-agent-x\"#\"/home/ctdc-agent-vendor-x\"#g'"):
        assert needle in o, needle


def test_rename_refuses_bad_or_missing_target():
    r = subprocess.run(["bash", str(REPO / "scripts/agent-segmentation/plan.sh"), "--dry-run",
                        "--rename-account", "ctdc-agent-x"], capture_output=True, text=True)
    assert r.returncode == 2 and "needs FROM and TO" in r.stderr


def test_signer_rename_on_sqlite(tmp_path):
    k = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "zz-a@corporatetraveldc-dispatch",
                    "-f", str(k)], check=True)
    env = {**os.environ, "DISPATCH_DB_BACKEND": "sqlite", "DISPATCH_DB": str(tmp_path / "t.db")}
    ctl = str(REPO / "scripts/board-signer-ctl.sh")
    subprocess.run([ctl, "register", "zz-a", str(k) + ".pub", "--kind", "agent"], env=env, check=True, capture_output=True)
    subprocess.run([ctl, "rename", "zz-a", "zz-b"], env=env, check=True, capture_output=True)
    show = subprocess.run([ctl, "show", "zz-b"], env=env, capture_output=True, text=True).stdout
    assert show.startswith("active") and "zz-b@corporatetraveldc-dispatch" in show


def test_liveness_locked_means_a_deliberate_lock_only():
    """2026-10-05: a never-set password ('!!', the useradd default for every
    key-only team account) is not a lock; usermod -L ('!' + real hash) is."""
    src = read("scripts/team-liveness.sh")
    fn = re.search(r"^locked\(\)\s*\{.*?fi; \}", src, re.S | re.M).group(0)
    for field, want in (("!!", 1), ("!", 1), ("*", 1), ("!*", 1), ("$6$salt$hash", 1), ("!$6$salt$hash", 0)):
        script = f'FAKE=""\ngetent() {{ printf "u:%s:1:0:99999:7:::\\n" \'{field}\'; }}\n{fn}\nlocked u'
        rc = subprocess.run(["bash", "-c", script], capture_output=True).returncode
        assert rc == want, f"{field!r}: rc={rc}"


def test_token_action_scope():
    sys.path.insert(0, str(REPO / "src"))
    from auth.auth import action_allowed
    assert action_allowed(None, "admin.osint.scope.create")            # pre-0070 tokens unchanged
    scope = "admin.alert.push,admin.healthz,admin.triggers.list,watchlist.*"
    assert action_allowed(scope, "watchlist.flight.add") and action_allowed(scope, "admin.healthz")
    for a in ("vault.remember", "admin.approval_request.create", "osint.scope.create", "admin.bandwidth_priority.set"):
        assert not action_allowed(scope, a), a


def test_convert_to_service_dry_run():
    out = subprocess.run(["bash", str(REPO / "scripts/agent-segmentation/plan.sh"), "--dry-run",
                          "--convert-to-service", "ctdc-agent-x"], capture_output=True, text=True, check=True).stdout
    for needle in ("usermod -s /usr/sbin/nologin ctdc-agent-x", "rm -f /home/ctdc-agent-x/.ssh/authorized_keys",
                   "'ctdc-agent-x' 'service' 'none' '0'", "loginctl disable-linger ctdc-agent-x",
                   "board-mint-nonce.py --label ctdc-agent-x"):
        assert needle in out, needle


def test_dispatch_access_log_keeps_the_real_client():
    fmt = read("nginx/conf.d/00-log-format-cfreal.conf")
    assert "log_format cfreal" in fmt and "$http_cf_connecting_ip" in fmt and '"$http_user_agent"' in fmt
    assert "dispatch-access.log cfreal;" in read("nginx/conf.d/dispatch.example.com.conf")
