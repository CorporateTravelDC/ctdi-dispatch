"""2026-10-24: config/exit-node/apply.sh -- the exit-node firewall installer
(POLICY 2 Mbps metered cap for the driver phone's forced Tailscale exit node,
plus the OPT-IN --with-aggressive encrypted-DNS-by-IP drops) plans correctly
and changes nothing in dry-run. nft / tc / curl / systemctl / pihole-FTL /
sqlite3 are PATH stubs that log every invocation; the root check is bypassed
with CTDC_EXITNODE_TEST_ALLOW_NONROOT=1, which the script honours only
together with --dry-run (same pattern as the Pi-hole aggressive profile).
The rendered ruleset is syntax-checked with `nft -c -f` inside an
unprivileged user+net namespace (unshare -rn) when that is permitted.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FOLDER = REPO / "config/exit-node"
APPLY = FOLDER / "apply.sh"
IPLIST_URL = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/ips/doh.txt"
UNIT = "corporatetraveldc-exitnode-fw.service"
NFT_FILE_LIVE = "/etc/nftables.d/ctdc-exitnode.nft"

STUB = """#!/usr/bin/env bash
printf '%s\\n' "{name} $*" >> "$STUB_LOG"
{body}
"""
BODIES = {
    "nft": 'case "$*" in "list table inet ctdc_exitnode") '
           'if [[ "${STUB_NFT_TABLE:-0}" == 1 ]]; then cat "$STUB_NFT_LISTING"; exit 0; fi; exit 1 ;; esac; exit 0',
    "tc": "exit 0",
    "curl": 'out=""; while [[ $# -gt 0 ]]; do [[ $1 == -o ]] && out=$2; shift; done; '
            'printf "%s\\n" "# HaGeZi DoH IPs (stub)" "1.1.1.1" "192.0.2.0/24" "2606:4700:4700::1111" "not-an-address" "9.9.9.9" > "$out"; exit 0',
    "systemctl": "exit 0",
    "pihole-FTL": "exit 0",
    "sqlite3": "exit 0",
}
ROUTE_HDR = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"


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
    etc = tmp_path / "etc"
    (etc / "wants").mkdir(parents=True)
    paths = {
        "nft_file": etc / "ctdc-exitnode.nft",
        "unit_file": etc / UNIT,
        "wants": etc / "wants",
        "render": tmp_path / "rendered.nft",
        "route": tmp_path / "route",
    }
    paths["route"].write_text(ROUTE_HDR + "wld0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\n"
                              "enu1\t00000000\t0100A8C0\t0003\t0\t0\t600\t00000000\n")

    def run(*args, env_extra=None, allow_nonroot=True):
        env = dict(os.environ)
        env.update({
            "PATH": f"{bindir}:{env['PATH']}",
            "STUB_LOG": str(log),
            "CTDC_EXITNODE_NFT_FILE": str(paths["nft_file"]),
            "CTDC_EXITNODE_UNIT_FILE": str(paths["unit_file"]),
            "CTDC_EXITNODE_WANTS_DIR": str(paths["wants"]),
            "CTDC_EXITNODE_RENDER_OUT": str(paths["render"]),
            "CTDC_EXITNODE_PROC_ROUTE": str(paths["route"]),
            # 2026-10-24: hermetic -- never read the box's real dispatch.env for CTDC_AGGRESSIVE_PRIVACY
            "CTDC_ENV_FILE": str(paths["route"].parent / "dispatch.env"),
        })
        env.pop("CTDC_AGGRESSIVE_PRIVACY", None)
        if allow_nonroot:
            env["CTDC_EXITNODE_TEST_ALLOW_NONROOT"] = "1"
        else:
            env.pop("CTDC_EXITNODE_TEST_ALLOW_NONROOT", None)
        env.update(env_extra or {})
        return subprocess.run(["bash", str(APPLY), *args], capture_output=True, text=True, env=env, timeout=120)

    return run, log, paths


def calls(log: Path) -> list[str]:
    return [line for line in log.read_text().splitlines() if line]


def test_syntax_banners_and_cross_links():
    assert subprocess.run(["bash", "-n", str(APPLY)], capture_output=True).returncode == 0
    readme = (FOLDER / "README.md").read_text()
    assert "## POLICY (required when the phone uses this box as its exit node)" in readme
    assert "**OPT-IN -- a more aggressive measure. Not applied by default.**" in readme
    assert IPLIST_URL in readme and NFT_FILE_LIVE in readme
    assert "NOT implemented" in readme and "no-nftset" in readme   # honest write-up, not a fake
    pihole_readme = (REPO / "config/pihole/profiles/aggressive-encrypted-dns/README.md").read_text()
    assert "config/exit-node/" in pihole_readme
    assert (FOLDER / "metered-cap.nft").exists() and (FOLDER / "aggressive/encrypted-dns-ip.nft").exists()
    assert (FOLDER / UNIT).exists()


def test_default_dry_run_plans_the_cap_only_and_changes_nothing(stubs):
    run, log, paths = stubs
    r = run()                                   # no flag = dry-run, cap only
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "DRY-RUN (apply plan)" in out and "aggressive" not in out.split("== exit-node firewall")[1].splitlines()[0]
    assert "cap: 250 kB/s = 2 Mbps total" in out
    rendered = paths["render"].read_text()
    # enu1-only forward cap, both directions, one shared limit object; never wld0.
    assert 'iifname "tailscale0" oifname "enu1" limit name "lim_exitnode_metered" counter drop' in rendered
    assert 'iifname "enu1" oifname "tailscale0" limit name "lim_exitnode_metered" counter drop' in rendered
    assert "rate over 250 kbytes/second burst 256 kbytes" in rendered
    assert "type filter hook forward" in rendered and "hook input" not in rendered and "hook output" not in rendered
    assert "wld0" not in rendered
    assert rendered.startswith("# /etc/nftables.d/ctdc-exitnode.nft")
    assert "table inet ctdc_exitnode\ndelete table inet ctdc_exitnode\ntable inet ctdc_exitnode {" in rendered
    # No aggressive content without the flag.
    for needle in ("doh_ip4", "dport 853", "ctdc-doh-ip", "forward_aggressive"):
        assert needle not in rendered and needle not in out
    # The plan targets the pinned live paths through the unit and the load command.
    assert f"ExecStart={paths['render'].parent / 'bin' / 'nft'} -f {NFT_FILE_LIVE}" in out
    assert f"would run: {paths['render'].parent / 'bin' / 'nft'} -f {NFT_FILE_LIVE}" in out
    assert "ConditionPathExists=" + NFT_FILE_LIVE in out
    assert f"would run: install -m 0644 -o root -g root" in out
    assert f"would run: systemctl enable --now {UNIT}" in out and "would run: systemctl daemon-reload" in out
    # Only one read ran: the table-presence check. No curl, tc, systemctl, pihole-FTL, sqlite3, nft -f.
    assert calls(log) == ["nft list table inet ctdc_exitnode"]
    assert not paths["nft_file"].exists() and not paths["unit_file"].exists()
    assert "RESULT: FAIL (dry-run -- this is the current state, not an error; nothing was changed)" in out


def test_with_aggressive_adds_the_drops_downloads_the_list_and_prints_the_count(stubs):
    run, log, paths = stubs
    r = run("--dry-run", "--with-aggressive")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "+ aggressive (OPT-IN)" in out
    assert f"HaGeZi DoH/DoT resolver IPs: 3 IPv4, 1 IPv6 (skipped 1 non-address lines) from {IPLIST_URL}" in out
    rendered = paths["render"].read_text()
    assert "wld0" not in rendered
    assert 'iifname "tailscale0" meta l4proto { tcp, udp } th dport 853 jump ctdc_doh_drop' in rendered
    assert 'iifname "tailscale0" udp dport 8853 jump ctdc_doh_drop' in rendered
    assert "ip saddr $ctdc_tailnet_peers4 meta l4proto { tcp, udp } th dport 443 ip daddr @doh_ip4 jump ctdc_doh_drop" in rendered
    assert "ip6 saddr $ctdc_tailnet_peers6 meta l4proto { tcp, udp } th dport 443 ip6 daddr @doh_ip6 jump ctdc_doh_drop" in rendered
    assert "define ctdc_tailnet_peers4 = 100.64.0.0/10" in rendered        # range, never a host
    assert 'log prefix "ctdc-doh-ip " level info' in rendered and "limit rate 10/minute burst 20 packets" in rendered
    assert "flags interval" in rendered and "auto-merge" in rendered
    assert re.search(r"set doh_ip4 \{.*?elements = \{\s*1\.1\.1\.1,\s*192\.0\.2\.0/24,\s*9\.9\.9\.9\s*\}", rendered, re.S)
    assert re.search(r"set doh_ip6 \{.*?elements = \{\s*2606:4700:4700::1111\s*\}", rendered, re.S)
    assert "not-an-address" not in rendered
    assert "ctdc-nodns" not in rendered                                   # no-DNS detection is NOT faked
    assert "... (3 entries elided)" in out
    # Reads only: the download and the table-presence check.
    executed = calls(log)
    assert [c.split()[0] for c in executed] == ["curl", "nft"]
    assert IPLIST_URL in executed[0] and "--max-time" in executed[0]
    assert not paths["nft_file"].exists() and not paths["unit_file"].exists()


def test_metered_uplink_blocks_the_download_and_reuses_the_cached_set(stubs):
    run, log, paths = stubs
    paths["route"].write_text(ROUTE_HDR + "wld0\t00000000\t0100A8C0\t0003\t0\t0\t600\t00000000\n"
                              "enu1\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\n")   # enu1 wins (lower metric)
    r = run("--with-aggressive")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "uplink is the METERED interface (enu1): NOT downloading the IP list" in out
    assert "no cached DoH IP list" in out
    assert calls(log) == []                                                # no curl at all
    # With a previously installed file the embedded elements are reused.
    paths["nft_file"].write_text(
        "table inet ctdc_exitnode {\n    set doh_ip4 {\n        type ipv4_addr\n        elements = {\n"
        "            198.51.100.4,\n            203.0.113.0/24\n        }\n    }\n    set doh_ip6 {\n        type ipv6_addr\n"
        "        # (no entries)\n    }\n}\n")
    log.write_text("")
    r = run("--with-aggressive")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "reusing cached DoH IP set" in out and "2 IPv4, 0 IPv6" in out
    assert re.search(r"elements = \{\s*198\.51\.100\.4,\s*203\.0\.113\.0/24\s*\}", paths["render"].read_text())
    assert not any(c.startswith("curl") for c in calls(log))


def test_apply_plan_is_idempotent_when_files_match(stubs):
    run, log, paths = stubs
    r = run()
    assert r.returncode == 0, r.stdout + r.stderr
    shutil.copy(paths["render"], paths["nft_file"])
    block = r.stdout.split(f"   --- rendered {UNIT} ---\n")[1].split("   --- end ---")[0]
    unit_text = "".join(l[5:] + "\n" for l in block.splitlines())
    paths["unit_file"].write_text(unit_text)
    (paths["wants"] / UNIT).symlink_to(paths["unit_file"])
    listing = paths["render"].read_text()
    (paths["render"].parent / "listing.txt").write_text(listing)
    log.write_text("")
    r = run(env_extra={"STUB_NFT_TABLE": "1", "STUB_NFT_LISTING": str(paths["render"].parent / "listing.txt")})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert f"{paths['nft_file']} unchanged" in out and f"{paths['unit_file']} unchanged" in out
    assert "table already loaded and file unchanged -- no reload" in out and f"{UNIT} already enabled" in out
    assert "would run:" not in out
    assert "live table inet ctdc_exitnode: 1 chains, 2 rules, doh_ip4 0 entries, doh_ip6 0 entries" in out
    assert "RESULT: PASS" in out


def test_revert_plan_removes_only_our_table_unit_and_file(stubs):
    run, log, paths = stubs
    r = run("--dry-run", "--with-aggressive")
    assert r.returncode == 0
    shutil.copy(paths["render"], paths["nft_file"])
    paths["unit_file"].write_text("[Unit]\n")
    (paths["wants"] / UNIT).symlink_to(paths["unit_file"])
    log.write_text("")
    r = run("--revert", "--dry-run", env_extra={"STUB_NFT_TABLE": "1", "STUB_NFT_LISTING": str(paths["nft_file"])})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "DRY-RUN (revert plan)" in out
    planned = [l.strip()[len("would run: "):] for l in out.splitlines() if l.strip().startswith("would run: ")]
    nft_bin = str(paths["render"].parent / "bin" / "nft")
    assert planned == [
        f"systemctl disable --now {UNIT}",
        f"{nft_bin} delete table inet ctdc_exitnode",
        f"rm -f {paths['unit_file']}",
        "systemctl daemon-reload",
        f"rm -f {paths['nft_file']}",
    ]
    assert "firewalld" not in " ".join(planned) and "ts-" not in " ".join(planned) and "flush" not in " ".join(planned)
    assert "live table inet ctdc_exitnode: 3 chains, 8 rules, doh_ip4 3 entries, doh_ip6 1 entries" in out
    assert calls(log) == ["nft list table inet ctdc_exitnode"] * 2        # presence check + verify, both reads
    assert paths["nft_file"].exists() and paths["unit_file"].exists()     # dry-run removed nothing
    assert "RESULT: FAIL (dry-run" in out                                  # still installed = expected in a plan


def test_knob_validation_refuses_a_bad_interface_and_primary_leak(stubs):
    run, log, paths = stubs
    r = run(env_extra={"CTDC_METERED_IF": "enu1; nft flush ruleset"})
    assert r.returncode == 1 and "bad interface name" in r.stderr and calls(log) == []
    r = run(env_extra={"CTDC_METERED_IF": "wld0"})
    assert r.returncode == 1 and "must differ" in r.stderr and calls(log) == []
    r = run(env_extra={"CTDC_TAILNET_PEERS4": "100.64.0.1"})       # a single host, not a range (2026-10-24: RFC 5737/allow-listed fixtures so the public scrub passes)
    assert r.returncode == 1 and "IPv4 CIDR expected" in r.stderr


def _netns_nft_check_available() -> str | None:
    """Return None when `unshare -rn nft -c -f` can work here, else the skip reason."""
    if shutil.which("nft") is None:
        return "nft not on PATH"
    if shutil.which("unshare") is None:
        return "unshare not on PATH"
    probe = subprocess.run(["unshare", "-rn", "nft", "list", "ruleset"], capture_output=True, text=True, timeout=30)
    if probe.returncode != 0:
        return f"unprivileged user+net namespace not permitted here: {probe.stderr.strip()[:120]}"
    return None


def test_rendered_ruleset_passes_nft_check_in_an_unprivileged_netns(stubs, tmp_path):
    reason = _netns_nft_check_available()
    if reason:
        pytest.skip(f"nft -c syntax check skipped: {reason}")
    run, log, paths = stubs
    for flags in ((), ("--with-aggressive",)):
        r = run("--dry-run", *flags)
        assert r.returncode == 0, r.stdout + r.stderr
        target = tmp_path / ("ruleset-" + ("aggr" if flags else "cap") + ".nft")
        shutil.copy(paths["render"], target)
        chk = subprocess.run(["unshare", "-rn", "nft", "-c", "-f", str(target)], capture_output=True, text=True, timeout=60)
        assert chk.returncode == 0, f"{flags}: nft -c rejected the rendered ruleset:\n{chk.stderr}\n{target.read_text()}"


@pytest.mark.skipif(os.geteuid() == 0, reason="root check cannot be exercised as root")
def test_non_root_is_refused_except_dry_run_with_the_test_hook(stubs):
    run, log, paths = stubs
    r = run("--dry-run", allow_nonroot=False)
    assert r.returncode == 1 and "must run as root" in r.stderr and calls(log) == []
    r = run("--apply", allow_nonroot=True)      # the hook is NOT honoured for a real apply
    assert r.returncode == 1 and "must run as root" in r.stderr and calls(log) == []
    r = run("--apply", "--with-aggressive", allow_nonroot=True)
    assert r.returncode == 1 and calls(log) == []
    r = run("--revert", allow_nonroot=True)
    assert r.returncode == 1 and calls(log) == []
    r = run("--bogus")
    assert r.returncode == 2


# --- 2026-10-24: deployment default (CTDC_AGGRESSIVE_PRIVACY) and the explicit overrides -----------

def test_deployment_setting_on_includes_aggressive_by_default(stubs):
    run, log, paths = stubs
    (paths["route"].parent / "dispatch.env").write_text("CTDC_AGGRESSIVE_PRIVACY=1\n")
    out = run().stdout
    assert "aggressive encrypted-DNS IP rules: INCLUDED (deployment setting CTDC_AGGRESSIVE_PRIVACY=1)" in out
    assert "ctdc-doh-ip " in paths["render"].read_text()


def test_without_aggressive_overrides_the_deployment_setting(stubs):
    run, log, paths = stubs
    (paths["route"].parent / "dispatch.env").write_text("CTDC_AGGRESSIVE_PRIVACY=1\n")
    out = run("--without-aggressive").stdout
    assert "not included (--without-aggressive)" in out
    assert "ctdc-doh-ip " not in paths["render"].read_text()


def test_public_default_is_opt_in(stubs):
    run, log, paths = stubs
    out = run().stdout                       # no env file at all = public default
    assert "not included (deployment setting CTDC_AGGRESSIVE_PRIVACY=0)" in out
