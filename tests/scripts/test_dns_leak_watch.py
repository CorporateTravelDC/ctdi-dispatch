"""tests/scripts/test_dns_leak_watch.py -- 2026-11-27: DNS leak watch and its collector (config/dns-leak-watch/).
Synthetic fixtures only (documentation ranges 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32)."""
from __future__ import annotations

import grp
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
D = REPO / "config/dns-leak-watch"
spec = importlib.util.spec_from_file_location("dns_leak_watch", D / "dns_leak_watch.py")
dl = importlib.util.module_from_spec(spec); spec.loader.exec_module(dl)
GROUP = grp.getgrgid(os.getgid()).gr_name
NOW = 1_795_000_000.0
ME, OTHER = "192.0.2.10", "192.0.2.99"


def conf(tmp_path, **kw):
    base = {"CLIENTS": f"{ME},2001:db8::10", "IFACE": "tailscale0", "PIHOLE_LOG": str(tmp_path / "pihole.log"),
            "OUT_DIR": str(tmp_path / "out"), "READ_GROUP": GROUP, "RDNS": "no",
            "RESOLVER_LIST": str(tmp_path / "resolvers.txt"), "RESOLVER_MIN": "2", "DNSLOCK_COUNTER": ""}
    base.update(kw)
    return dl.parse_conf("\n".join(f"{k}={v}" for k, v in base.items()))


# ---- config and rendering --------------------------------------------------------------------------------------
def test_example_config_parses_and_renders():
    c = dl.parse_conf((D / "dnsleak.conf.example").read_text())
    nft = dl.render_nft(c)
    assert "table inet ctdc_dnsleak\ndelete table inet ctdc_dnsleak\n" in nft
    assert 'iifname "tailscale0" ct state new meta l4proto { tcp, udp } jump record_new' in nft
    assert "elements = { 192.0.2.10/32 }" in nft and "update @seen4 { ip saddr . ip daddr . meta l4proto . th dport }" in nft
    assert "ip saddr @quar4 ip daddr != 100.64.0.0/10 counter drop" in nft and "ip6 saddr @quar6" in nft
    assert nft.index("jump record_new") < nft.index("ip saddr @quar4")      # attempts are recorded before the drop
    for ctr in ("c_new", "c_dns53", "c_dot", "c_quic", "c_tls"):
        assert f'counter name "{ctr}"' in nft
    assert c["DNSLOCK_COUNTER"] == "inet ctdc_dnslock dns_tailnet_redirect" and c["RESOLVER_MIN"] == 100


@pytest.mark.parametrize("bad", [
    {"CLIENTS": ""}, {"CLIENTS": "not-an-ip"}, {"CLIENTS": "10.x.x.x/8"}, {"IFACE": "eth0; flush ruleset"},
    {"PIHOLE_LOG": "relative"}, {"OUT_DIR": "/var/../etc"}, {"RESOLVER_LIST": "list.txt"}, {"READ_GROUP": "Bad Group"},
    {"SET_TIMEOUT": "forever"}, {"INDEX_HOURS": "0"}, {"RDNS": "maybe"}, {"RESOLVER_LIST_URL": "http://x/y"},
    {"RESOLVER_MIN": "0"}, {"METERED_IFACES": "enu1;reboot"}, {"DNSLOCK_COUNTER": "inet x"}, {"NTFY_URL": "ftp://x"},
    {"NTFY_TOPIC": "a b"}, {"NTFY_TOKEN_FILE": "token"},
])
def test_bad_values_are_refused(tmp_path, bad):
    with pytest.raises(dl.ConfError):
        conf(tmp_path, **bad)


def test_unknown_key_and_non_kv_lines_are_refused():
    with pytest.raises(dl.ConfError):
        dl.parse_conf("CLIENTS=192.0.2.1\nIFACE=lan0\nEXTRA=1")
    with pytest.raises(dl.ConfError):
        dl.parse_conf("CLIENTS=192.0.2.1\nIFACE=lan0\n$(reboot)")


# ---- classification tiers --------------------------------------------------------------------------------------
def idx_with(lines, t):
    idx = dl.Index(); idx.feed(lines, t); return idx




def mine_fn():
    import ipaddress
    return dl._mine_fn([ipaddress.ip_network(ME), ipaddress.ip_network("2001:db8::10")])


def test_tiers_hardcoded_other_client_cached_stale_lookup():
    idx = dl.Index()
    idx.feed([f"x query[A] mine.example.test from {ME}", "x reply mine.example.test is 198.51.100.1"], NOW)
    idx.feed([f"x query[A] theirs.example.test from {OTHER}", "x reply theirs.example.test is 198.51.100.2"], NOW)
    idx.feed([f"x query[A] old.example.test from {ME}", "x reply old.example.test is 198.51.100.3"], NOW - 72 * 3600)
    m = mine_fn()
    assert idx.classify("198.51.100.1", m, NOW, 48) == ("lookup", "mine.example.test")
    assert idx.classify("198.51.100.2", m, NOW, 48) == ("other-client", "theirs.example.test")
    assert idx.classify("198.51.100.3", m, NOW, 48) == ("cached-stale", "old.example.test")
    assert idx.classify("203.0.113.50", m, NOW, 48) == ("hardcoded", "")


def test_old_answer_only_to_others_is_hardcoded():
    idx = dl.Index()
    idx.feed([f"x query[A] x.example.test from {OTHER}", "x reply x.example.test is 198.51.100.4"], NOW - 72 * 3600)
    assert idx.classify("198.51.100.4", mine_fn(), NOW, 48) == ("hardcoded", "")


def test_bypass_kinds_win_over_tiers():
    import ipaddress
    res = [ipaddress.ip_network("203.0.113.53/32")]
    idx, m = dl.Index(), mine_fn()
    assert dl.classify_destination("203.0.113.9", "udp", 53, idx, m, res, NOW, 48)[0] == "dns53"
    assert dl.classify_destination("203.0.113.9", "tcp", 853, idx, m, res, NOW, 48)[0] == "dot"
    assert dl.classify_destination("203.0.113.9", "udp", 853, idx, m, res, NOW, 48)[0] == "doq"
    assert dl.classify_destination("203.0.113.53", "tcp", 443, idx, m, res, NOW, 48)[0] == "doh"
    assert dl.classify_destination("203.0.113.54", "tcp", 443, idx, m, res, NOW, 48)[0] == "hardcoded"
    assert dl.classify_destination("203.0.113.53", "tcp", 443, idx, m, None, NOW, 48)[0] == "hardcoded"   # no list loaded


# ---- the run: once-only, outputs, redirect delta, silent baseline ---------------------------------------------
PIHOLE = f"""x query[A] api.example.test from {ME}
x reply api.example.test is 198.51.100.7
x query[A] shared.example.test from {OTHER}
x reply shared.example.test is 198.51.100.8
"""


def test_run_outputs_and_once_only(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(dl, "push", lambda c, t, m, p=3, tp="": sent.append(t))
    (tmp_path / "pihole.log").write_text(PIHOLE)
    (tmp_path / "resolvers.txt").write_text("203.0.113.53\n203.0.113.54\n")
    c = conf(tmp_path, NTFY_URL="http://127.0.0.1:2586", NTFY_TOPIC="ops-health", DNSLOCK_COUNTER="inet ctdc_dnslock dns_tailnet_redirect")
    el1 = [("198.51.100.7", "tcp", 443, 2), ("203.0.113.9", "tcp", 443, 1)]
    r1 = dl.run(c, now=NOW, elements=el1, counters={"dnslock_redirect": 10})
    assert r1["first_run"] and r1["hardcoded"] == 1 and sent == []                     # silent baseline
    el2 = el1 + [("198.51.100.8", "tcp", 443, 1), ("203.0.113.20", "udp", 443, 3), ("203.0.113.53", "tcp", 443, 1),
                 ("203.0.113.60", "udp", 853, 1)]
    r2 = dl.run(c, now=NOW + 180, elements=el2, counters={"dnslock_redirect": 14})
    assert r2["new"] == 4 and r2["hardcoded"] == 1 and r2["bypass_evidence"] == 2
    assert sent == ["dns-leak-watch: 1 new hard-coded destination(s)"]                  # bypass is not pushed here
    r3 = dl.run(c, now=NOW + 360, elements=el2, counters={"dnslock_redirect": 15})
    assert r3["new"] == 0 and len(sent) == 1
    j = json.loads((tmp_path / "out/latest.json").read_text())
    assert j["schema"] == 1 and j["tool"] == "dns-leak-watch"
    assert {f["dest"] for f in j["hardcoded"]["findings"]} == {"203.0.113.9", "203.0.113.20"}
    assert {(f["dest"], f["class"]) for f in j["bypass_evidence"]["findings"]} == {("203.0.113.53", "doh"), ("203.0.113.60", "doq")}
    assert j["bypass_evidence"]["dnslock_redirect_total"] == 5 and j["bypass_evidence"]["dnslock_redirect_last_delta"] == 1
    assert j["classes"]["other-client"]["destinations"] == 1 and j["classes"]["lookup"]["destinations"] == 1
    s = (tmp_path / "out/summary.txt").read_text()
    assert s.index("== HARD-CODED IP") < s.index("-- context") < s.index("-- normal lookups")
    assert "bypass evidence: 2, reported by DNS anomaly watch" in s
    rows = (tmp_path / "out/2026-11-18.csv").read_text().splitlines()
    assert rows[0].startswith("first_seen_utc,class,") and len(rows) == 1 + 6
    for f in ("summary.txt", "latest.json", "state.json", "2026-11-18.csv"):
        assert oct((tmp_path / "out" / f).stat().st_mode & 0o777) == "0o640"


def test_missing_dnslock_table_is_skipped(tmp_path):
    (tmp_path / "pihole.log").write_text("")
    c = conf(tmp_path)
    dl.run(c, now=NOW, elements=[], counters={"dnslock_redirect": None})
    j = json.loads((tmp_path / "out/latest.json").read_text())
    assert j["bypass_evidence"]["dnslock_redirect_total"] == 0 and j["resolver_list"]["loaded"] is False


def test_index_is_incremental_and_survives_rotation(tmp_path):
    log = tmp_path / "pihole.log"
    log.write_text("x reply a.example.test is 198.51.100.1\n")
    idx = dl.Index()
    dl.update_index(idx, log, 1.0, first=True)
    with open(log, "a") as fh:
        fh.write("x reply b.example.test is 198.51.100.2\n")
    dl.update_index(idx, log, 2.0, first=False)
    log.rename(tmp_path / "pihole.log.1")
    with open(tmp_path / "pihole.log.1", "a") as fh:
        fh.write("x reply c.example.test is 198.51.100.3\n")
    log.write_text("x reply d.example.test is 198.51.100.4\n")
    dl.update_index(idx, log, 3.0, first=False)
    assert set(idx.answers) == {"198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4"}


# ---- resolver refresh ------------------------------------------------------------------------------------------
def test_refresh_updates_refuses_small_keeps_last_good_and_skips_metered(tmp_path):
    c = conf(tmp_path, RESOLVER_MIN="3", METERED_IFACES="enu1")
    dest = tmp_path / "resolvers.txt"
    assert dl.refresh_resolvers(c, fetch=lambda u: b"# list\n203.0.113.1\n203.0.113.2\n2001:db8::53\njunk\n", iface="end0") == 0
    good = dest.read_text()
    assert good.splitlines() == ["203.0.113.1/32", "203.0.113.2/32", "2001:db8::53/128"]
    assert oct(dest.stat().st_mode & 0o777) == "0o644"
    assert dl.refresh_resolvers(c, fetch=lambda u: b"203.0.113.1\n", iface="end0") == 1 and dest.read_text() == good
    def boom(u): raise OSError("offline")
    assert dl.refresh_resolvers(c, fetch=boom, iface="end0") == 1 and dest.read_text() == good
    called = []
    assert dl.refresh_resolvers(c, fetch=lambda u: called.append(u) or b"", iface="enu1") == 0 and called == []


# ---- public-mirror safety and install rules --------------------------------------------------------------------
REAL = re.compile(r"\b100\.(?!64\.0\.0\b)(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b|\btail[0-9a-f]{6}\.ts\.net\b|corporatetraveldc-dispatch")


def test_no_real_tailnet_addresses_or_names_in_tracked_files():
    for p in [f for f in D.iterdir() if f.is_file()] + [REPO / "docs/DNS_LEAK_WATCH.md"]:
        assert not REAL.search(p.read_text()), p.name


def test_root_runs_only_the_installed_copy():
    apply = (D / "apply.sh").read_text()
    assert 'python3 "$HERE/' not in apply and apply.count('python3 "$LIB/dns_leak_watch.py"') >= 3
    assert apply.index('nft -c -f "$tmp"') < apply.index('install -m 0644 -o root -g root "$tmp" "$NFT_FILE"')
    for u in ("ctdc-dnsleak-watch.service", "ctdc-dnsleak-resolvers.service"):
        s = (D / u).read_text()
        assert "/usr/local/libexec/ctdc-dnsleak/dns_leak_watch.py" in s and "Nice=19" in s and "IOSchedulingClass=idle" in s


def test_only_the_resolver_unit_fetches_and_timers_have_no_self_requires():
    assert "refresh-resolvers" in (D / "ctdc-dnsleak-resolvers.service").read_text()
    assert "refresh-resolvers" not in (D / "ctdc-dnsleak-watch.service").read_text()
    src = (D / "dns_leak_watch.py").read_text()
    run_body = src[src.index("def run("):src.index("def default_iface(")]
    assert "urlopen" not in run_body and "refresh_resolvers" not in run_body
    r = (D / "ctdc-dnsleak-resolvers.timer").read_text()
    assert "OnCalendar=daily" in r and "RandomizedDelaySec=" in r
    for t in ("ctdc-dnsleak-watch.timer", "ctdc-dnsleak-resolvers.timer"):
        assert not re.search(r"^(Requires|Wants)=", (D / t).read_text(), re.M)


# ---- 2026-11-27 refinements: proxy clients, shared-address, diagnose ------------------------------------------
def test_proxy_clients_count_as_the_device(tmp_path):
    import ipaddress
    idx = dl.Index()
    idx.feed(["x query[A] api.example.test from 127.0.0.1", "x reply api.example.test is 198.51.100.7"], NOW)
    plain = dl._mine_fn([ipaddress.ip_network(ME)])
    proxied = dl._mine_fn([ipaddress.ip_network(ME)], [ipaddress.ip_network("127.0.0.1")])
    assert idx.classify("198.51.100.7", plain, NOW, 48)[0] == "other-client"
    assert idx.classify("198.51.100.7", proxied, NOW, 48)[0] == "lookup"
    assert conf(tmp_path, PROXY_CLIENTS="127.0.0.1,::1")["PROXY_CLIENTS"][1].version == 6
    with pytest.raises(dl.ConfError):
        conf(tmp_path, PROXY_CLIENTS="box")


def test_shared_address_marks_cdn_other_client_and_never_alerts(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(dl, "push", lambda c, t, m, p=3, tp="": sent.append(t))
    (tmp_path / "pihole.log").write_text(f"x query[A] img.example.test from {OTHER}\nx reply d111.cloudfront.net is 198.51.100.9\n"
                                         f"x query[A] own.example.test from {OTHER}\nx reply own.example.test is 198.51.100.10\n")
    c = conf(tmp_path, NTFY_URL="http://127.0.0.1:2586", NTFY_TOPIC="ops-health")
    dl.run(c, now=NOW, elements=[], counters={})
    dl.run(c, now=NOW + 180, elements=[("198.51.100.9", "tcp", 443, 1), ("198.51.100.10", "tcp", 443, 1)], counters={})
    st = json.loads((tmp_path / "out/state.json").read_text())
    assert st["seen"]["198.51.100.9|tcp|443"]["shared"] is True and st["seen"]["198.51.100.10|tcp|443"]["shared"] is False
    assert sent == []                                                   # other-client never alerts
    assert "shared-address" in (tmp_path / "out/summary.txt").read_text()
    assert json.loads((tmp_path / "out/latest.json").read_text())["shared_address"] == 1
    assert dl.is_shared("", "server-1-2-3-4.iad89.r.cloudfront.net") and not dl.is_shared("notcloudfront.net")


def test_diagnose_counts_kinds_without_addresses(tmp_path):
    c = conf(tmp_path, PROXY_CLIENTS="198.51.100.200")
    lines = [f"x query[A] a.example.test from {ME}", "x query[A] b.example.test from 127.0.0.1",
             "x query[A] c.example.test from 198.51.100.200", "x query[A] d.example.test from 192.0.2.250",
             "x query[A] e.example.test from 192.0.2.1"]
    got = dl.diagnose_counts(lines, c, {"192.0.2.1"})
    assert got == {"watched device (CLIENTS)": 1, "proxy (PROXY_CLIENTS)": 1, "this host (loopback / own address)": 2, "other": 1}


# ---- per-device CONNECTION CHECK (synthetic tailscale status; documentation addresses inside the tailnet ranges) ----
PH, PH6 = "100.64.0.11", "fd7a:115c:a1e0::11"          # synthetic peers (RFC 6598 range start; not a real tailnet)
LAP, TAB = "100.64.0.12", "100.64.0.13"
BOX = "100.64.0.1"


def ts_raw(online=("phone", "laptop", "tablet")):
    mk = lambda h, os_, ips: {"HostName": h, "OS": os_, "Online": h in online, "TailscaleIPs": ips}
    return {"Self": {"HostName": "box", "TailscaleIPs": [BOX]},
            "Peer": {"a": mk("phone", "android", [PH, PH6]), "b": mk("laptop", "linux", [LAP]), "c": mk("tablet", "android", [TAB])}}


def tconf(tmp_path, **kw):
    base = {"CLIENTS": "", "TAILNET": "yes", "PROXY_CLIENTS": "127.0.0.1", "CHECK_WINDOW_MIN": "10"}
    base.update(kw)
    return conf(tmp_path, **base)


class Nft:
    def __init__(self): self.ops = []
    def __call__(self, op, addrs): self.ops.append((op, tuple(addrs))); return True


def drive(tmp_path, c, passes, nft, online=("phone", "laptop", "tablet"), log_lines=None):
    """passes: list of (minutes, elements, pihole lines appended before the pass)."""
    log = tmp_path / "pihole.log"; log.write_text("")
    sent = []
    import types
    for m, el, lines in passes:
        with open(log, "a") as fh:
            fh.write("".join(l + "\n" for l in lines))
        dl.push = lambda c_, t_, m_, p_=3, tp_="": sent.append(t_)
        dl.run(c, now=NOW + m * 60, elements=el, counters={}, ts_raw=ts_raw(online), nft=nft, local={BOX, "127.0.0.1"})
    return sent, json.loads((tmp_path / "out/state.json").read_text())


def test_tailnet_ranges_are_the_only_wide_clients_allowed(tmp_path):
    c = tconf(tmp_path)
    assert [str(n) for n in c["CLIENTS"]] == ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]
    with pytest.raises(dl.ConfError):
        conf(tmp_path, CLIENTS="100.0.0.0/8")
    with pytest.raises(dl.ConfError):
        conf(tmp_path, QUARANTINE="always")
    with pytest.raises(dl.ConfError):
        conf(tmp_path, DEVICE_SSH_CHECK="yes", DEVICE_SSH_USER="root")
    assert tconf(tmp_path, QUARANTINE_EXEMPT="laptop,work-pc")["QUARANTINE_EXEMPT"] == ["laptop", "work-pc"]


def test_verdicts_pass_fail_nolookup_fail_bypass_and_not_routed(tmp_path, monkeypatch):
    c = tconf(tmp_path)
    nft = Nft()
    base = [(PH, "198.51.100.7", "tcp", 443, 1), (LAP, "203.0.113.70", "tcp", 443, 1), (LAP, "203.0.113.9", "udp", 853, 1)]
    passes = [(0, base, []),                                                         # baseline
              (3, [(PH, "198.51.100.7", "tcp", 443, 3), (LAP, "203.0.113.70", "tcp", 443, 3), (LAP, "203.0.113.9", "udp", 853, 2)],
               [f"x query[A] a.example.test from {PH}", "x reply a.example.test is 198.51.100.7"]),
              (14, [(PH, "198.51.100.7", "tcp", 443, 5), (LAP, "203.0.113.70", "tcp", 443, 5), (LAP, "203.0.113.9", "udp", 853, 3)],
               [f"x query[A] a.example.test from {PH}"])]
    sent, st = drive(tmp_path, c, passes, nft)
    d = st["devices"]
    assert d["phone"]["verdict"] == "PASS"
    assert d["laptop"]["verdict"] == "FAIL" and "bypass" in d["laptop"]["reason"]
    assert d["tablet"]["verdict"] == "NOT-ROUTED"
    assert "dns-leak-watch: laptop FAIL" in sent and "dns-leak-watch: tablet NOT-ROUTED" in sent
    assert not any("phone PASS" in s for s in sent)                                   # a first PASS is not pushed
    assert not any("QUARANTINED" in s for s in sent)                                 # QUARANTINE=no by default
    j = json.loads((tmp_path / "out/latest.json").read_text())
    assert j["devices"]["laptop"]["verdict"] == "FAIL" and PH not in json.dumps(j["devices"])
    assert PH not in (tmp_path / "out/state.json").read_text()                       # addresses only in root-state
    assert PH in (tmp_path / "out/root-state.json").read_text()
    assert oct((tmp_path / "out/root-state.json").stat().st_mode & 0o777) == "0o600"
    assert "== CONNECTION CHECK" in (tmp_path / "out/summary.txt").read_text()


def test_traffic_without_any_lookup_fails(tmp_path):
    c = tconf(tmp_path)
    passes = [(0, [(PH, "198.51.100.7", "tcp", 443, 1)], []),
              (3, [(PH, "198.51.100.7", "tcp", 443, 4)], []),
              (14, [(PH, "198.51.100.7", "tcp", 443, 9)], [])]
    _, st = drive(tmp_path, c, passes, Nft())
    assert st["devices"]["phone"]["verdict"] == "FAIL" and "no Pi-hole lookups" in st["devices"]["phone"]["reason"]


def test_proxy_lookups_count_while_the_device_is_active(tmp_path):
    c = tconf(tmp_path)
    passes = [(0, [(PH, "198.51.100.7", "tcp", 443, 1)], []),
              (3, [(PH, "198.51.100.7", "tcp", 443, 4)], ["x query[A] a.example.test from 127.0.0.1"]),
              (14, [(PH, "198.51.100.7", "tcp", 443, 9)], [])]
    _, st = drive(tmp_path, c, passes, Nft())
    assert st["devices"]["phone"]["verdict"] == "PASS"


def _fail_passes(n_windows, src=TAB):
    passes, cnt = [(0, [(src, "198.51.100.7", "tcp", 443, 1)], [])], 1
    for i in range(n_windows * 2):
        cnt += 2
        passes.append((3 + i * 6, [(src, "198.51.100.7", "tcp", 443, cnt)], []))
    return passes


def test_quarantine_after_grace_then_release_on_pass(tmp_path):
    c = tconf(tmp_path, QUARANTINE="alert-then-quarantine", QUARANTINE_GRACE_MIN="5")
    nft = Nft()
    sent, st = drive(tmp_path, c, _fail_passes(2), nft)
    assert st["devices"]["tablet"]["quarantined"] is True
    assert ("add", (TAB,)) in nft.ops and "dns-leak-watch: QUARANTINED tablet" in sent
    # it recovers: traffic with lookups for a full window
    log = tmp_path / "pihole.log"
    dl.push = lambda *a, **k: sent.append(a[1])
    for m, cnt in ((40, 30), (52, 40)):
        with open(log, "a") as fh:
            fh.write(f"x query[A] a.example.test from {TAB}\n")
        dl.run(c, now=NOW + m * 60, elements=[(TAB, "198.51.100.7", "tcp", 443, cnt)], counters={}, ts_raw=ts_raw(),
               nft=nft, local={BOX})
    st = json.loads((tmp_path / "out/state.json").read_text())
    assert st["devices"]["tablet"]["quarantined"] is False and ("delete", (TAB,)) in nft.ops
    assert "dns-leak-watch: released tablet (auto)" in sent


def test_never_quarantines_this_host_proxies_or_exempt(tmp_path):
    for kw, src, host in (({"QUARANTINE_EXEMPT": "tablet"}, TAB, "tablet"), ({"PROXY_CLIENTS": f"127.0.0.1,{TAB}"}, TAB, "tablet")):
        sub = tmp_path / host / str(len(kw)) / list(kw)[0]
        sub.mkdir(parents=True)
        c = tconf(sub, QUARANTINE="alert-then-quarantine", QUARANTINE_GRACE_MIN="0", **kw) if "PROXY_CLIENTS" not in kw else \
            conf(sub, CLIENTS="", TAILNET="yes", CHECK_WINDOW_MIN="10", QUARANTINE="alert-then-quarantine", QUARANTINE_GRACE_MIN="0", **kw)
        nft = Nft()
        _, st = drive(sub, c, _fail_passes(3, src), nft)
        assert st["devices"][host]["exempt"] is True and not st["devices"][host].get("quarantined")
        assert not any(op == "add" for op, _ in nft.ops)
    assert dl.protected("box2", [BOX], tconf(tmp_path), {BOX}, local=set())        # this host's own tailnet address


def test_manual_release_one_and_all(tmp_path, monkeypatch):
    c = tconf(tmp_path, QUARANTINE="alert-then-quarantine", QUARANTINE_GRACE_MIN="5")
    nft = Nft()
    drive(tmp_path, c, _fail_passes(2), nft)
    assert dl.release(c, "phone", nft=nft) == 1                                       # not quarantined: refused
    assert dl.release(c, "tablet", nft=nft) == 0 and ("delete", (TAB,)) in nft.ops
    st = json.loads((tmp_path / "out/state.json").read_text())
    assert st["devices"]["tablet"]["quarantined"] is False and "fail_since" not in st["devices"]["tablet"]
    assert dl.release(c, "--all", nft=nft) == 0


def test_device_check_keeps_only_pass_fail_lines(tmp_path):
    c = tconf(tmp_path, DEVICE_SSH_CHECK="yes", DEVICE_SSH_USER="tester")
    seen = []
    def runner(cmd, script):
        seen.append(cmd)
        assert script.startswith("#!/usr/bin/env bash")
        return "header\n  PASS  exit node active      ok\n  FAIL  tracker blocked   no\nsecret-ish other line\n"
    lines = dl.device_check(c, "laptop", D / "dns-lockdown-check.sh", runner)
    assert lines == ["PASS  exit node active      ok", "FAIL  tracker blocked   no"]
    assert seen[0][:3] == ["timeout", "60", "tailscale"] and seen[0][4] == "tester@laptop"
    calls = []
    passes = [(0, [], []), (3, [(LAP, "198.51.100.7", "tcp", 443, 2)], [])]
    log = tmp_path / "pihole.log"; log.write_text("")
    for m, el, _ in passes:
        dl.run(c, now=NOW + m * 60, elements=el, counters={}, ts_raw=ts_raw(), nft=Nft(), local={BOX},
               checker=lambda h: calls.append(h) or ["PASS  x"])
    assert calls == ["laptop"]                                                        # Linux peers only, once


def test_vendored_device_script_is_unchanged():
    import hashlib
    assert hashlib.sha256((D / "dns-lockdown-check.sh").read_bytes()).hexdigest() == \
        "706d47940b6f80b72810884d0ad68f9f0e0370144168d77c178b26dfd555fceb"


# ---- OFF-TUNNEL episodes, the captive-portal hold and its releases ------------------------------------------------
def captive_run(tmp_path, c, script, approver=None):
    """script: list of (minute, phone_online, phone_cnt or None, lookups). Returns (pushes, final state)."""
    log = tmp_path / "pihole.log"; log.write_text("")
    pushes, nft = [], Nft()
    dl.push = lambda c_, t_, m_, p_=3, tp_="": pushes.append((t_, p_, tp_))
    for m, online, cnt, lookups in script:
        if lookups:
            with open(log, "a") as fh:
                fh.write(f"x query[A] a.example.test from {PH}\n")
        el = [(PH, "198.51.100.7", "tcp", 443, cnt)] if cnt is not None else []
        dl.run(c, now=NOW + m * 60, elements=el, counters={}, ts_raw=ts_raw(("phone",) if online else ()),
               nft=nft, local={BOX}, approver=approver)
    return pushes, json.loads((tmp_path / "out/state.json").read_text()), nft


OFFLINE_5 = [(0, True, 1, False), (3, True, 3, True), (6, False, None, False), (9, False, None, False),
             (12, False, None, False), (15, False, None, False), (18, True, 6, True)]


def cconf(tmp_path, **kw):
    base = {"ALWAYS_ON_HOSTS": "phone", "NTFY_P5_TOPICS": "hot-alerts"}
    base.update(kw)
    return tconf(tmp_path, **base)


def test_offline_episode_alerts_p5_once_then_holds_on_return(tmp_path):
    pushes, st, nft = captive_run(tmp_path, cconf(tmp_path), OFFLINE_5)
    p5 = [p for p in pushes if "off the tunnel" in p[0]]
    assert len(p5) == 1 and p5[0][1] == 5 and p5[0][2] == "hot-alerts" and "captive portal?" in p5[0][0]
    held = [p for p in pushes if p[0].startswith("dns-leak-watch: HELD phone")]
    assert len(held) == 1 and held[0][1] == 5
    assert st["devices"]["phone"]["captive_quarantine"] is True and ("add", (PH, PH6)) in nft.ops
    assert PH not in (tmp_path / "out/state.json").read_text()


def test_online_but_not_routed_starts_an_episode_too(tmp_path):
    script = [(0, True, 1, False), (3, True, 3, True), (6, True, 3, False), (9, True, 3, False), (12, True, 3, False)]
    pushes, st, _ = captive_run(tmp_path, cconf(tmp_path), script)
    assert any("off the tunnel" in p[0] for p in pushes)
    assert st["devices"]["phone"]["off_tunnel"]["kind"] == "not-routed"


def test_short_blip_no_alert_no_hold(tmp_path):
    script = [(0, True, 1, False), (3, True, 3, True), (6, False, None, False), (8, True, 5, True)]
    pushes, st, nft = captive_run(tmp_path, cconf(tmp_path), script)
    assert not any("off the tunnel" in p[0] or "HELD" in p[0] for p in pushes)
    assert not st["devices"]["phone"].get("captive_quarantine") and not nft.ops


def test_signed_release_lifts_the_hold_at_once(tmp_path):
    calls = []
    def approver(cmd):
        calls.append(cmd)
        return "ok capt0001\n" if any(m >= 0 for m in [0]) and len(calls) > 1 else "none\n"
    pushes, st, nft = captive_run(tmp_path, cconf(tmp_path, RELEASE_APPROVAL_CMD="/usr/local/bin/gate", RELEASE_APPROVAL_USER="op"),
                                  OFFLINE_5 + [(21, True, 8, True)], approver)
    assert st["devices"]["phone"]["captive_quarantine"] is False
    assert st["devices"]["phone"]["last_release"]["how"] == "signed" and ("delete", (PH, PH6)) in nft.ops
    assert any(p[0] == "dns-leak-watch: released phone (signed)" for p in pushes)
    assert calls[0][:5] == ["runuser", "-u", "op", "--", "/usr/local/bin/gate"] and calls[0][5:8] == ["check", "--host", "phone"]


def test_only_an_exact_ok_line_counts_as_signed(tmp_path):
    c = cconf(tmp_path, RELEASE_APPROVAL_CMD="/usr/local/bin/gate", RELEASE_APPROVAL_USER="op")
    for out in ("ok\n", "OK capt1\n", "ok capt1; rm -rf /\n", "allowed\n", ""):
        assert dl.signed_release(c, "phone", NOW, lambda cmd, o=out: o) == ""
    assert dl.signed_release(c, "phone", NOW, lambda cmd: "ok capt-0001\n") == "capt-0001"
    assert dl.signed_release(cconf(tmp_path), "phone", NOW, lambda cmd: "ok x1234\n") == ""      # not configured


def test_auto_release_after_pass_and_minimum_hold(tmp_path):
    script = OFFLINE_5 + [(21, True, 8, True), (24, True, 10, True), (27, True, 12, True), (30, True, 14, True)]
    pushes, st, nft = captive_run(tmp_path, cconf(tmp_path, CAPTIVE_MIN_HOLD_MIN="10"), script)
    d = st["devices"]["phone"]
    assert d["captive_quarantine"] is False and d["last_release"]["how"] == "auto"
    assert any(p[0] == "dns-leak-watch: released phone (auto)" for p in pushes)


def test_still_held_before_the_minimum_hold(tmp_path):
    script = OFFLINE_5 + [(21, True, 8, True), (24, True, 10, True)]
    _, st, _ = captive_run(tmp_path, cconf(tmp_path, CAPTIVE_MIN_HOLD_MIN="10"), script)
    assert st["devices"]["phone"]["captive_quarantine"] is True


def test_not_always_on_gets_no_p5_and_no_hold(tmp_path):
    pushes, st, nft = captive_run(tmp_path, tconf(tmp_path, NTFY_P5_TOPICS="hot-alerts"), OFFLINE_5)
    assert not any("off the tunnel" in p[0] or "HELD" in p[0] for p in pushes) and not nft.ops


def test_exempt_always_on_host_gets_the_p5_but_is_never_held(tmp_path):
    pushes, st, nft = captive_run(tmp_path, cconf(tmp_path, QUARANTINE_EXEMPT="phone"), OFFLINE_5)
    assert any("off the tunnel" in p[0] for p in pushes) and not any("HELD" in p[0] for p in pushes)
    assert not st["devices"]["phone"].get("captive_quarantine") and not nft.ops


def test_manual_release_clears_a_captive_hold(tmp_path):
    c = cconf(tmp_path)
    captive_run(tmp_path, c, OFFLINE_5)
    assert dl.release(c, "phone", nft=Nft()) == 0
    d = json.loads((tmp_path / "out/state.json").read_text())["devices"]["phone"]
    assert d["captive_quarantine"] is False and d["last_release"]["how"] == "manual"


def test_release_approval_config_is_validated(tmp_path):
    for bad in ({"RELEASE_APPROVAL_CMD": "gate"}, {"RELEASE_APPROVAL_CMD": "/bin/gate --x"},
                {"RELEASE_APPROVAL_CMD": "/bin/gate"}, {"RELEASE_APPROVAL_CMD": "/bin/gate", "RELEASE_APPROVAL_USER": "root"},
                {"ALWAYS_ON_HOSTS": "a;c"}, {"NTFY_P5_TOPICS": "a;b"}, {"OFF_TUNNEL_ALERT_MIN": "0"}):
        with pytest.raises(dl.ConfError):
            cconf(tmp_path, **bad)


def test_star_watches_every_peer_and_p5_goes_to_every_topic(tmp_path):
    c = cconf(tmp_path, ALWAYS_ON_HOSTS="*", NTFY_P5_TOPICS="ops-health,hot-alerts")
    pushes, st, _ = captive_run(tmp_path, c, OFFLINE_5)
    p5 = [p for p in pushes if "off the tunnel" in p[0]]
    assert sorted(p[2] for p in p5) == ["hot-alerts", "ops-health"]               # once per topic, one episode
    assert st["devices"]["phone"]["captive_quarantine"] is True


def test_star_never_watches_this_host_or_proxies(tmp_path):
    c = cconf(tmp_path, ALWAYS_ON_HOSTS="*", PROXY_CLIENTS=f"127.0.0.1,{PH}")
    pushes, st, nft = captive_run(tmp_path, c, OFFLINE_5)
    assert not any("off the tunnel" in p[0] or "HELD" in p[0] for p in pushes) and not nft.ops


def test_star_still_needs_recent_routing(tmp_path):
    script = [(0, True, None, False), (3, True, None, False), (6, False, None, False), (12, False, None, False),
              (40, True, 1, True)]
    pushes, st, nft = captive_run(tmp_path, cconf(tmp_path, ALWAYS_ON_HOSTS="*"), script)
    assert not any("off the tunnel" in p[0] or "HELD" in p[0] for p in pushes)   # never used the exit node: no page


# 2026-11-27: the first install failed because a chain was named "fwd", an nft keyword (the fwd statement).
NFT_KEYWORDS = {"fwd", "dup", "log", "counter", "limit", "reject", "drop", "accept", "queue", "notrack", "flow",
                "meta", "ct", "ip", "ip6", "inet", "tcp", "udp", "set", "map", "table", "chain", "jump", "goto",
                "return", "masquerade", "redirect", "snat", "dnat", "tproxy", "synproxy", "last", "quota", "secmark"}


def test_chain_and_set_names_are_not_nft_keywords():
    c = dl.parse_conf((REPO / "config/dns-leak-watch/dnsleak.conf.example").read_text())
    nft = dl.render_nft(c)
    names = re.findall(r"^\s*(?:chain|set|counter)\s+([A-Za-z0-9_]+)\s*\{", nft, re.M)
    assert names, "no chains/sets found"
    assert not (set(names) & NFT_KEYWORDS), set(names) & NFT_KEYWORDS
