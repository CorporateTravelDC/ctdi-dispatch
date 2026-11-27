#!/usr/bin/env python3
"""dns_leak_watch.py -- 2026-11-27: DNS LEAK WATCH, and the root collector for DNS anomaly watch's bypass class
(docs/DNS_LEAK_WATCH.md).

ONE root collector, two consumers:
  * this tool's own report, the HARD-CODED IP class: a destination address no DNS answer handed out to ANYONE in the
    lookback window (INDEX_HOURS). Tiered so context is never mistaken for a finding:
        hardcoded     no Pi-hole answer to any client gave this address within the window
        other-client  answered within the window, but only for names other clients asked for
        cached-stale  answered for this device, but only before the window (an old cache)
        lookup        answered for this device within the window (normal)
  * the DNS BYPASS evidence, exported (not reported or pushed here) in latest.json "bypass_evidence" for
    scripts/dns-anomaly-watch.py, which classifies, correlates and alerts on it:
        dns53  plain DNS (port 53) forwarded to an outside resolver
        dot    port 853 over TCP (DNS over TLS)          doq  port 853 over UDP (DNS over QUIC)
        doh    TCP/UDP 443 to an address on the encrypted-resolver list (RESOLVER_LIST, a local file refreshed by
               the separate daily ctdc-dnsleak-resolvers timer; this watch only reads it)
     plus the dns-lock table's tailnet port-53 redirect counter as a per-run delta (optional; absent = skipped).
    Bypass destinations are left out of the hard-coded report (an encrypted resolver is usually a built-in address
    anyway; the finding that matters is the bypass).

The device's traffic must be routed through this host (a Tailscale exit node, or this host as its gateway). An nft
table (inet ctdc_dnsleak, rendered by `render-nft`) adds every NEW forwarded TCP/UDP connection from the watched
addresses to a dynamic set keyed on destination . protocol . port, with per-element counters. Each run (system timer,
every 3 min, as root): read the sets and counters; fold newly written Pi-hole log lines into a query index
(name -> client -> last query) and an answer index (address -> name -> last answer); classify each destination once;
write OUT_DIR/<date>.csv, summary.txt and latest.json (schema 1, stable); push once per new hard-coded key and a daily
digest. The first run is a silent baseline.

  dns_leak_watch.py render-nft --conf FILE
  dns_leak_watch.py run --conf FILE
  dns_leak_watch.py status --conf FILE
  dns_leak_watch.py refresh-resolvers --conf FILE     the daily resolver-list refresh (its own timer)
  dns_leak_watch.py check-resolvers --file FILE
  dns_leak_watch.py release --conf FILE --host NAME|--all     lift a connection-check quarantine
  dns_leak_watch.py diagnose --conf FILE [--seconds N]  which client addresses Pi-hole logs while the device is active
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import fcntl
import grp
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TABLE = "ctdc_dnsleak"
SCHEMA = 1
STALE_HORIZON_DAYS = 30            # answers older than this are forgotten (cached-stale needs some history)
FINDINGS_KEEP_DAYS = 7
BYPASS = ("dns53", "dot", "doq", "doh")
HARD = ("hardcoded", "other-client", "cached-stale")
KEYS = {"CLIENTS", "TAILNET", "PROXY_CLIENTS", "IFACE", "CHECK_WINDOW_MIN", "QUARANTINE", "QUARANTINE_GRACE_MIN",
        "DEVICE_SSH_CHECK", "DEVICE_SSH_USER", "QUARANTINE_EXEMPT",
        "ALWAYS_ON_HOSTS", "ROUTED_RECENT_MIN", "OFF_TUNNEL_ALERT_MIN", "NTFY_P5_TOPICS", "CAPTIVE_MIN_HOLD_MIN",
        "RELEASE_APPROVAL_CMD", "RELEASE_APPROVAL_USER", "PIHOLE_LOG", "OUT_DIR", "READ_GROUP", "SET_TIMEOUT", "INDEX_HOURS", "RDNS",
        "RESOLVER_LIST", "RESOLVER_LIST_URL", "RESOLVER_MIN", "METERED_IFACES", "DNSLOCK_COUNTER", "NTFY_URL", "NTFY_TOPIC", "NTFY_TOKEN_FILE"}
DEFAULTS = {"PIHOLE_LOG": "/var/log/pihole/pihole.log", "OUT_DIR": "/var/lib/ctdc-dnsleak", "READ_GROUP": "root",
            "SET_TIMEOUT": "6h", "INDEX_HOURS": "48", "RDNS": "no",
            "RESOLVER_LIST": "/etc/ctdc-dnsleak/encrypted-resolvers.txt", "RESOLVER_MIN": "100", "METERED_IFACES": "",
            "RESOLVER_LIST_URL": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/ips/doh.txt",
            "DNSLOCK_COUNTER": "inet ctdc_dnslock dns_tailnet_redirect",
            "NTFY_URL": "", "NTFY_TOPIC": "", "NTFY_TOKEN_FILE": "",
            "TAILNET": "no", "CHECK_WINDOW_MIN": "10", "QUARANTINE": "no", "QUARANTINE_GRACE_MIN": "5", "QUARANTINE_EXEMPT": "",
            "DEVICE_SSH_CHECK": "no", "DEVICE_SSH_USER": "",
            "ALWAYS_ON_HOSTS": "", "ROUTED_RECENT_MIN": "30", "OFF_TUNNEL_ALERT_MIN": "5", "NTFY_P5_TOPICS": "",
            "CAPTIVE_MIN_HOLD_MIN": "10", "RELEASE_APPROVAL_CMD": "", "RELEASE_APPROVAL_USER": ""}
# Tailscale's own address ranges: the only networks wider than 65536 addresses CLIENTS accepts (TAILNET=yes = both).
TAILNET_RANGES = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")
ANS = re.compile(r"\b(?:reply|cached|cached-stale)\s+(\S+)\s+is\s+(\S+)")
QRY = re.compile(r"\bquery\[[A-Za-z0-9]+\]\s+(\S+)\s+from\s+(\S+)")


# "shared-address": an other-client destination whose Pi-hole answer name or reverse-DNS name ends in one of these
# large CDN / cloud suffixes. A heuristic (docs/DNS_LEAK_WATCH.md, "Reading the tiers"): a CDN without PTR records
# (Cloudflare, most Fastly) is not caught, and a customer's own server on these clouds reads as shared too.
SHARED_SUFFIXES = ("cloudfront.net", "akamaiedge.net", "akamaitechnologies.com", "akamai.net", "edgekey.net",
                   "edgesuite.net", "1e100.net", "googleusercontent.com", "gvt1.com", "googlevideo.com",
                   "amazonaws.com", "azureedge.net", "cloudapp.azure.com", "cloudapp.net", "trafficmanager.net",
                   "fastly.net", "fastlylb.net", "cdn.cloudflare.net", "edgecastcdn.net", "llnwd.net", "aaplimg.com",
                   "fbcdn.net", "cdninstagram.com", "steamcontent.com")


def is_shared(*names) -> bool:
    return any(n and any(n == s or n.endswith("." + s) for s in SHARED_SUFFIXES) for n in names)


class ConfError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------- config --
def parse_conf(text: str) -> dict:
    """key=value lines, '#' comments; parsed, never sourced. Unknown keys and bad values are errors."""
    c = dict(DEFAULTS)
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfError(f"line {n}: not key=value")
        k, v = (s.strip() for s in line.split("=", 1))
        if k not in KEYS:
            raise ConfError(f"line {n}: unknown key {k}")
        c[k] = v.strip('"').strip("'")
    if c["TAILNET"] not in ("yes", "no"):
        raise ConfError("TAILNET: yes or no")
    nets = [ipaddress.ip_network(r) for r in TAILNET_RANGES] if c["TAILNET"] == "yes" else []
    for tok in re.split(r"[,\s]+", c.get("CLIENTS", "").strip()):
        if not tok:
            continue
        try:
            nets.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            raise ConfError(f"CLIENTS: {tok!r} is not an address or CIDR") from None
    if not nets:
        raise ConfError("CLIENTS: at least one address or CIDR is required")
    wide_ok = {ipaddress.ip_network(r) for r in TAILNET_RANGES}
    if any(n.num_addresses > 65536 and n not in wide_ok for n in nets):
        raise ConfError("CLIENTS: a network wider than 65536 addresses is refused (except Tailscale's two ranges)")
    nets = list(dict.fromkeys(nets))
    c["CLIENTS"] = nets
    proxies = []
    for tok in re.split(r"[,\s]+", c.get("PROXY_CLIENTS", "").strip()):
        if not tok:
            continue
        try:
            proxies.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            raise ConfError(f"PROXY_CLIENTS: {tok!r} is not an address or CIDR") from None
    c["PROXY_CLIENTS"] = proxies
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", c.get("IFACE", "")):
        raise ConfError("IFACE: an interface name is required (e.g. tailscale0, or the LAN interface)")
    for k in ("PIHOLE_LOG", "OUT_DIR", "RESOLVER_LIST"):
        if not c[k].startswith("/") or ".." in c[k].split("/"):
            raise ConfError(f"{k}: an absolute path is required")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", c["READ_GROUP"]):
        raise ConfError("READ_GROUP: not a group name")
    if not re.fullmatch(r"\d{1,3}[smhd]", c["SET_TIMEOUT"]):
        raise ConfError("SET_TIMEOUT: like 6h or 90m")
    if not re.fullmatch(r"\d{1,3}", c["INDEX_HOURS"]) or not 1 <= int(c["INDEX_HOURS"]) <= 168:
        raise ConfError("INDEX_HOURS: 1-168")
    c["INDEX_HOURS"] = int(c["INDEX_HOURS"])
    for k, lo, hi in (("CHECK_WINDOW_MIN", 2, 120), ("QUARANTINE_GRACE_MIN", 0, 1440)):
        if not re.fullmatch(r"\d{1,4}", c[k]) or not lo <= int(c[k]) <= hi:
            raise ConfError(f"{k}: {lo}-{hi}")
        c[k] = int(c[k])
    ex = [x for x in re.split(r"[,\s]+", c["QUARANTINE_EXEMPT"].strip()) if x]
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", x) for x in ex):
        raise ConfError("QUARANTINE_EXEMPT: host names, comma-separated")
    c["QUARANTINE_EXEMPT"] = ex
    ao = [x for x in re.split(r"[,\s]+", c["ALWAYS_ON_HOSTS"].strip()) if x]
    if ao != ["*"] and any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", x) for x in ao):
        raise ConfError("ALWAYS_ON_HOSTS: host names, comma-separated, or * for every peer")
    c["ALWAYS_ON_HOSTS"] = ao
    for k, lo, hi in (("ROUTED_RECENT_MIN", 1, 1440), ("OFF_TUNNEL_ALERT_MIN", 1, 1440), ("CAPTIVE_MIN_HOLD_MIN", 0, 1440)):
        if not re.fullmatch(r"\d{1,4}", c[k]) or not lo <= int(c[k]) <= hi:
            raise ConfError(f"{k}: {lo}-{hi}")
        c[k] = int(c[k])
    tp = [x for x in re.split(r"[,\s]+", c["NTFY_P5_TOPICS"].strip()) if x]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", x) for x in tp):
        raise ConfError("NTFY_P5_TOPICS: topic names, comma-separated")
    c["NTFY_P5_TOPICS"] = tp or [""]                       # empty = the normal NTFY_TOPIC
    if c["RELEASE_APPROVAL_CMD"] and (not c["RELEASE_APPROVAL_CMD"].startswith("/") or ".." in c["RELEASE_APPROVAL_CMD"].split("/")
                                      or " " in c["RELEASE_APPROVAL_CMD"]):
        raise ConfError("RELEASE_APPROVAL_CMD: an absolute path to a program, no arguments")
    if c["RELEASE_APPROVAL_USER"] and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", c["RELEASE_APPROVAL_USER"]):
        raise ConfError("RELEASE_APPROVAL_USER: a user name")
    if c["RELEASE_APPROVAL_CMD"] and (not c["RELEASE_APPROVAL_USER"] or c["RELEASE_APPROVAL_USER"] == "root"):
        raise ConfError("RELEASE_APPROVAL_CMD needs RELEASE_APPROVAL_USER, a non-root user it runs as")
    if c["QUARANTINE"] not in ("no", "alert-then-quarantine"):
        raise ConfError("QUARANTINE: no or alert-then-quarantine")
    if c["DEVICE_SSH_CHECK"] not in ("yes", "no"):
        raise ConfError("DEVICE_SSH_CHECK: yes or no")
    if c["DEVICE_SSH_USER"] and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", c["DEVICE_SSH_USER"]):
        raise ConfError("DEVICE_SSH_USER: a user name")
    if c["DEVICE_SSH_CHECK"] == "yes" and (not c["DEVICE_SSH_USER"] or c["DEVICE_SSH_USER"] == "root"):
        raise ConfError("DEVICE_SSH_CHECK=yes needs DEVICE_SSH_USER, a non-root user")
    if c["RDNS"] not in ("yes", "no"):
        raise ConfError("RDNS: yes or no")
    if c["RESOLVER_LIST_URL"] and not re.fullmatch(r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._/~-]*)?", c["RESOLVER_LIST_URL"]):
        raise ConfError("RESOLVER_LIST_URL: an https URL")
    if not re.fullmatch(r"\d{1,5}", c["RESOLVER_MIN"]) or int(c["RESOLVER_MIN"]) < 1:
        raise ConfError("RESOLVER_MIN: a positive number")
    c["RESOLVER_MIN"] = int(c["RESOLVER_MIN"])
    if c["METERED_IFACES"] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}([ ,][A-Za-z0-9_.-]{1,15})*", c["METERED_IFACES"]):
        raise ConfError("METERED_IFACES: interface names, space- or comma-separated")
    if c["DNSLOCK_COUNTER"] and not re.fullmatch(r"(inet|ip|ip6) [a-z_][a-z0-9_]{0,63} [a-z_][a-z0-9_]{0,63}", c["DNSLOCK_COUNTER"]):
        raise ConfError("DNSLOCK_COUNTER: 'family table counter', or empty")
    if c["NTFY_URL"] and not re.fullmatch(r"https?://[A-Za-z0-9.:\[\]_-]+(/[A-Za-z0-9._/-]*)?", c["NTFY_URL"]):
        raise ConfError("NTFY_URL: http(s)://host[:port]")
    if c["NTFY_TOPIC"] and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", c["NTFY_TOPIC"]):
        raise ConfError("NTFY_TOPIC: letters, digits, - and _")
    if c["NTFY_TOKEN_FILE"] and not c["NTFY_TOKEN_FILE"].startswith("/"):
        raise ConfError("NTFY_TOKEN_FILE: an absolute path")
    return c


def render_nft(c: dict) -> str:
    v4 = [str(n) for n in c["CLIENTS"] if n.version == 4]
    v6 = [str(n) for n in c["CLIENTS"] if n.version == 6]
    el = lambda xs: f" elements = {{ {', '.join(xs)} }};" if xs else ""
    t = c["SET_TIMEOUT"]
    return f"""# /etc/nftables.d/ctdc-dnsleak.nft -- RENDERED by dns_leak_watch.py render-nft; do not edit (docs/DNS_LEAK_WATCH.md)
table inet {TABLE}
delete table inet {TABLE}
table inet {TABLE} {{
    set clients4 {{ type ipv4_addr; flags interval;{el(v4)} }}
    set clients6 {{ type ipv6_addr; flags interval;{el(v6)} }}
    set seen4 {{ type ipv4_addr . ipv4_addr . inet_proto . inet_service; flags dynamic, timeout; timeout {t}; size 65535; counter; }}
    set seen6 {{ type ipv6_addr . ipv6_addr . inet_proto . inet_service; flags dynamic, timeout; timeout {t}; size 65535; counter; }}
    set quar4 {{ type ipv4_addr; comment "quarantined devices (QUARANTINE=alert-then-quarantine)"; }}
    set quar6 {{ type ipv6_addr; comment "quarantined devices (QUARANTINE=alert-then-quarantine)"; }}
    counter c_new {{ }}
    counter c_dns53 {{ }}
    counter c_dot {{ }}
    counter c_quic {{ }}
    counter c_tls {{ }}
    chain fwd {{
        type filter hook forward priority filter - 20; policy accept;
        iifname "{c['IFACE']}" ct state new meta l4proto {{ tcp, udp }} jump watch
        # quarantine AFTER the watch records the attempt, so a quarantined device's traffic still counts toward the
        # PASS that releases it; only forwarded traffic is dropped (DNS to this host and Tailscale itself are not)
        iifname "{c['IFACE']}" ip saddr @quar4 ip daddr != {TAILNET_RANGES[0]} counter drop
        iifname "{c['IFACE']}" ip6 saddr @quar6 ip6 daddr != {TAILNET_RANGES[1]} counter drop
    }}
    chain watch {{
        ip saddr @clients4 update @seen4 {{ ip saddr . ip daddr . meta l4proto . th dport }} goto tally
        ip6 saddr @clients6 update @seen6 {{ ip6 saddr . ip6 daddr . meta l4proto . th dport }} goto tally
    }}
    chain tally {{
        counter name "c_new"
        th dport 53 counter name "c_dns53"
        th dport 853 counter name "c_dot"
        udp dport 443 counter name "c_quic"
        tcp dport 443 counter name "c_tls"
    }}
}}
"""


# ------------------------------------------------------------------------------------- encrypted resolvers --
def parse_resolvers(text: str) -> list:
    """One address or CIDR per line; '#' comments and blank lines ignored; anything else is skipped (counted)."""
    nets, bad = [], 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            nets.append(ipaddress.ip_network(line, strict=False))
        except ValueError:
            bad += 1
    return nets, bad


def load_resolvers(path: str):
    try:
        nets, _ = parse_resolvers(Path(path).read_text())
        return nets
    except OSError:
        return None                                       # no list: DoH-by-address detection is off (said in summary)


def in_nets(addr: str, nets) -> bool:
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(a.version == n.version and a in n for n in nets or [])


# ------------------------------------------------------------------------------------------ Pi-hole index --
class Index:
    """queries: name -> {client: last query epoch}; answers: address -> {name: last answer epoch}.
    Pi-hole's log does not tie a reply to the client that asked, so an answer is attributed to every client that
    queried that name (docs: "how classification works")."""

    def __init__(self, data: dict | None = None):
        d = data or {}
        self.queries: dict[str, dict] = dict(d.get("queries", {}))
        self.answers: dict[str, dict] = dict(d.get("answers", {}))
        self.inode = d.get("inode")
        self.offset = d.get("offset", 0)
        self.pass_q: dict[str, int] = {}                    # queries per client in THIS pass (not persisted)

    def feed(self, lines, now: float) -> int:
        n = 0
        for line in lines:
            m = ANS.search(line)
            if m:
                addr = m.group(2)
                try:
                    ipaddress.ip_address(addr)
                except ValueError:
                    continue
                self.answers.setdefault(addr, {})[m.group(1).rstrip(".")] = now
                n += 1
                continue
            m = QRY.search(line)
            if m:
                self.queries.setdefault(m.group(1).rstrip("."), {})[m.group(2)] = now
                self.pass_q[m.group(2)] = self.pass_q.get(m.group(2), 0) + 1
        return n

    def prune(self, now: float):
        cut = now - STALE_HORIZON_DAYS * 86400
        self.answers = {a: {k: t for k, t in v.items() if t >= cut} for a, v in self.answers.items()}
        self.answers = {a: v for a, v in self.answers.items() if v}
        self.queries = {q: {k: t for k, t in v.items() if t >= cut} for q, v in self.queries.items()}
        self.queries = {q: v for q, v in self.queries.items() if v}

    def classify(self, addr: str, mine, now: float, hours: int) -> tuple[str, str]:
        """(tier, name) for one destination; mine(client) says whether a client address is the watched device."""
        cut = now - hours * 3600
        ans = self.answers.get(addr, {})
        recent = [n for n, t in ans.items() if t >= cut]
        if recent:
            for n in recent:
                if any(mine(c) and t >= cut for c, t in self.queries.get(n, {}).items()):
                    return "lookup", n
            return "other-client", recent[0]
        for n in ans:                                    # only answers older than the window remain
            if any(mine(c) for c in self.queries.get(n, {})):
                return "cached-stale", n
        return "hardcoded", ""

    def to_json(self) -> dict:
        return {"queries": self.queries, "answers": self.answers, "inode": self.inode, "offset": self.offset}


def update_index(idx: Index, log: Path, now: float, first: bool):
    """Only what was appended since the last run; a rotation (new inode) reads the rotated file's remainder once; the
    first run also reads yesterday's rotation (same path + .1) whole."""
    if not log.exists():
        return
    st = log.stat()
    rotated = Path(str(log) + ".1")
    if first and rotated.exists():
        with open(rotated, errors="replace") as fh:
            idx.feed(fh, now)
    if idx.inode not in (None, st.st_ino):
        if rotated.exists() and rotated.stat().st_ino == idx.inode:
            with open(rotated, errors="replace") as fh:
                fh.seek(idx.offset)
                idx.feed(fh, now)
        idx.offset = 0
    if st.st_size < idx.offset:
        idx.offset = 0
    with open(log, errors="replace") as fh:
        fh.seek(idx.offset)
        idx.feed(fh, now)
        idx.offset = fh.tell()
    idx.inode = st.st_ino


def classify_destination(dest: str, proto: str, port: int, idx: Index, mine, resolvers, now: float, hours: int):
    """(class, name). A bypass kind (dns53 / dot / doq / doh) wins over the hard-coded tiers: those destinations are
    exported as bypass evidence for DNS anomaly watch and left out of this tool's hard-coded report."""
    tier, name = idx.classify(dest, mine, now, hours)
    if port == 53:
        return "dns53", name
    if port == 853:
        return ("doq" if proto == "udp" else "dot"), name
    if port == 443 and in_nets(dest, resolvers):
        return "doh", name
    return tier, name


# -------------------------------------------------------------------------------------------------- nft --
def _proto(p) -> str:
    return {6: "tcp", 17: "udp"}.get(p, str(p)) if isinstance(p, int) else str(p)


def parse_set_json(obj: dict) -> list:
    """`nft -j list set` output -> [(source, destination, proto, port, connections)] (source "" for 3-part keys)."""
    out = []
    for item in obj.get("nftables", []):
        s = item.get("set")
        if not s:
            continue
        for e in s.get("elem", []) or []:
            el = e.get("elem", e) if isinstance(e, dict) else e
            val = el.get("val", el) if isinstance(el, dict) else el
            parts = val.get("concat") if isinstance(val, dict) else None
            if not parts or len(parts) not in (3, 4):
                continue
            if len(parts) == 3:
                parts = [""] + list(parts)
            cnt = (el.get("counter") or {}).get("packets", 0) if isinstance(el, dict) else 0
            try:
                out.append((str(parts[0]), str(parts[1]), _proto(parts[2]), int(parts[3]), int(cnt)))
            except (TypeError, ValueError):
                continue
    return out


def nft_json(*args) -> dict:
    r = subprocess.run(["nft", "-j", *args], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"nft {' '.join(args)}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def counter_packets(spec: str):
    """packets of one named counter ('family table name'), or None when the table/counter does not exist."""
    try:
        o = nft_json("list", "counter", *spec.split())
    except Exception:
        return None
    for it in o.get("nftables", []):
        if "counter" in it:
            return int(it["counter"].get("packets", 0))
    return None


def read_counters(c: dict) -> dict:
    out = {n: counter_packets(f"inet {TABLE} {n}") for n in ("c_new", "c_dns53", "c_dot", "c_quic", "c_tls")}
    out["dnslock_redirect"] = counter_packets(c["DNSLOCK_COUNTER"]) if c["DNSLOCK_COUNTER"] else None
    return out


# ------------------------------------------------------------------------------------------------ report --
def _mine_fn(nets, proxies=()):
    nets = list(nets) + list(proxies)

    def mine(client: str) -> bool:
        try:
            a = ipaddress.ip_address(client)
        except ValueError:
            return False
        return any(a.version == n.version and a in n for n in nets)
    return mine


def process(elements, idx: Index, state: dict, c: dict, resolvers, now: float, devmap=None, hostof=None):
    """Fold set elements into state['seen'] (keyed destination|proto|port, aggregated over devices); classify each
    destination on first sight. With per-device data (devmap: host -> addresses, hostof: address -> host) "this device"
    is the source's own device; without it, all CLIENTS are one device. Returns new findings
    [(class, dest, proto, port, name)]."""
    seen = state.setdefault("seen", {})
    group = _mine_fn(c["CLIENTS"], c["PROXY_CLIENTS"])
    new = []
    for src, dest, proto, port, cnt in elements:
        host = (hostof or {}).get(src, "")
        if host and devmap:
            nets = [ipaddress.ip_network(a) for a in devmap.get(host, [])]
            mine = _mine_fn(nets, c["PROXY_CLIENTS"])
        else:
            mine = group
        key = f"{dest}|{proto}|{port}"
        rec = seen.get(key)
        if rec is None:
            cls, name = classify_destination(dest, proto, port, idx, mine, resolvers, now, c["INDEX_HOURS"])
            rec = seen[key] = {"first": now, "class": cls, "name": name, "conns": 0, "last_cnt": {}, "host": host}
            new.append((cls, dest, proto, port, name))
        elif not rec.get("name"):
            tier, name = idx.classify(dest, mine, now, c["INDEX_HOURS"])
            if name:
                rec["later_name"] = name                  # context only: the first classification stands
        lc = rec.get("last_cnt")
        if not isinstance(lc, dict):                       # pre-per-device state: one counter
            lc = rec["last_cnt"] = {}
        sk = host or "-"
        last = lc.get(sk, 0)
        rec["conns"] += max(cnt - last if cnt >= last else cnt, 0)
        lc[sk] = cnt
        rec["last"] = now
    return new


# ------------------------------------------------------------------------------ per-device connection check --
# Every tailnet device the collector watches gets a CHECK_WINDOW_MIN evaluation window whenever it comes online or
# starts sending forwarded traffic; while it stays online, a new window opens after each verdict:
#   PASS        forwarded traffic AND Pi-hole lookups from it (or from PROXY_CLIENTS while it was active) AND no bypass
#   FAIL        forwarded traffic but no lookups (its DNS goes elsewhere, or is broken), or any bypass evidence
#   NOT-ROUTED  online but no forwarded traffic: it is not using this exit node, so this box cannot vouch for it
# Alerts: once per device per verdict change (a first PASS is not pushed; a PASS after FAIL / NOT-ROUTED is).
# QUARANTINE=alert-then-quarantine: a device still FAILing QUARANTINE_GRACE_MIN after its first FAIL has its forwarded
# traffic dropped (nft sets quar4/quar6), except to the tailnet itself; DNS to this host and Tailscale's own control
# traffic are not forwarded traffic, so they keep working. Released automatically on its next PASS, or by
# `apply.sh release HOST|--all`. Never this host, a PROXY_CLIENTS address, or a QUARANTINE_EXEMPT host.
DEVICE_PUBLIC = {"verdict", "verdict_since", "reason", "online", "window_open", "quarantined", "quarantined_at",
                 "os", "device_check", "device_check_at", "exempt", "captive_quarantine", "captive_since",
                 "off_tunnel", "last_release"}


def tailscale_peers(c: dict, raw: dict | None = None):
    """(peers, self_addrs): peers = [{host, os, online, addrs}] whose addresses fall in CLIENTS, from
    `tailscale status --json` (root may run it). (None, set()) when Tailscale is unavailable."""
    if raw is None:
        try:
            r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=20)
            raw = json.loads(r.stdout) if r.returncode == 0 else None
        except Exception:
            raw = None
    if not raw:
        return None, set()
    me = set((raw.get("Self") or {}).get("TailscaleIPs") or [])
    out = []
    for pe in (raw.get("Peer") or {}).values():
        addrs = [a for a in pe.get("TailscaleIPs") or [] if in_nets(a, c["CLIENTS"])]
        host = re.sub(r"[^A-Za-z0-9-]", "-", (pe.get("HostName") or "").strip())[:63]
        if addrs and host:
            out.append({"host": host, "os": (pe.get("OS") or "").lower(), "online": bool(pe.get("Online")), "addrs": addrs})
    return out, me


def protected(host: str, addrs, c: dict, self_addrs, local=None) -> bool:
    """Never quarantined: a QUARANTINE_EXEMPT host, this host's own addresses, PROXY_CLIENTS, loopback."""
    if host in c["QUARANTINE_EXEMPT"]:
        return True
    local = local_addresses() if local is None else local
    for a in addrs:
        if a in self_addrs or a in local or in_nets(a, c["PROXY_CLIENTS"]) or a.startswith("127.") or a == "::1":
            return True
    return False


def nft_quarantine(op: str, addrs) -> bool:
    """op 'add' | 'delete' the device's addresses in quar4 / quar6. add is idempotent; deleting a missing one is ignored."""
    ok = True
    for a in addrs:
        s = "quar6" if ":" in a else "quar4"
        r = subprocess.run(["nft", op, "element", "inet", TABLE, s, "{", a, "}"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0 and op == "add":
            ok = False
    return ok


def signed_release(c: dict, host: str, since: float, runner=None) -> str:
    """The id of a SIGNED captive-portal release for HOST resolved after SINCE, or "". RELEASE_APPROVAL_CMD is run as
    RELEASE_APPROVAL_USER (runuser; never root, never a shell) with `check --host HOST --since EPOCH`; only an exact
    "ok <id>" first line counts. The program must verify the human signature itself (this deployment:
    scripts/captive-release-gate.sh, which re-verifies it with the governance code)."""
    if not c["RELEASE_APPROVAL_CMD"]:
        return ""
    cmd = ["runuser", "-u", c["RELEASE_APPROVAL_USER"], "--", c["RELEASE_APPROVAL_CMD"], "check", "--host", host,
           "--since", str(int(since))]
    try:
        if runner:
            out = runner(cmd)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            out = r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""
    m = re.fullmatch(r"ok ([A-Za-z0-9_-]{4,64})", (out.splitlines() or [""])[0].strip())
    return m.group(1) if m else ""


def _held(d: dict) -> bool:
    return bool(d.get("quarantined") or d.get("captive_quarantine"))


def device_check(c: dict, host: str, script: Path, runner=None) -> list:
    """DEVICE_SSH_CHECK=yes: run the vendored dns-lockdown-check.sh on a Linux peer through Tailscale SSH as
    DEVICE_SSH_USER (60 s limit). Keeps only its PASS / FAIL lines."""
    cmd = ["timeout", "60", "tailscale", "ssh", f"{c['DEVICE_SSH_USER']}@{host}", "--", "bash", "-s"]
    try:
        if runner:
            outp = runner(cmd, script.read_text())
        else:
            outp = subprocess.run(cmd, input=script.read_text(), capture_output=True, text=True, timeout=75).stdout
    except Exception as e:
        return [f"FAIL  device check could not run ({type(e).__name__})"]
    lines = [l.strip() for l in outp.splitlines() if l.strip().startswith(("PASS", "FAIL"))]
    return lines[:20] or ["FAIL  device check returned no PASS/FAIL lines"]


def off_tunnel(c: dict, h: str, d: dict, r: dict, addrs, online: bool, fwd: int, last_fwd: float, now: float,
               notify, nft=nft_quarantine, addr_protected=False):
    """ALWAYS_ON_HOSTS only. An episode starts when a host routed through this box within ROUTED_RECENT_MIN goes
    tailnet-Offline, or stays Online with no forwarded traffic. OFF_TUNNEL_ALERT_MIN later: ONE priority-5 push per
    episode (NTFY_P5_TOPIC). When it comes back (Online + forwarded traffic) after an ALERTED episode it is put in a
    captive-portal hold at once: the quarantine sets, so DNS to this host and Tailscale keep working. The box cannot
    block anything DURING the outage: the device's traffic is not routed here then. Never this host, PROXY_CLIENTS or
    QUARANTINE_EXEMPT. ALWAYS_ON_HOSTS=* means every peer; this host and PROXY_CLIENTS addresses are never watched
    for this at all; a QUARANTINE_EXEMPT host still gets the push but is never held. The P5 goes to every topic in
    NTFY_P5_TOPICS, once per episode."""
    if addr_protected or (c["ALWAYS_ON_HOSTS"] != ["*"] and h not in c["ALWAYS_ON_HOSTS"]):
        r.pop("episode", None)
        return
    back = online and fwd > 0
    ep = r.get("episode")
    if not back:
        if ep is None and last_fwd and now - last_fwd <= c["ROUTED_RECENT_MIN"] * 60:
            start = last_fwd if online else max(last_fwd, now - 1)    # offline: from when we first see it offline
            ep = r["episode"] = {"start": now if not online else start, "alerted": False,
                                 "kind": "offline" if not online else "not-routed"}
        if ep is not None:
            if not online:
                ep["kind"] = "offline"
            d["off_tunnel"] = {"since": ep["start"], "alerted": ep["alerted"], "kind": ep["kind"]}
            if not ep["alerted"] and now - ep["start"] >= c["OFF_TUNNEL_ALERT_MIN"] * 60:
                ep["alerted"] = True
                d["off_tunnel"]["alerted"] = True
                mins = int((now - ep["start"]) // 60)
                for topic in c["NTFY_P5_TOPICS"]:
                    notify(f"dns-leak-watch: {h} off the tunnel {mins}+ min (captive portal?)",
                           f"{h} has not routed through this box for {mins} min ({'tailnet offline' if ep['kind'] == 'offline' else 'online but not using this exit node'}). "
                           f"Its traffic is NOT protected meanwhile. When it returns it is held until a signed captive-portal "
                           f"release, or until it passes the connection check after {c['CAPTIVE_MIN_HOLD_MIN']} min.", 5,
                           topic)
        return
    if ep is None:
        return
    r.pop("episode", None)
    d.pop("off_tunnel", None)
    if not ep["alerted"]:
        return                                                         # a short blip: nothing to do
    if d.get("exempt"):
        notify(f"dns-leak-watch: {h} back on the tunnel", f"{h} is routed through this box again (exempt: not held).", 4)
        return
    if nft("add", addrs):
        d["captive_quarantine"], d["captive_since"] = True, now
        notify(f"dns-leak-watch: HELD {h} (troublesome captive portal?)",
               f"{h} is back after {int((now - ep['start']) // 60)} min off the tunnel. Forwarded traffic held; DNS to this "
               f"host and Tailscale keep working. Release: a signed captive-portal release, or automatically once it "
               f"passes the connection check after {c['CAPTIVE_MIN_HOLD_MIN']} min, or: apply.sh release {h}", 5,
               c["NTFY_P5_TOPICS"][0])


def evaluate_devices(c: dict, state: dict, root: dict, peers, self_addrs, elements, pass_q: dict, classes: dict,
                     now: float, first: bool, notify, nft=nft_quarantine, checker=None, local=None, approver=None):
    """One pass of the per-device connection check. elements: (src, dest, proto, port, cnt); classes:
    dest|proto|port -> class; pass_q: Pi-hole queries per client address in this pass. state['devices'] holds host
    names only; root['devices'] and root['elem_last'] hold the addresses (root-only file)."""
    devs = state.setdefault("devices", {})
    rdev = root.setdefault("devices", {})
    elast = root.setdefault("elem_last", {})
    win = c["CHECK_WINDOW_MIN"] * 60
    proxy_q = sum(n for cl, n in pass_q.items() if in_nets(cl, c["PROXY_CLIENTS"]))
    by_src: dict = {}
    for src, dest, proto, port, cnt in elements:
        by_src.setdefault(src, []).append((dest, proto, port, cnt))
    events = []
    for pe in peers:
        h, addrs = pe["host"], pe["addrs"]
        d = devs.setdefault(h, {"verdict": None, "online": False})
        r = rdev.setdefault(h, {})
        r["addrs"] = addrs
        d["os"] = pe["os"]
        d["exempt"] = protected(h, addrs, c, self_addrs, local)
        fwd, byp = 0, False
        for a in addrs:
            for dest, proto, port, cnt in by_src.get(a, []):
                k = f"{a}|{dest}|{proto}|{port}"
                last = elast.get(k, 0)
                delta = cnt - last if cnt >= last else cnt
                elast[k] = cnt
                if delta > 0:
                    fwd += delta
                    if classes.get(f"{dest}|{proto}|{port}") in BYPASS:
                        byp = True
        q = sum(pass_q.get(a, 0) for a in addrs) + (proxy_q if fwd else 0)
        was_online, d["online"] = d.get("online", False), pe["online"]
        last_fwd = r.get("last_fwd_at", 0)
        if fwd > 0 and pe["online"]:
            r["last_fwd_at"] = now
        if first:
            continue                                                  # baseline: counters recorded, no windows
        addr_prot = protected(h, addrs, {**c, "QUARANTINE_EXEMPT": []}, self_addrs, local)
        off_tunnel(c, h, d, r, addrs, pe["online"], fwd, last_fwd, now, notify, nft, addr_prot)
        if not pe["online"]:
            d["window_open"] = False
            r.pop("win", None)
            continue
        w = r.get("win")
        if w is None and (not was_online or fwd > 0 or not r.get("evaluated")):
            w = r["win"] = {"start": now, "fwd": 0, "q": 0, "bypass": False}
            d["window_open"] = True
            if c["DEVICE_SSH_CHECK"] == "yes" and pe["os"] == "linux" and now - d.get("device_check_at", 0) >= 6 * 3600:
                run_check = checker or (lambda host: device_check(c, host, Path(__file__).with_name("dns-lockdown-check.sh")))
                d["device_check"], d["device_check_at"] = run_check(h), now
        if w is None:
            continue
        w["fwd"] += fwd
        w["q"] += q
        w["bypass"] = w["bypass"] or byp
        if now - w["start"] >= win:                                    # window end: the verdict
            if w["fwd"] == 0:
                verdict, reason = "NOT-ROUTED", "online, but no traffic through this exit node"
            elif w["bypass"]:
                verdict, reason = "FAIL", "DNS bypass evidence (plain DNS elsewhere, 853, or a known DoH resolver)"
            elif w["q"] == 0:
                verdict, reason = "FAIL", "traffic through this exit node but no Pi-hole lookups (DNS elsewhere, or broken)"
            else:
                verdict, reason = "PASS", f"{w['fwd']} connection(s), {w['q']} lookup(s), no bypass"
            r["evaluated"] = True
            r["win"] = {"start": now, "fwd": 0, "q": 0, "bypass": False}   # rolling: the next window starts now
            prev = d.get("verdict")
            if verdict != prev:
                d["verdict_since"] = now
                if not (verdict == "PASS" and prev is None):
                    events.append((h, verdict, reason, prev))
            d["verdict"], d["reason"] = verdict, reason
            if verdict == "FAIL":
                d.setdefault("fail_since", now)
            else:
                d.pop("fail_since", None)
            if verdict == "PASS" and d.get("quarantined"):
                d["quarantined"] = False
                if not _held(d):
                    nft("delete", addrs)
                d["last_release"] = {"at": now, "how": "auto", "why": "passed the connection check"}
                notify(f"dns-leak-watch: released {h} (auto)", f"{h} passed the connection check; quarantine lifted.", 4)
        # Grace is checked on EVERY pass: a device whose last verdict was FAIL and whose current window still shows no
        # sign of passing (traffic with lookups and no bypass) is quarantined QUARANTINE_GRACE_MIN after its first FAIL.
        w = r["win"]
        still_failing = w["bypass"] or (w["fwd"] > 0 and w["q"] == 0) or w["fwd"] == 0
        if (c["QUARANTINE"] == "alert-then-quarantine" and d.get("verdict") == "FAIL" and still_failing
                and not d.get("quarantined") and not d["exempt"] and "fail_since" in d
                and now - d["fail_since"] >= c["QUARANTINE_GRACE_MIN"] * 60):
            if nft("add", addrs):
                d["quarantined"], d["quarantined_at"] = True, now
                notify(f"dns-leak-watch: QUARANTINED {h}", f"{h} still failing {c['QUARANTINE_GRACE_MIN']} min after its "
                       f"first FAIL ({d.get('reason', '')}). Forwarded traffic dropped; DNS to this host and Tailscale keep "
                       f"working. Released on its next PASS, or: apply.sh release {h}", 5)
    for h, d in devs.items():                                          # captive holds: signed or auto release
        if not d.get("captive_quarantine"):
            continue
        addrs = rdev.get(h, {}).get("addrs", [])
        since = d.get("captive_since", now)
        how = why = ""
        aid = signed_release(c, h, since, approver)
        if aid:
            how, why = "signed", f"signed captive-portal release {aid}"
        elif (d.get("verdict") == "PASS" and d.get("verdict_since", 0) >= since
              and now - since >= c["CAPTIVE_MIN_HOLD_MIN"] * 60):
            how, why = "auto", f"passed the connection check, held {int((now - since) // 60)} min"
        if how:
            d["captive_quarantine"] = False
            if not _held(d):
                nft("delete", addrs)
            d["last_release"] = {"at": now, "how": how, "why": why}
            notify(f"dns-leak-watch: released {h} ({how})", f"{h}: captive-portal hold lifted: {why}.", 4)
    for h, d in devs.items():                                          # re-assert after a table reload
        if _held(d) and rdev.get(h, {}).get("addrs"):
            nft("add", rdev[h]["addrs"])
    for h, verdict, reason, prev in events:
        notify(f"dns-leak-watch: {h} {verdict}", f"{h}: {verdict} (was {prev or 'unchecked'}): {reason}.",
               4 if verdict != "PASS" else 3)
    return events


def release(c: dict, host: str, nft=nft_quarantine) -> int:
    """Manual escape: lift the quarantine on HOST, or on every device for --all."""
    out = Path(c["OUT_DIR"])
    with _locked(out):
        sp, rp = out / "state.json", out / "root-state.json"
        state = json.loads(sp.read_text()) if sp.exists() else {}
        root = json.loads(rp.read_text()) if rp.exists() else {}
        hosts = [h for h, d in state.get("devices", {}).items() if _held(d) and host in ("--all", h)]
        for h in hosts:
            nft("delete", root.get("devices", {}).get(h, {}).get("addrs", []))
            d = state["devices"][h]
            d["quarantined"] = d["captive_quarantine"] = False
            d.pop("fail_since", None)                      # a new grace period starts from the next FAIL
            d["last_release"] = {"at": time.time(), "how": "manual", "why": "apply.sh release"}
            push(c, f"dns-leak-watch: released {h} (manual)", f"{h}: quarantine lifted by apply.sh release.", 4)
        _write_private(sp, json.dumps(state), grp.getgrnam(c["READ_GROUP"]).gr_gid)
    print(f"dns-leak-watch: released {len(hosts)} device(s)" + ("" if hosts else f" (no quarantined device matches {host})"))
    return 0 if hosts or host == "--all" else 1


def device_summary(state: dict, now: float) -> list:
    devs = state.get("devices", {})
    if not devs:
        return []
    L = ["== CONNECTION CHECK (per tailnet device; PASS = traffic through this box with Pi-hole lookups and no bypass)"]
    for h, d in sorted(devs.items()):
        v = d.get("verdict") or ("offline" if not d.get("online") else "checking")
        extra = (" QUARANTINED" if d.get("quarantined") else "") + (" HELD(captive portal)" if d.get("captive_quarantine") else "")
        if d.get("off_tunnel"):
            extra += f" OFF-TUNNEL since {_ts(d['off_tunnel']['since'])}"
        extra = extra or (" (exempt)" if d.get("exempt") else "")
        L.append(f"  {h:<28} {v:<11}{extra}  {d.get('reason', '')}")
        for line in d.get("device_check", [])[:8]:
            L.append(f"      on-device: {line}")
    return L + [""]


@contextlib.contextmanager
def _locked(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def rdns(addr: str) -> str:
    try:
        r = subprocess.run(["dig", "+short", "+time=1", "+tries=1", "-x", addr, "@127.0.0.1"],
                           capture_output=True, text=True, timeout=4)
        return (r.stdout.split() or [""])[0].rstrip(".")
    except Exception:
        return ""


def _ts(t: float, fmt="%m-%d %H:%MZ") -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime(fmt)


def class_totals(state: dict) -> dict:
    tot = {k: {"destinations": 0, "connections": 0} for k in BYPASS + HARD + ("lookup",)}
    for v in state.get("seen", {}).values():
        t = tot.setdefault(v["class"], {"destinations": 0, "connections": 0})
        t["destinations"] += 1
        t["connections"] += v.get("conns", 0)
    return tot


def latest_json(state: dict, c: dict, counters: dict, now: float, resolvers) -> dict:
    """The stable machine-readable output (schema 1). "hardcoded" is this tool's finding; "bypass_evidence" is the
    raw material DNS anomaly watch classifies and alerts on."""
    keep = now - FINDINGS_KEEP_DAYS * 86400
    hard, byp = [], []
    for key, v in state.get("seen", {}).items():
        if v["first"] < keep or v["class"] not in BYPASS + ("hardcoded",):
            continue
        d, p, port = key.split("|")
        row = {"key": f"{v['class']}|{key}", "class": v["class"], "dest": d, "proto": p, "port": int(port),
               "first_seen": v["first"], "conns": v.get("conns", 0), "name": v.get("name", ""),
               "later_name": v.get("later_name", ""), "rdns": v.get("rdns", ""), "host": v.get("host", "")}
        (byp if v["class"] in BYPASS else hard).append(row)
    for lst in (hard, byp):
        lst.sort(key=lambda f: -f["first_seen"])
    tot = class_totals(state)
    return {"schema": SCHEMA, "tool": "dns-leak-watch", "generated": now, "since": state.get("since"),
            "clients": [str(n) for n in c["CLIENTS"]], "index_hours": c["INDEX_HOURS"],
            "resolver_list": {"loaded": resolvers is not None, "entries": len(resolvers or [])},
            "counters": counters, "classes": tot,
            "devices": {h: {k: v for k, v in d.items() if k in DEVICE_PUBLIC} for h, d in state.get("devices", {}).items()},
            "shared_address": sum(1 for v in state.get("seen", {}).values() if v.get("shared")),
            "proxy_clients": [str(n) for n in c["PROXY_CLIENTS"]],
            "hardcoded": {"destinations": tot["hardcoded"]["destinations"], "connections": tot["hardcoded"]["connections"],
                          "findings": hard[:500]},
            "bypass_evidence": {"destinations": sum(tot[k]["destinations"] for k in BYPASS),
                                "connections": sum(tot[k]["connections"] for k in BYPASS),
                                "dnslock_redirect_total": state.get("redirect_total", 0),
                                "dnslock_redirect_last_delta": state.get("redirect_delta", 0),
                                "findings": byp[:500]}}


def summary_text(state: dict, counters: dict, now: float, resolvers) -> str:
    seen = state.get("seen", {})
    tot = class_totals(state)
    rows = lambda classes: sorted(((k, v) for k, v in seen.items() if v["class"] in classes), key=lambda kv: -kv[1].get("conns", 0))

    def fmt(k, v):
        d, p, port = k.split("|")
        extra = v.get("name") or (f"(later answered: {v['later_name']})" if v.get("later_name") else "")
        label = "shared-address" if v.get("shared") else v["class"]
        return f"  {v.get('conns', 0):6d}  {_ts(v['first'])}  {label:<14} {p}/{port:<5} {d:<40} {extra} {v.get('rdns', '')}".rstrip()

    nb = sum(tot[k]["destinations"] for k in BYPASS)
    L = [f"dns-leak-watch summary at {_ts(now, '%Y-%m-%d %H:%MZ')} (since {state.get('since', '?')})",
         f"destinations: {len(seen)}   HARD-CODED: {tot['hardcoded']['destinations']}   "
         f"other-client: {tot['other-client']['destinations']}   cached-stale: {tot['cached-stale']['destinations']}   "
         f"normal lookups: {tot['lookup']['destinations']}   (bypass evidence: {nb}, reported by DNS anomaly watch)",
         "new connections counted: " + ", ".join(f"{k[2:] if k.startswith('c_') else k} {v if v is not None else '-'}" for k, v in counters.items()),
         f"encrypted-resolver list: {'%d entries' % len(resolvers) if resolvers is not None else 'NOT LOADED (the resolver timer has not run yet)'}",
         ""] + device_summary(state, now) + [
         f"== HARD-CODED IP: no DNS answer to anyone in the last {state.get('index_hours', '?')} h gave this address"]
    L += [fmt(k, v) for k, v in rows(("hardcoded",))[:80]] or ["  (none)"]
    n_shared = sum(1 for v in seen.values() if v.get("shared"))
    L += ["", f"-- context, never alerted: other-client (answered, but only for other clients' lookups; {n_shared} of them "
              "on a shared CDN/cloud address) and cached-stale (answered for this device before the window)"]
    L += [fmt(k, v) for k, v in rows(("other-client", "cached-stale"))[:60]] or ["  (none)"]
    L += ["", "-- normal lookups, by name (connections)"]
    by = {}
    for k, v in rows(("lookup",)):
        by[v["name"]] = by.get(v["name"], 0) + v.get("conns", 0)
    L += [f"  {n:6d}  {name}" for name, n in sorted(by.items(), key=lambda kv: -kv[1])[:60]]
    return "\n".join(L) + "\n"


def push(c: dict, title: str, msg: str, priority: int = 3, topic: str = ""):
    topic = topic or c["NTFY_TOPIC"]
    if not (c["NTFY_URL"] and topic):
        return
    req = urllib.request.Request(f"{c['NTFY_URL'].rstrip('/')}/{topic}", data=msg.encode()[:3500],
                                 headers={"Title": title, "Priority": str(priority)})
    if c["NTFY_TOKEN_FILE"]:
        try:
            tok = Path(c["NTFY_TOKEN_FILE"]).read_text().strip()
            if tok:
                req.add_header("Authorization", f"Bearer {tok}")
        except OSError:
            pass
    try:
        urllib.request.urlopen(req, timeout=8).close()
    except Exception as e:
        print(f"dns-leak-watch: push failed (non-fatal): {type(e).__name__}")


def _own(path: Path, gid: int):
    if os.geteuid() == 0:                                  # root in production; tests run unprivileged
        os.chown(path, 0, gid)


def _write_private(path: Path, text: str, gid: int):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o640)
    _own(tmp, gid)
    os.replace(tmp, path)


def run(c: dict, now: float | None = None, elements=None, counters=None, resolvers="load", ts_raw=None,
        nft=nft_quarantine, checker=None, local=None, approver=None) -> dict:
    now = now or time.time()
    with _locked(Path(c["OUT_DIR"])):
        return _run(c, now, elements, counters, resolvers, ts_raw, nft, checker, local, approver)


def _run(c, now, elements, counters, resolvers, ts_raw, nft, checker, local, approver) -> dict:
    out = Path(c["OUT_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    gid = grp.getgrnam(c["READ_GROUP"]).gr_gid
    os.chmod(out, 0o750)
    _own(out, gid)
    sp = out / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else {}
    rp = out / "root-state.json"                           # device addresses and the Pi-hole index: root only, 0600
    root = json.loads(rp.read_text()) if rp.exists() else {}
    if "index" in state and "index" not in root:
        root["index"] = state["index"]
    state.pop("index", None)
    first = not state
    state.setdefault("since", _ts(now, "%Y-%m-%d %H:%MZ"))
    state["index_hours"] = c["INDEX_HOURS"]
    if resolvers == "load":
        resolvers = load_resolvers(c["RESOLVER_LIST"])
    idx = Index(root.get("index"))
    update_index(idx, Path(c["PIHOLE_LOG"]), now, first)
    idx.prune(now)
    if elements is None:
        elements = []
        for s in ("seen4", "seen6"):
            elements += parse_set_json(nft_json("list", "set", "inet", TABLE, s))
    if counters is None:
        counters = read_counters(c)
    red = counters.get("dnslock_redirect")
    if red is not None:                                    # the dns-lock table is optional (absent = skipped)
        prev = state.get("redirect_last")
        if prev is not None:
            state["redirect_delta"] = red - prev if red >= prev else red
            state["redirect_total"] = state.get("redirect_total", 0) + state["redirect_delta"]
        state["redirect_last"] = red
    elements = [e if len(e) == 5 else ("",) + tuple(e) for e in elements]   # (src, dest, proto, port, cnt)
    peers, self_addrs = tailscale_peers(c, ts_raw) if (c["TAILNET"] == "yes" or ts_raw is not None) else (None, set())
    devmap = {pe["host"]: pe["addrs"] for pe in peers or []}
    hostof = {a: h for h, al in devmap.items() for a in al}
    new = process(elements, idx, state, c, resolvers, now, devmap, hostof)
    if peers is not None:
        classes = {k: v["class"] for k, v in state.get("seen", {}).items()}
        evaluate_devices(c, state, root, peers, self_addrs, elements, idx.pass_q, classes, now, first,
                         lambda ti, m, pr=3, tp="": push(c, ti, m, pr, tp), nft, checker, local, approver)
    if c["RDNS"] == "yes":
        for cls, d, p, port, _ in [n for n in new if n[0] in BYPASS + ("hardcoded", "other-client")][:20]:
            state["seen"][f"{d}|{p}|{port}"]["rdns"] = rdns(d)
    for cls, d, p, port, name in new:
        if cls == "other-client":
            rec = state["seen"][f"{d}|{p}|{port}"]
            rec["shared"] = is_shared(name, rec.get("rdns", ""))
    day = _ts(now, "%Y-%m-%d")
    csvp = out / f"{day}.csv"
    fresh = not csvp.exists()
    with open(csvp, "a", newline="") as fh:
        w = csv.writer(fh)
        if fresh:
            w.writerow(["first_seen_utc", "class", "proto", "dport", "destination", "pihole_name", "rdns_hint"])
        for cls, d, p, port, name in new:
            w.writerow([_ts(now, "%Y-%m-%dT%H:%M:%SZ"), cls, p, port, d, name, state["seen"][f"{d}|{p}|{port}"].get("rdns", "")])
    os.chmod(csvp, 0o640)
    _own(csvp, gid)
    alerted = set(state.get("alerted", []))
    nh = [n for n in new if n[0] == "hardcoded" and f"{n[0]}|{n[1]}|{n[2]}|{n[3]}" not in alerted]
    if not first:                                          # bypass evidence is DNS anomaly watch's to alert on
        if nh:
            push(c, f"dns-leak-watch: {len(nh)} new hard-coded destination(s)",
                 ", ".join(f"{d} {p}/{port}" for _, d, p, port, _ in nh[:8]) + f". {out}/summary.txt", 3)
        if state.get("digest_day") not in (None, day):
            t = class_totals(state)
            push(c, "dns-leak-watch: daily digest",
                 f"hard-coded {t['hardcoded']['destinations']}, other-client {t['other-client']['destinations']}, "
                 f"{len(state.get('seen', {}))} destinations. {out}/summary.txt", 2)
    alerted |= {f"{n[0]}|{n[1]}|{n[2]}|{n[3]}" for n in new if n[0] == "hardcoded"}
    state["alerted"] = sorted(alerted)
    state["digest_day"] = day
    root["index"] = idx.to_json()
    state["counters"] = counters
    _write_private(out / "summary.txt", summary_text(state, counters, now, resolvers), gid)
    _write_private(out / "latest.json", json.dumps(latest_json(state, c, counters, now, resolvers), indent=1), gid)
    _write_private(sp, json.dumps(state), gid)
    tmp = rp.with_suffix(".tmp")
    tmp.write_text(json.dumps(root))
    os.chmod(tmp, 0o600)
    os.replace(tmp, rp)
    return {"new": len(new), "bypass_evidence": len([n for n in new if n[0] in BYPASS]),
            "hardcoded": len([n for n in new if n[0] == "hardcoded"]), "destinations": len(state["seen"]), "first_run": first}


# --------------------------------------------------------------------------------- daily resolver refresh --
def default_iface() -> str:
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    parts = (out.splitlines() or [""])[0].split()
    return parts[parts.index("dev") + 1] if "dev" in parts else ""


def refresh_resolvers(c: dict, fetch=None, iface=None) -> int:
    """Fetch RESOLVER_LIST_URL, validate it, replace RESOLVER_LIST atomically (0644). Offline-tolerant: on any failure
    the last good list stays. Skips on a metered default route (METERED_IFACES). Returns 0 updated/unchanged/skipped,
    1 refused or failed (the old list kept)."""
    import hashlib
    dest = Path(c["RESOLVER_LIST"])
    iface = default_iface() if iface is None else iface
    metered = {x for x in re.split(r"[ ,]+", c["METERED_IFACES"]) if x}
    if iface and iface in metered:
        print(f"dns-leak-watch: resolver refresh skipped: the default route ({iface}) is metered; list kept")
        return 0
    try:
        body = (fetch or (lambda u: urllib.request.urlopen(u, timeout=30).read()))(c["RESOLVER_LIST_URL"])
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    except Exception as e:
        print(f"dns-leak-watch: resolver refresh failed ({type(e).__name__}); last good list kept")
        return 1
    nets, bad = parse_resolvers(text)
    if len(nets) < c["RESOLVER_MIN"]:
        print(f"dns-leak-watch: resolver refresh REFUSED: {len(nets)} entries (< RESOLVER_MIN {c['RESOLVER_MIN']}); list kept")
        return 1
    clean = "".join(f"{n}\n" for n in nets)
    sha = hashlib.sha256(clean.encode()).hexdigest()
    if dest.exists() and dest.read_text() == clean:
        print(f"dns-leak-watch: resolver list unchanged ({len(nets)} entries, sha256 {sha[:16]})")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(clean)
    os.chmod(tmp, 0o644)
    if os.geteuid() == 0:
        os.chown(tmp, 0, 0)
    os.replace(tmp, dest)
    print(f"dns-leak-watch: resolver list updated: {len(nets)} entries ({bad} skipped), sha256 {sha[:16]}")
    return 0


# ------------------------------------------------------------------------------------------------ diagnose --
def local_addresses() -> set:
    try:
        out = subprocess.run(["ip", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return set()
    return {f.split("/")[0] for line in out.splitlines() for i, f in enumerate(line.split()) if i == 3}


def diagnose_counts(lines, c: dict, local: set) -> dict:
    """Which kind of client address Pi-hole logs queries from: the watched device, a configured proxy, this host
    (loopback / one of its own addresses), or something else. Counts only; no address is returned."""
    watched, proxy = _mine_fn(c["CLIENTS"]), _mine_fn(c["PROXY_CLIENTS"])
    out = {"watched device (CLIENTS)": 0, "proxy (PROXY_CLIENTS)": 0, "this host (loopback / own address)": 0, "other": 0}
    for line in lines:
        m = QRY.search(line)
        if not m:
            continue
        cl = m.group(2)
        if watched(cl):
            out["watched device (CLIENTS)"] += 1
        elif proxy(cl):
            out["proxy (PROXY_CLIENTS)"] += 1
        elif cl in local or cl.startswith("127.") or cl == "::1":
            out["this host (loopback / own address)"] += 1
        else:
            out["other"] += 1
    return out


def diagnose(c: dict, seconds: int) -> int:
    log = Path(c["PIHOLE_LOG"])
    start = log.stat().st_size if log.exists() else 0
    c0 = counter_packets(f"inet {TABLE} c_new")
    print(f"dns-leak-watch: diagnose: use the watched device now; watching Pi-hole's log for {seconds} s ...", flush=True)
    time.sleep(seconds)
    with open(log, errors="replace") as fh:
        fh.seek(start)
        counts = diagnose_counts(fh, c, local_addresses())
    c1 = counter_packets(f"inet {TABLE} c_new")
    print(f"   new connections from the device in the window: {(c1 - c0) if None not in (c0, c1) else 'n/a (table not loaded)'}")
    for k, v in counts.items():
        print(f"   queries from {k:<38} {v}")
    if counts["watched device (CLIENTS)"] == 0 and counts["this host (loopback / own address)"] + counts["proxy (PROXY_CLIENTS)"] > 0:
        print("   -> the device's DNS reaches Pi-hole through this host (exit-node DNS proxy): put this host's loopback and "
              "tailnet addresses in PROXY_CLIENTS, or other-client will be over-reported")
    elif counts["watched device (CLIENTS)"] > 0:
        print("   -> Pi-hole sees the device's own address: PROXY_CLIENTS can stay empty")
    else:
        print("   -> no queries seen: use the device during the window, or check that its DNS reaches this Pi-hole")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["render-nft", "run", "status", "refresh-resolvers", "check-resolvers", "diagnose", "release"])
    ap.add_argument("--conf", default="/etc/ctdc-dnsleak/dnsleak.conf")
    ap.add_argument("--file")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--host")
    a = ap.parse_args(argv)
    if a.cmd == "check-resolvers":
        nets, bad = parse_resolvers(Path(a.file).read_text())
        print(f"dns-leak-watch: resolver list: {len(nets)} entries, {bad} unparsable line(s) skipped")
        return 0 if len(nets) >= 10 else 1
    try:
        c = parse_conf(Path(a.conf).read_text())
    except (OSError, ConfError) as e:
        print(f"dns-leak-watch: config: {e}", file=sys.stderr)
        return 2
    if a.cmd == "render-nft":
        sys.stdout.write(render_nft(c))
        return 0
    if a.cmd == "refresh-resolvers":
        return refresh_resolvers(c)
    if a.cmd == "release":
        if not a.host or not (a.host == "--all" or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", a.host)):
            print("dns-leak-watch: release needs --host NAME, or --host --all", file=sys.stderr)
            return 2
        return release(c, a.host)
    if a.cmd == "diagnose":
        return diagnose(c, max(10, min(a.seconds, 1800)))
    if a.cmd == "status":
        sp = Path(c["OUT_DIR"]) / "state.json"
        st = json.loads(sp.read_text()) if sp.exists() else {}
        t = class_totals(st)
        print(f"dns-leak-watch: {len(st.get('seen', {}))} destinations; hard-coded {t['hardcoded']['destinations']}; "
              f"bypass evidence {sum(t[k]['destinations'] for k in BYPASS)}; since {st.get('since', '-')}")
        return 0
    r = run(c)
    print(f"dns-leak-watch: {r['new']} new destination(s): {r['hardcoded']} hard-coded, {r['bypass_evidence']} bypass evidence; "
          f"{r['destinations']} total" + (" (first run: baseline, no push)" if r["first_run"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
