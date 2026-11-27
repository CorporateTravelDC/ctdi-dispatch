"""tests/scripts/test_dns_lock.py -- 2026-11-27: the DNS lock (config/dns-lock/). Pi-hole -> Unbound is the only DNS
path; Unbound forwards only this tailnet's MagicDNS names; the tailnet suffix never sits in a tracked file."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
D = REPO / "config/dns-lock"
NFT = (D / "ctdc-dnslock.nft").read_text()
APPLY = (D / "apply.sh").read_text()
UB = (D / "60-tailscale-magicdns.conf.in").read_text()


def rules():
    return [l.strip() for l in NFT.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_only_loopback_and_unbound_reach_dns_before_the_verdict():
    r = rules()
    out = r.index("chain out {")
    lo = r.index('oifname "lo" accept')
    ub = next(i for i, l in enumerate(r) if 'meta skuid "unbound"' in l)
    verdict = next(i for i, l in enumerate(r) if 'counter name "dns_bypass" @VERDICT@' in l)
    assert out < lo < ub < verdict
    assert "{ 53, 853 } accept" in r[ub]
    # nothing else is accepted on 53/853 in the out chain
    for l in r[out:verdict]:
        if "53" in l and "accept" in l:
            assert l in (r[lo], r[ub])


def test_bypass_is_logged_with_the_uid_and_rate_limited():
    assert re.search(r'limit rate \d+/minute log prefix "ctdc-dns-bypass " flags skuid', NFT)


def test_tailnet_queries_are_redirected_to_the_local_pihole():
    assert re.search(r'iifname "tailscale0" meta l4proto \{ tcp, udp \} th dport 53 .*redirect to :53', NFT)
    assert "type nat hook prerouting priority dstnat" in NFT


def test_own_table_reloads_idempotently_and_never_flushes_others():
    assert "table inet ctdc_dnslock\ndelete table inet ctdc_dnslock\n" in NFT
    assert "flush ruleset" not in NFT and "ctdc_exitnode" not in NFT.split("never touches")[1]


def test_verdict_is_rendered_and_checked_before_install():
    assert 's/@VERDICT@/${verdict}/' in APPLY
    i_check, i_install = APPLY.index('"$NFT" -c -f "$tmp"'), APPLY.index('install -m 0644 -o root -g root "$tmp" "$NFT_FILE"')
    assert i_check < i_install
    assert "--audit)   install_table accept" in APPLY and "--enforce) install_table reject" in APPLY


def test_unbound_forwards_only_this_tailnet_and_no_suffix_is_tracked():
    assert 'name: "@TAILNET_SUFFIX@."' in UB
    assert 'name: "ts.net."' not in UB
    for p in D.iterdir():
        assert not re.search(r"\btail[0-9a-f]{6}\.ts\.net\b", p.read_text()), p.name
    assert "unbound-checkconf" in APPLY and "restored, Unbound not restarted" in APPLY


def test_suffix_helper(tmp_path):
    st = {"MagicDNSSuffix": "tailq1a2b3.ts.net", "Self": {"DNSName": "box.tailq1a2b3.ts.net."}}
    f = tmp_path / "st.json"; f.write_text(json.dumps(st))
    run = lambda *a: subprocess.run([sys.executable, str(D / "tailnet_suffix.py"), str(f), *a], capture_output=True, text=True)
    assert run().stdout.strip() == "tailq1a2b3.ts.net"
    assert run("--self").stdout.strip() == "box.tailq1a2b3.ts.net"
    f.write_text(json.dumps({"Self": {}}))
    assert run().returncode == 1
