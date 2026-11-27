#!/usr/bin/env python3
"""blocklist_merge.py -- 2026-11-27: one merged Blocklist Project list for Pi-hole, next to HaGeZi, with dead
registrable domains pruned by evidence (docs/BLOCKLIST_MERGE.md).

The problem it solves: Blocklist Project (BLP) and HaGeZi overlap only a little (2026-11-27: 17% of BLP's
malware/abuse/fraud/phishing/ads/crypto/ransomware entries are covered by HaGeZi Pro + TIF), so dropping BLP for
HaGeZi loses ~2.7M blocked names, of which a 2,000-name sample still resolved 37.5%. Keeping both kept 5.3M gravity
entries. This tool keeps BOTH and makes BLP smaller in three evidence-based ways, never by guessing:
  1 covered    a BLP entry HaGeZi already blocks (the name itself, or a parent under HaGeZi's wildcard) is dropped
  2 folded     a BLP entry under another BLP entry is written once, as ||parent^ (Pi-hole: parent + subdomains);
               never for a shared platform (PLATFORM_MIN or more listed subdomains under one registrable domain)
  3 pruned     every entry under a registrable domain that does not EXIST (NXDOMAIN from its parent zone, asked
               for its DS record, PRUNE_CONFIRM times at least PRUNE_MIN_DAYS apart) is left out; it comes back the
               moment one check finds the domain registered again, or a device here looks it up and it is alive
Nothing is deleted: every BLP-only entry is archived (monthly snapshot + daily added/removed diffs), and the checks
are a longitudinal record (state transitions with dates), the evidence for upstream reports the operator files.

  blocklist_merge.py build --conf FILE        daily (root): fetch, merge, write the Pi-hole lists, archive, report
  blocklist_merge.py check --conf FILE        hourly (User=unbound): DS lookups for a budget of registrable domains
  blocklist_merge.py selftest --conf FILE     positive controls must be blocked by Pi-hole, negative ones NXDOMAIN
  blocklist_merge.py contrib --conf FILE      draft upstream reports (dead BLP entries; HaGeZi candidates)
  blocklist_merge.py status --conf FILE
The checker runs as the unbound user so the DNS lock (only Unbound reaches outside DNS) lets it out; it has its own
libunbound resolver and cache, so millions of lookups never churn the production Unbound cache. A DS query is
answered by the PARENT zone (the TLD), so a dead or live malicious domain's own nameservers never see these checks.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gzip
import hashlib
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = 1
BLP_ALL = ("abuse", "ads", "crypto", "fraud", "malware", "phishing", "ransomware", "porn", "gambling", "redirect",
           "drugs", "facebook", "tiktok", "piracy", "twitter", "whatsapp")
KEYS = {"BLP_LISTS", "BLP_URL", "HAGEZI_LISTS", "HAGEZI_URL", "PSL_URL", "OUT_DIR", "LIST_DIR", "CHECK_DIR",
        "READ_GROUP", "PLATFORM_MIN", "PRUNE_CONFIRM", "PRUNE_MIN_DAYS", "DEAD_RECHECK_DAYS", "CHECK_PER_DAY",
        "CHECK_QPS", "LOAD_MAX", "METERED_IFACES", "GRAVITY_AFTER_BUILD", "GRAVITY_LOAD_MAX", "PIHOLE_LOG",
        "PIHOLE_DNS", "UNBOUND_DNS", "MIN_ENTRIES", "NTFY_URL", "NTFY_TOPIC", "NTFY_TOKEN_FILE"}
DEFAULTS = {"BLP_LISTS": " ".join(BLP_ALL), "BLP_URL": "https://blocklistproject.github.io/Lists/{}.txt",
            "HAGEZI_LISTS": "pro tif doh",
            "HAGEZI_URL": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/{}.txt",
            "PSL_URL": "https://publicsuffix.org/list/public_suffix_list.dat",
            "OUT_DIR": "/var/lib/ctdc-blmerge", "LIST_DIR": "/etc/pihole/ctdc-merged",
            "CHECK_DIR": "/var/lib/ctdc-blmerge-check", "READ_GROUP": "root", "PLATFORM_MIN": "100",
            "PRUNE_CONFIRM": "2", "PRUNE_MIN_DAYS": "7", "DEAD_RECHECK_DAYS": "30", "CHECK_PER_DAY": "100000",
            "CHECK_QPS": "20", "LOAD_MAX": "20", "METERED_IFACES": "", "GRAVITY_AFTER_BUILD": "yes",
            "GRAVITY_LOAD_MAX": "12", "PIHOLE_LOG": "/var/log/pihole/pihole.log", "PIHOLE_DNS": "127.0.0.1#53",
            "UNBOUND_DNS": "127.0.0.1#5335", "MIN_ENTRIES": "100000", "NTFY_URL": "", "NTFY_TOPIC": "",
            "NTFY_TOKEN_FILE": ""}
HOSTS_FILE, ABP_FILE = "blp-merged-hosts.txt", "blp-merged-abp.txt"
DOM = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1?)?\s*([a-z0-9_][a-z0-9_.-]*\.[a-z0-9-]{2,})\s*(?:#.*)?$")
QRY = re.compile(r"\bquery\[[A-Za-z0-9]+\]\s+(\S+)\s+from\s+\S+")


class ConfError(ValueError):
    pass


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
    c["BLP_LISTS"] = [x for x in c["BLP_LISTS"].split() if x]
    c["HAGEZI_LISTS"] = [x for x in c["HAGEZI_LISTS"].split() if x]
    if not c["BLP_LISTS"] or any(not re.fullmatch(r"[a-z0-9-]{1,40}", x) for x in c["BLP_LISTS"] + c["HAGEZI_LISTS"]):
        raise ConfError("BLP_LISTS / HAGEZI_LISTS: list names (letters, digits, -), BLP_LISTS not empty")
    for k in ("BLP_URL", "HAGEZI_URL"):
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._/~-]*\{\}[A-Za-z0-9._-]*", c[k]):
            raise ConfError(f"{k}: an https URL with one {{}} for the list name")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._/~-]*)?", c["PSL_URL"]):
        raise ConfError("PSL_URL: an https URL")
    for k in ("OUT_DIR", "LIST_DIR", "CHECK_DIR", "PIHOLE_LOG"):
        if not c[k].startswith("/") or ".." in c[k].split("/"):
            raise ConfError(f"{k}: an absolute path")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", c["READ_GROUP"]):
        raise ConfError("READ_GROUP: a group name")
    for k, lo, hi in (("PLATFORM_MIN", 2, 100000), ("PRUNE_CONFIRM", 2, 10), ("PRUNE_MIN_DAYS", 1, 365),
                      ("DEAD_RECHECK_DAYS", 1, 365), ("CHECK_PER_DAY", 0, 2000000), ("CHECK_QPS", 1, 200),
                      ("LOAD_MAX", 1, 100), ("GRAVITY_LOAD_MAX", 1, 100), ("MIN_ENTRIES", 1, 50000000)):
        if not re.fullmatch(r"\d{1,8}", c[k]) or not lo <= int(c[k]) <= hi:
            raise ConfError(f"{k}: {lo}-{hi}")
        c[k] = int(c[k])
    if c["GRAVITY_AFTER_BUILD"] not in ("yes", "no"):
        raise ConfError("GRAVITY_AFTER_BUILD: yes or no")
    for k in ("PIHOLE_DNS", "UNBOUND_DNS"):
        if not re.fullmatch(r"127\.0\.0\.1#\d{1,5}", c[k]):
            raise ConfError(f"{k}: 127.0.0.1#PORT (loopback only)")
    if c["METERED_IFACES"] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}([ ,][A-Za-z0-9_.-]{1,15})*", c["METERED_IFACES"]):
        raise ConfError("METERED_IFACES: interface names")
    if c["NTFY_URL"] and not re.fullmatch(r"https?://[A-Za-z0-9.:\[\]_-]+(/[A-Za-z0-9._/-]*)?", c["NTFY_URL"]):
        raise ConfError("NTFY_URL: http(s)://host[:port]")
    if c["NTFY_TOPIC"] and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", c["NTFY_TOPIC"]):
        raise ConfError("NTFY_TOPIC: letters, digits, - and _")
    return c


# ------------------------------------------------------------------------------------------ names and PSL --
def domains(text: str) -> set:
    """Hosts-format ("0.0.0.0 name"), plain, wildcard ("*.name") and ABP ("||name^") lines -> lower-case names."""
    out = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if line.startswith("*."):
            line = line[2:]
        elif line.startswith("||"):
            line = line[2:].split("^", 1)[0]
        m = DOM.match(line)
        if m and m.group(1) not in ("localhost", "localhost.localdomain", "0.0.0.0"):
            out.add(m.group(1).rstrip("."))
    return out


class PSL:
    """Public Suffix List (ICANN and private sections): registrable(name) = the eTLD+1, or '' for a bare suffix."""

    def __init__(self, text: str):
        self.rules, self.exc, self.wild = set(), set(), set()
        for l in text.splitlines():
            l = l.strip().lower()
            if not l or l.startswith("//"):
                continue
            l = l.split()[0]
            if l.startswith("!"):
                self.exc.add(l[1:])
            elif l.startswith("*."):
                self.wild.add(l[2:])
            else:
                self.rules.add(l)

    def suffix_len(self, labels) -> int:
        n = len(labels)
        for i in range(n):
            s = ".".join(labels[i:])
            if s in self.exc:
                return n - i - 1
            if s in self.rules:
                return n - i
            if i + 1 < n and ".".join(labels[i + 1:]) in self.wild:
                return n - i
        return 1

    def registrable(self, name: str) -> str:
        p = name.split(".")
        k = self.suffix_len(p)
        return ".".join(p[-(k + 1):]) if len(p) > k else ""


def covered(name: str, wild: set) -> bool:
    p = name.split(".")
    return any(".".join(p[i:]) in wild for i in range(len(p) - 1))


# ------------------------------------------------------------------------------------------------ merge --
def merge(src: dict, hz: set, psl: PSL, dead: set, platform_min: int):
    """src: name -> {BLP list names}; hz: HaGeZi wildcard names; dead: pruned registrable domains.
    Returns (abp_parents, exact, stats, rest) where rest = BLP entries HaGeZi does not cover (the archive set)."""
    rest = {d for d in src if not covered(d, hz)}
    reg = {d: psl.registrable(d) for d in rest}
    per_reg = Counter(reg.values())
    platform = {r for r, n in per_reg.items() if r and n >= platform_min}
    live = {d for d in rest if reg[d] and reg[d] not in dead}
    pruned = len(rest) - len(live) - sum(1 for d in rest if not reg[d])
    def parent(d):
        p = d.split(".")
        for i in range(1, len(p) - 1):
            a = ".".join(p[i:])
            if a in live and a not in platform and reg.get(a):
                return a
        return None
    par = {d: parent(d) for d in live}
    parents = {a for a in par.values() if a}
    exact = {d for d in live if not par[d] and d not in parents}
    stats = {"blp_entries": len(src), "hagezi_covered": len(src) - len(rest), "blp_only": len(rest),
             "pruned_dead": pruned, "bare_suffix_dropped": sum(1 for d in rest if not reg[d]),
             "folded_children": sum(1 for d in live if par[d]), "abp_parents": len(parents), "exact": len(exact),
             "merged_entries": len(parents) + len(exact), "platforms": len(platform), "registrables": len(per_reg)}
    return parents, exact, stats, rest, reg


# ----------------------------------------------------------------------------------------------- helpers --
def _fetch_cached(url: str, cache: Path, fetch=None):
    cp = cache / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".txt")
    try:
        body = (fetch or (lambda u: urllib.request.urlopen(u, timeout=180).read()))(url)
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        cache.mkdir(parents=True, exist_ok=True)
        cp.write_text(text)
        return text, True
    except Exception as e:
        print(f"blocklist-merge: fetch failed ({type(e).__name__}) {url.split('/')[2]}: " + ("last good copy used" if cp.exists() else "no copy"))
        return (cp.read_text() if cp.exists() else None), False


def default_iface() -> str:
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    parts = (out.splitlines() or [""])[0].split()
    return parts[parts.index("dev") + 1] if "dev" in parts else ""


def metered(c: dict, iface=None) -> str:
    iface = default_iface() if iface is None else iface
    return iface if iface and iface in {x for x in re.split(r"[ ,]+", c["METERED_IFACES"]) if x} else ""


def load1() -> float:
    try:
        return float(Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0


def _gid(c):
    import grp
    try:
        return grp.getgrnam(c["READ_GROUP"]).gr_gid
    except KeyError:
        return os.getgid()


def write_atomic(path: Path, text: str, mode=0o640, gid=None, gz=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if gz:
        with gzip.open(tmp, "wt") as fh:
            fh.write(text)
    else:
        tmp.write_text(text)
    os.chmod(tmp, mode)
    if os.geteuid() == 0:
        os.chown(tmp, 0, 0 if gid is None else gid)
    os.replace(tmp, path)


def push(c: dict, title: str, msg: str, priority: int = 3):
    if not (c["NTFY_URL"] and c["NTFY_TOPIC"]):
        return
    req = urllib.request.Request(f"{c['NTFY_URL'].rstrip('/')}/{c['NTFY_TOPIC']}", data=msg.encode()[:3500],
                                 headers={"Title": title, "Priority": str(priority)})
    if c["NTFY_TOKEN_FILE"]:
        with contextlib.suppress(OSError):
            tok = Path(c["NTFY_TOKEN_FILE"]).read_text().strip()
            if tok:
                req.add_header("Authorization", f"Bearer {tok}")
    try:
        urllib.request.urlopen(req, timeout=8).close()
    except Exception as e:
        print(f"blocklist-merge: push failed (non-fatal): {type(e).__name__}")


def _ts(t: float, fmt="%Y-%m-%d") -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime(fmt)


# --------------------------------------------------------------------------------------- check results db --
def results_db(path: Path, create: bool = False):
    if not create and not path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path) if create else f"file:{path}?mode=ro", uri=not create, timeout=30)
    if create:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS reg (name TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'unchecked',
                nx_count INTEGER NOT NULL DEFAULT 0, first_nx REAL, last_nx REAL, last_alive REAL, last_check REAL,
                last_rcode TEXT, checks INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS transitions (name TEXT NOT NULL, ts REAL NOT NULL, from_state TEXT, to_state TEXT);
            CREATE INDEX IF NOT EXISTS reg_due ON reg (priority DESC, last_check);
            CREATE INDEX IF NOT EXISTS tr_ts ON transitions (ts);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);""")
    return db


def dead_set(c: dict) -> set:
    db = results_db(Path(c["CHECK_DIR"]) / "results.sqlite")
    if db is None:
        return set()
    with contextlib.closing(db):
        return {r[0] for r in db.execute("SELECT name FROM reg WHERE state = 'dead'")}


def observe(db, name: str, rcode: str, now: float, c: dict):
    """One DS check result. NXDOMAIN -> nx-pending, then dead after PRUNE_CONFIRM results PRUNE_MIN_DAYS apart;
    NOERROR -> alive (a dead one coming back is 'revived'); anything else (SERVFAIL, timeout) changes nothing."""
    row = db.execute("SELECT state, nx_count, first_nx FROM reg WHERE name = ?", (name,)).fetchone()
    state, nxc, first_nx = row if row else ("unchecked", 0, None)
    new = state
    if rcode == "NXDOMAIN":
        nxc, first_nx = nxc + 1, first_nx or now
        new = "dead" if nxc >= c["PRUNE_CONFIRM"] and now - first_nx >= c["PRUNE_MIN_DAYS"] * 86400 else (
            "dead" if state == "dead" else "nx-pending")
        db.execute("""INSERT INTO reg (name, state, nx_count, first_nx, last_nx, last_check, last_rcode, checks, priority)
                      VALUES (?,?,?,?,?,?,?,1,0) ON CONFLICT(name) DO UPDATE SET state=excluded.state,
                      nx_count=excluded.nx_count, first_nx=excluded.first_nx, last_nx=excluded.last_nx,
                      last_check=excluded.last_check, last_rcode=excluded.last_rcode, checks=checks+1, priority=0""",
                   (name, new, nxc, first_nx, now, now, rcode))
    elif rcode == "NOERROR":
        new = "revived" if state == "dead" else "alive"
        db.execute("""INSERT INTO reg (name, state, nx_count, last_alive, last_check, last_rcode, checks, priority)
                      VALUES (?,?,0,?,?,?,1,0) ON CONFLICT(name) DO UPDATE SET state=excluded.state, nx_count=0,
                      first_nx=NULL, last_alive=excluded.last_alive, last_check=excluded.last_check,
                      last_rcode=excluded.last_rcode, checks=checks+1, priority=0""", (name, new, now, now, rcode))
    else:
        db.execute("""INSERT INTO reg (name, last_check, last_rcode, checks) VALUES (?,?,?,1) ON CONFLICT(name)
                      DO UPDATE SET last_check=excluded.last_check, last_rcode=excluded.last_rcode, checks=checks+1""",
                   (name, now, rcode))
    if new != state:
        db.execute("INSERT INTO transitions VALUES (?,?,?,?)", (name, now, state, new))
    return new


def due(db, limit: int, now: float, c: dict) -> list:
    """Highest priority first (a pruned domain a device just looked up), then never checked, then nx-pending whose
    confirmation is due, then dead ones every DEAD_RECHECK_DAYS, then the oldest alive check."""
    q = """SELECT name FROM reg WHERE
             priority > 0 OR state = 'unchecked'
             OR (state = 'nx-pending' AND last_check <= :pend)
             OR (state = 'dead' AND last_check <= :dead)
             OR (state IN ('alive', 'revived'))
           ORDER BY priority DESC, (state = 'unchecked') DESC, (state = 'nx-pending') DESC, (state = 'dead') DESC,
                    last_check ASC LIMIT :lim"""
    return [r[0] for r in db.execute(q, {"pend": now - c["PRUNE_MIN_DAYS"] * 86400,
                                          "dead": now - c["DEAD_RECHECK_DAYS"] * 86400, "lim": limit})]


# ------------------------------------------------------------------------------------------------- check --
def ds_resolver(c: dict):
    """A private libunbound context: full recursion from the root, its own cache, nothing shared with Unbound."""
    import unbound
    ctx = unbound.ub_ctx()
    with contextlib.suppress(Exception):
        ctx.set_option("qname-minimisation:", "yes")

    def resolve_many(names, qps):
        out, inflight, i = {}, {}, 0
        gap = 1.0 / qps

        def cb(name, status, result):
            inflight.pop(name, None)
            if status != 0 or result is None:
                out[name] = "ERROR"
            elif result.nxdomain:
                out[name] = "NXDOMAIN"
            elif result.rcode == 0:
                out[name] = "NOERROR"
            else:
                out[name] = {2: "SERVFAIL", 5: "REFUSED"}.get(result.rcode, f"RCODE{result.rcode}")
        while i < len(names) or inflight:
            if i < len(names) and len(inflight) < qps * 3:
                n = names[i]
                inflight[n] = time.time()
                ctx.resolve_async(n, n, cb, unbound.RR_TYPE_DS, unbound.RR_CLASS_IN)
                i += 1
                time.sleep(gap)
            ctx.process()
            for n, t in list(inflight.items()):
                if time.time() - t > 20:
                    inflight.pop(n, None)
                    out[n] = "TIMEOUT"
            if i >= len(names):
                time.sleep(0.05)
        return out
    return resolve_many


def check(c: dict, now: float | None = None, resolver=None, budget: int | None = None, load=None, iface=None) -> dict:
    """One hourly pass (User=unbound): import the build's registrable queue and priorities, check a budget of due
    names with DS lookups, record results. Skips (no lookups) on a metered route or above LOAD_MAX."""
    now = now or time.time()
    m = metered(c, iface)
    if m:
        print(f"blocklist-merge: check skipped: the default route ({m}) is metered")
        return {"skipped": "metered"}
    l1 = load1() if load is None else load
    if l1 > c["LOAD_MAX"]:
        print(f"blocklist-merge: check skipped: load {l1:.1f} > LOAD_MAX {c['LOAD_MAX']}")
        return {"skipped": "load"}
    cd = Path(c["CHECK_DIR"])
    db = results_db(cd / "results.sqlite", create=True)
    with contextlib.closing(db):
        q = cd / "queue.txt.gz"
        qs = q.stat().st_mtime if q.exists() else 0
        last = db.execute("SELECT value FROM meta WHERE key = 'queue_mtime'").fetchone()
        if q.exists() and qs > float(last[0] if last else 0):                 # a new queue from the daily build
            with gzip.open(q, "rt") as fh:
                rows = [l.rstrip("\n").split("\t") for l in fh if l.strip() and not l.startswith("#")]
            db.executemany("INSERT OR IGNORE INTO reg (name) VALUES (?)", ((r[0],) for r in rows))
            db.executemany("UPDATE reg SET priority = 1 WHERE name = ?", ((r[0],) for r in rows if len(r) > 1 and r[1] == "1"))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('queue_mtime', ?)", (str(qs),))
            db.commit()
        n = budget if budget is not None else max(1, c["CHECK_PER_DAY"] // 24)
        names = due(db, n, now, c)
        res = (resolver or ds_resolver(c))(names, c["CHECK_QPS"]) if names else {}
        tally = Counter()
        for name in names:
            r = res.get(name, "TIMEOUT")
            tally[observe(db, name, r, now, c) + ":" + r] += 1
        db.commit()
    os.chmod(cd / "results.sqlite", 0o644)
    print(f"blocklist-merge: checked {len(names)}: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    return {"checked": len(names), "tally": dict(tally)}


# ------------------------------------------------------------------------------------------------- build --
def seen_pruned(c: dict, dead: set, psl: PSL) -> set:
    """Registrable domains currently pruned that a device here looked up (yesterday's and today's Pi-hole log):
    they go to the front of the check queue, and come back into the list the moment a check finds them alive."""
    out = set()
    for p in (Path(c["PIHOLE_LOG"] + ".1"), Path(c["PIHOLE_LOG"])):
        with contextlib.suppress(OSError), open(p, errors="replace") as fh:
            for line in fh:
                m = QRY.search(line)
                if m:
                    r = psl.registrable(m.group(1).rstrip(".").lower())
                    if r in dead:
                        out.add(r)
    return out


def lists_of(mask, names) -> str:
    if isinstance(mask, (set, frozenset, list, tuple)):
        return ",".join(sorted(mask))
    return ",".join(n for i, n in enumerate(names) if mask >> i & 1)


def archive(c: dict, rest: set, src: dict, reg: dict, now: float, names=()) -> str:
    """Monthly full snapshot of the BLP-only set, daily added/removed diffs against the previous build."""
    ad = Path(c["OUT_DIR"]) / "archive"
    ad.mkdir(parents=True, exist_ok=True)
    gid = _gid(c)
    day = _ts(now)
    prev_p = Path(c["OUT_DIR"]) / "blp-only-current.txt.gz"
    prev = set()
    if prev_p.exists():
        with gzip.open(prev_p, "rt") as fh:
            prev = {l.split("\t")[0] for l in fh if l.strip() and not l.startswith("#")}
    row = lambda d: f"{d}\t{lists_of(src.get(d, 0), names)}\t{reg.get(d, '')}\n"
    head = f"# BLP-only entries (not covered by HaGeZi) {day}: name, BLP list(s), registrable domain\n"
    added, removed = sorted(rest - prev), sorted(prev - rest)
    if prev and (added or removed):                       # the first build has only the snapshot
        write_atomic(ad / f"{day}-added.tsv.gz", head + "".join(row(d) for d in added), gid=gid, gz=True)
        write_atomic(ad / f"{day}-removed.tsv.gz", head + "".join(f"{d}\n" for d in removed), gid=gid, gz=True)
    snap = ad / f"{day[:7]}-snapshot.tsv.gz"
    if not snap.exists():
        write_atomic(snap, head + "".join(row(d) for d in sorted(rest)), gid=gid, gz=True)
    write_atomic(prev_p, head + "".join(row(d) for d in sorted(rest)), gid=gid, gz=True)
    return f"{len(added)} added, {len(removed)} removed since the last build" if prev else f"first snapshot, {len(rest)} entries"


def build(c: dict, fetch=None, now: float | None = None, iface=None, gravity=None, load=None) -> int:
    now = now or time.time()
    out = Path(c["OUT_DIR"])
    cache = out / "sources"
    m = metered(c, iface)
    if m:
        print(f"blocklist-merge: build skipped: the default route ({m}) is metered; the lists Pi-hole has stay")
        return 0
    src, ok = {}, True                                     # name -> bitmask of BLP_LISTS (a set per name cost ~1 GB)
    for i, n in enumerate(c["BLP_LISTS"]):
        t, fresh = _fetch_cached(c["BLP_URL"].format(n), cache, fetch)
        ok &= fresh
        bit = 1 << i
        for d in domains(t or ""):
            src[d] = src.get(d, 0) | bit
        del t
    hz = set()
    for n in c["HAGEZI_LISTS"]:
        t, fresh = _fetch_cached(c["HAGEZI_URL"].format(n), cache, fetch)
        ok &= fresh
        hz |= domains(t or "")
    pt, _ = _fetch_cached(c["PSL_URL"], cache, fetch)
    if not pt:
        print("blocklist-merge: no Public Suffix List (fetch failed, no copy); build refused, the lists Pi-hole has stay")
        return 1
    if len(src) < c["MIN_ENTRIES"]:
        print(f"blocklist-merge: build REFUSED: {len(src)} BLP entries (< MIN_ENTRIES {c['MIN_ENTRIES']}); the lists Pi-hole has stay")
        return 1
    psl = PSL(pt)
    dead = dead_set(c)
    seen = seen_pruned(c, dead, psl)
    parents, exact, stats, rest, reg = merge(src, hz, psl, dead, c["PLATFORM_MIN"])
    stats["seen_while_pruned"] = len(seen)
    day = _ts(now, "%Y-%m-%d %H:%MZ")
    hdr = (f"# ctdc merged Blocklist Project list ({{kind}}), built {day} by blocklist_merge.py (docs/BLOCKLIST_MERGE.md)\n"
           f"# sources: Blocklist Project {', '.join(c['BLP_LISTS'])}; minus HaGeZi {', '.join(c['HAGEZI_LISTS'])}; "
           f"{stats['pruned_dead']} entries under {len(dead)} non-existent registrable domains left out (archived)\n")
    ld = Path(c["LIST_DIR"])
    hosts_text = hdr.format(kind="exact names") + "".join(f"{d}\n" for d in sorted(exact))
    abp_text = "[Adblock Plus]\n" + hdr.format(kind="parent + subdomains").replace("# ", "! ") + "".join(f"||{p}^\n" for p in sorted(parents))
    changed = False
    for name, text in ((HOSTS_FILE, hosts_text), (ABP_FILE, abp_text)):
        p = ld / name
        body = lambda t: "".join(l for l in t.splitlines(True) if not l.startswith(("#", "!")))
        if not p.exists() or body(p.read_text()) != body(text):
            write_atomic(p, text, mode=0o644)
            changed = True
    arch = archive(c, rest, src, reg, now, c["BLP_LISTS"])
    cd = Path(c["CHECK_DIR"])
    regs = sorted({r for r in reg.values() if r})
    write_atomic(cd / "queue.txt.gz", "# registrable\tpriority\n" + "".join(f"{r}\t{1 if r in seen else 0}\n" for r in regs),
                 mode=0o644, gz=True)
    g = "not needed"
    if changed:
        l1 = load1() if load is None else load
        if c["GRAVITY_AFTER_BUILD"] == "yes" and l1 <= c["GRAVITY_LOAD_MAX"]:
            r = (gravity or (lambda: subprocess.run(["nice", "-n", "19", "pihole", "-g"], capture_output=True, text=True, timeout=3600).returncode))()
            g = "ran" if r == 0 else f"FAILED (rc {r}; Pi-hole keeps its previous gravity)"
        else:
            g = f"pending (load {l1:.1f} > GRAVITY_LOAD_MAX {c['GRAVITY_LOAD_MAX']}, or GRAVITY_AFTER_BUILD=no): Pi-hole picks the lists up at its next gravity run"
    stats.update({"generated": now, "schema": SCHEMA, "hagezi_entries": len(hz), "gravity": g, "archive": arch,
                  "sources_fresh": ok, "gravity_estimate": stats["merged_entries"] + len(hz)})
    write_atomic(out / "latest.json", json.dumps(stats, indent=1), gid=_gid(c))
    write_atomic(out / "report.txt", report_text(c, stats), gid=_gid(c))
    print(f"blocklist-merge: merged list {stats['merged_entries']:,} entries ({stats['abp_parents']:,} ||parent^ + "
          f"{stats['exact']:,} exact) from {stats['blp_entries']:,} BLP entries; HaGeZi covers {stats['hagezi_covered']:,}; "
          f"pruned {stats['pruned_dead']:,} under {len(dead):,} dead domains; archive: {arch}; gravity {g}")
    if seen:
        push(c, f"blocklist-merge: {len(seen)} pruned domain(s) looked up here",
             f"{', '.join(sorted(seen)[:5])}{' ...' if len(seen) > 5 else ''}: moved to the front of the check queue; "
             f"back in the list if alive. {out}/report.txt", 3)
    return 0 if ok else 1


def report_text(c: dict, s: dict) -> str:
    db = results_db(Path(c["CHECK_DIR"]) / "results.sqlite")
    states, trans = Counter(), Counter()
    if db is not None:
        with contextlib.closing(db):
            states = Counter(dict(db.execute("SELECT state, count(*) FROM reg GROUP BY state").fetchall()))
            trans = Counter({f"{a}->{b}": n for a, b, n in db.execute(
                "SELECT from_state, to_state, count(*) FROM transitions WHERE ts > ? GROUP BY 1, 2", (time.time() - 30 * 86400,))})
    L = [f"blocklist-merge report {_ts(s['generated'], '%Y-%m-%d %H:%MZ')}",
         f"Blocklist Project entries {s['blp_entries']:,}; covered by HaGeZi {s['hagezi_covered']:,}; BLP-only {s['blp_only']:,}",
         f"merged list {s['merged_entries']:,} = {s['abp_parents']:,} ||parent^ (folding {s['folded_children']:,} children) + {s['exact']:,} exact",
         f"pruned (registrable domain does not exist, confirmed) {s['pruned_dead']:,}; shared platforms never folded {s['platforms']:,}",
         f"gravity estimate (merged + HaGeZi) {s['gravity_estimate']:,}; gravity: {s['gravity']}",
         f"archive: {s['archive']}; pruned domains looked up here since yesterday: {s['seen_while_pruned']}",
         "",
         f"registrable-domain checks ({s['registrables']:,} registrable domains): "
         + ", ".join(f"{k} {v:,}" for k, v in sorted(states.items())),
         "transitions, last 30 days: " + (", ".join(f"{k} {v:,}" for k, v in sorted(trans.items())) or "none")]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------- self-test --
def dig_status(server: str, name: str) -> tuple[str, list]:
    host, port = server.split("#")
    try:
        r = subprocess.run(["dig", "+tries=1", "+time=3", "-p", port, f"@{host}", name, "A"], capture_output=True,
                           text=True, timeout=10).stdout
    except Exception:
        return "TIMEOUT", []
    m = re.search(r"status: ([A-Z]+)", r)
    ans = re.findall(r"\sIN\s+A\s+(\S+)", r)
    return (m.group(1) if m else "TIMEOUT"), ans


def selftest(c: dict, n: int = 5, query=None, seed=None) -> int:
    """Positive controls: merged entries under registrable domains confirmed alive must be BLOCKED by Pi-hole
    (0.0.0.0 / :: / NXDOMAIN from the blocklist). Negative controls: entries under confirmed-dead registrable domains
    must not resolve at all, through Unbound directly; they fail whether or not anything blocks them."""
    q = query or dig_status
    ld, rnd = Path(c["LIST_DIR"]), random.Random(seed)
    db = results_db(Path(c["CHECK_DIR"]) / "results.sqlite")
    if db is None:
        print("blocklist-merge: selftest: no check results yet; nothing to test")
        return 0
    with contextlib.closing(db):
        alive = {r[0] for r in db.execute("SELECT name FROM reg WHERE state IN ('alive','revived') AND checks >= 1")}
        dead = [r[0] for r in db.execute("SELECT name FROM reg WHERE state = 'dead'")]
    try:
        exact = [l.strip() for l in (ld / HOSTS_FILE).read_text().splitlines() if l.strip() and not l.startswith("#")]
    except OSError:
        exact = []
    pos = [d for d in exact if d in alive]                 # entries that are themselves a confirmed-alive registrable
    fails = []
    for d in rnd.sample(pos, min(n, len(pos))):
        st, ans = q(c["PIHOLE_DNS"], d)
        ok = st == "NXDOMAIN" or (st == "NOERROR" and (not ans or all(a in ("0.0.0.0", "::") for a in ans)))
        print(f"  {'PASS' if ok else 'FAIL'}  positive control blocked by Pi-hole: {st} {ans[:1]}")
        fails += [] if ok else [d]
    for d in rnd.sample(dead, min(n, len(dead))):
        st, _ = q(c["UNBOUND_DNS"], d)
        ok = st == "NXDOMAIN"
        print(f"  {'PASS' if ok else 'FAIL'}  negative control does not exist (Unbound direct): {st}")
        fails += [] if ok else [d]
    if not pos and not dead:
        print("  (no controls yet: they appear as the checks confirm domains alive or dead)")
    if fails:
        push(c, f"blocklist-merge: selftest {len(fails)} FAIL", f"{len(fails)} control(s) failed; {c['OUT_DIR']}/report.txt", 4)
    return 1 if fails else 0


# ---------------------------------------------------------------------------------------- contributions --
def contrib(c: dict, now: float | None = None) -> Path:
    """Draft upstream evidence (the operator reviews and files it; nothing is sent from here):
      blp-dead-<date>.csv          BLP entries whose registrable domain is confirmed non-existent, with dates
      hagezi-candidates-<date>.csv merged entries (BLP-only) under a domain confirmed alive that a device here
                                   looked up since yesterday: candidates to propose to HaGeZi"""
    now = now or time.time()
    day = _ts(now)
    od = Path(c["OUT_DIR"]) / "contrib" / day
    od.mkdir(parents=True, exist_ok=True)
    db = results_db(Path(c["CHECK_DIR"]) / "results.sqlite")
    rows = {}
    if db is not None:
        with contextlib.closing(db):
            rows = {r[0]: r[1:] for r in db.execute("SELECT name, state, first_nx, last_nx, nx_count, last_alive, checks FROM reg")}
    cur = Path(c["OUT_DIR"]) / "blp-only-current.txt.gz"
    ent = []
    if cur.exists():
        with gzip.open(cur, "rt") as fh:
            ent = [l.rstrip("\n").split("\t") for l in fh if l.strip() and not l.startswith("#")]
    dead = ["name,blp_lists,registrable,first_nxdomain_utc,last_nxdomain_utc,nxdomain_checks\n"]
    for e in ent:
        r = rows.get(e[2]) if len(e) > 2 else None
        if r and r[0] == "dead":
            dead.append(f"{e[0]},{e[1].replace(',', ' ')},{e[2]},{_ts(r[1])},{_ts(r[2])},{r[3]}\n")
    seen = set()
    for p in (Path(c["PIHOLE_LOG"] + ".1"), Path(c["PIHOLE_LOG"])):
        with contextlib.suppress(OSError), open(p, errors="replace") as fh:
            for line in fh:
                m = QRY.search(line)
                if m:
                    seen.add(m.group(1).rstrip(".").lower())
    cand = ["name,blp_lists,registrable,last_alive_utc\n"]
    for e in ent:
        r = rows.get(e[2]) if len(e) > 2 else None
        if e[0] in seen and r and r[0] in ("alive", "revived"):
            cand.append(f"{e[0]},{e[1].replace(',', ' ')},{e[2]},{_ts(r[4]) if r[4] else ''}\n")
    gid = _gid(c)
    write_atomic(od / f"blp-dead-{day}.csv", "".join(dead), gid=gid)
    write_atomic(od / f"hagezi-candidates-{day}.csv", "".join(cand), gid=gid)
    print(f"blocklist-merge: drafts in {od}: {len(dead) - 1} dead BLP entries, {len(cand) - 1} HaGeZi candidates")
    return od


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "check", "selftest", "contrib", "status"])
    ap.add_argument("--conf", default="/etc/ctdc-blmerge/merge.conf")
    a = ap.parse_args(argv)
    try:
        c = parse_conf(Path(a.conf).read_text())
    except (OSError, ConfError) as e:
        print(f"blocklist-merge: config: {e}", file=sys.stderr)
        return 2
    if a.cmd == "build":
        return build(c)
    if a.cmd == "check":
        check(c)
        return 0
    if a.cmd == "selftest":
        return selftest(c)
    if a.cmd == "contrib":
        contrib(c)
        return 0
    p = Path(c["OUT_DIR"]) / "report.txt"
    print(p.read_text() if p.exists() else "blocklist-merge: no build yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
