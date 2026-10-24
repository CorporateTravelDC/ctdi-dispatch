#!/usr/bin/env python3
"""scripts/sdk-attribution.py -- which APP on the driver phone is behind each
third-party SDK / telemetry host Pi-hole sees? Daily co-occurrence
attribution over the FTL query log, with change alerts.

Operator 2026-10-23: "do the sdk <> request debug going forward and if
possible backfill". DNS alone cannot name an app (dns-anomaly-watch.py says
so in every alert), but an SDK host that is only ever resolved in the same
two-minute windows as ONE first-party app domain is attributable with
useful confidence: the SDK fires when the app is open. The 2026-10-23
prototype that this reproduces recovered, for example, McDonald's ->
split.io / mparticle, the credit union (nwfcu, alkami) -> urbanairship /
apptentive / pendo, PayPal / Venmo -> optimizely / branch, Qobuz -> braze EU,
Uber -> singular.

Method (identical to the prototype, kept deliberately simple):
  * rows = (timestamp, domain, status) for the configured client IP(s) over
    the window, from /etc/pihole/pihole-FTL.db opened READ-ONLY (sqlite
    `file:...?mode=ro`); query_storage is filtered by client id, not through
    the `queries` view, because the view joins every row before filtering;
  * 2-minute bins; N = bins in which the phone resolved anything at all;
  * SDK hosts = hosts with >= 20 BLOCKED lookups in the window (Pi-hole
    blocked statuses, BLOCKED_STATUSES below) PLUS hosts matching the known
    SDK regex (SDK_RE) even when Pi-hole allows them -- an allowed SDK host
    is exactly the kind the operator wants to see attributed;
  * app anchors = registrable domains (last two labels; three for co.uk-style
    public suffixes) of hosts that were NEVER blocked, are not infrastructure
    (INFRA_RE: google/apple/CDN/tailnet/... -- they co-occur with everything
    and would win every lift contest) and do not match SDK_RE, seen in >= 8
    bins;
  * score(sdk, anchor) = lift * min(1, both / 20) where
        lift = (both / S) / (A / N),
    S = sdk bins, A = anchor bins, both = shared bins; require both >= 6;
    the min(1, both/20) factor stops a 6-bin coincidence from outranking a
    40-bin habit; top 3 anchors per SDK host are reported.

Output per run: <report-dir>/YYYY-MM-DD.txt (plain text, ASCII) and the
same name .json; state.json keeps every SDK host ever seen and each host's
last confident top app. Alerts go through scripts/relay-nudge.py
(approval-gate, priority 3) ONCE per
  * brand-new SDK host (never in state), and
  * SDK host whose top attributed app CHANGED with n >= 15 shared bins.
The first run only seeds state (no alert storm); --backfill-days seeds /
extends state too but never alerts (a month of history is not news).

Configuration -- /etc/corporatetraveldc/dispatch.env, PARSED never sourced:
  SDK_ATTRIBUTION_CLIENT_IPS   comma list; default = UBER_WATCH_CLIENT_IP,
                               default 100.x.x.x (as dns-anomaly-watch.py)
The same key in the process environment overrides the file (tests);
SDK_ATTRIBUTION_ENV_FILE / SDK_ATTRIBUTION_FTL_DB override the paths
(tests). Client IPs are configuration, not output: nothing here prints,
logs or writes an IP -- reports and alerts name hosts and apps only.

Runs as the corporatetraveldc user (pihole group, read-only) from
corporatetraveldc-sdk-attribution.timer, daily 04:20 America/New_York,
inside the 23:00-05:00 maintenance window. Exit 0 normally.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_PREFIX = "sdk-attribution:"
LOCAL_TZ = ZoneInfo("America/New_York")
DISPATCH_ENV = "/etc/corporatetraveldc/dispatch.env"
PIHOLE_DB = "/etc/pihole/pihole-FTL.db"
DEFAULT_CLIENT_IP = "100.x.x.x"
REPORT_DIR = "/var/lib/corporatetraveldc/sdk-attribution"
STATE_FILE = REPORT_DIR + "/state.json"
RELAY_NUDGE = Path(__file__).resolve().with_name("relay-nudge.py")
ALERT_TOPIC = "approval-gate"
ALERT_PRIORITY = 3

BIN_S = 120
SDK_MIN_BLOCKED = 20       # blocked lookups that make an unlisted host an SDK host
ANCHOR_MIN_BINS = 8        # an app has to be used in this many bins to be an anchor
PAIR_MIN_BOTH = 6          # shared bins before a pair is scored at all
PAIR_FULL_WEIGHT = 20      # shared bins at which the lift gets its full weight
CHANGE_MIN_N = 15          # shared bins before a top-app change is worth an alert
TOP_K = 3

# 2026-10-24: Pi-hole FTL status codes that mean "blocked" (gravity, regex,
# denylist, their CNAME variants, external NXDOMAIN/NULL/IP blocks, special
# domain, database busy -> blocked). 2/3/12/13/14/17 are answers (forwarded,
# cache, retried, cache-stale). Same set as the 2026-10-23 prototype.
BLOCKED_STATUSES = {1, 4, 5, 6, 7, 8, 9, 10, 11, 15, 16}

# Known third-party SDK / telemetry / attribution vendors. Any host matching
# is an SDK host whether or not Pi-hole blocks it, and can never be an anchor.
SDK_RE = re.compile(
    r"braze|mparticle|split\.io|segment|mixpanel|amplitude|appsflyer|adjust|singular|"
    r"branch\.io|onesignal|urbanairship|apptentive|pendo|optimizely|launchdarkly|statsig|"
    r"datadoghq|newrelic|sentry|bugsnag|instabug|crashlytics|app-measurement|"
    r"firebaselogging|graph\.facebook\.com|siftscience|forter|mapbox.*events|"
    r"googlesyndication|doubleclick"
)
# Infrastructure the phone talks to regardless of which app is open. Never an
# anchor: with ~100% bin coverage it would co-occur with every SDK host.
INFRA_RE = re.compile(
    r"google|gstatic|gvt1|youtube|googlevideo|apple|icloud|cloudflare|akamai|fastly|"
    r"cloudfront|amazonaws|azure|microsoft|arpa|grapheneos|ntp|cdn|tailscale|ts\.net"
)
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_-]+\.)+[a-z]{2,}$")
# Second-level labels under which the registrable domain is three labels long
# (co.uk, com.au, com.br, ...). A heuristic, not the public suffix list.
_SLD_PUBLIC = {"co", "com", "net", "org", "gov", "edu", "ac", "ne", "or", "go"}
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


# --------------------------------------------------------------------- config
def read_env_key(path: str, key: str) -> str:
    """Value of KEY=... in an env file by PARSING it (never sourcing): the
    file also carries secrets, and a sourced file can run code. Handles an
    optional `export ` prefix and surrounding quotes; the first match wins."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def client_ips(env_file: str = DISPATCH_ENV) -> list[str]:
    raw = os.environ.get("SDK_ATTRIBUTION_CLIENT_IPS") or read_env_key(env_file, "SDK_ATTRIBUTION_CLIENT_IPS")
    if not raw:
        raw = os.environ.get("UBER_WATCH_CLIENT_IP") or read_env_key(env_file, "UBER_WATCH_CLIENT_IP") or DEFAULT_CLIENT_IP
    return [p.strip() for p in raw.split(",") if p.strip()]


# ----------------------------------------------------------------------- data
def load_rows(db_path: str, ips: list[str], start_ts: int, end_ts: int) -> tuple[list[tuple[int, str, int]], int]:
    """(timestamp, domain, status) rows for the clients in [start_ts, end_ts),
    plus how many of the configured clients the DB knows. Read-only URI open;
    the query_storage/domain_by_id/client_by_id split is Pi-hole v6's schema."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        marks = ",".join("?" * len(ips))
        ids = [r[0] for r in con.execute(f"SELECT id FROM client_by_id WHERE ip IN ({marks})", ips)]
        if not ids:
            return [], 0
        id_marks = ",".join("?" * len(ids))
        cur = con.execute(
            "SELECT q.timestamp, d.domain, q.status FROM query_storage q "
            "JOIN domain_by_id d ON d.id = q.domain "
            f"WHERE q.timestamp >= ? AND q.timestamp < ? AND q.client IN ({id_marks})",
            [start_ts, end_ts, *ids],
        )
        rows = [(int(ts), str(dom).lower(), int(st)) for ts, dom, st in cur]
        return rows, len(ids)
    finally:
        con.close()


def db_oldest_ts(db_path: str) -> int | None:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            v = con.execute("SELECT MIN(timestamp) FROM query_storage").fetchone()[0]
        finally:
            con.close()
        return int(v) if v is not None else None
    except sqlite3.Error:
        return None


# ------------------------------------------------------------------- analysis
def registrable(host: str) -> str:
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SLD_PUBLIC and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def analyse(rows: list[tuple[int, str, int]]) -> dict:
    """The prototype's scoring. Returns {"bins": N, "queries": Q,
    "hosts": [per-SDK-host dicts sorted by blocked desc]}."""
    host_bins: dict[str, set[int]] = defaultdict(set)
    blocked: dict[str, int] = defaultdict(int)
    allowed: dict[str, int] = defaultdict(int)
    all_bins: set[int] = set()
    for ts, host, status in rows:
        # 2026-10-24: reverse lookups (x.x.x.x.in-addr.arpa) carry a client or
        # peer address in the name; they are never an app signal and must not
        # reach a report. Non-hostnames (vault filenames etc.) are skipped too.
        if not _HOSTNAME_RE.match(host) or host.endswith(".arpa"):
            continue
        b = ts // BIN_S
        all_bins.add(b)
        host_bins[host].add(b)
        if status in BLOCKED_STATUSES:
            blocked[host] += 1
        else:
            allowed[host] += 1
    n_bins = len(all_bins)

    sdk_hosts = sorted(
        h for h in host_bins if blocked[h] >= SDK_MIN_BLOCKED or SDK_RE.search(h)
    )

    anchor_bins: dict[str, set[int]] = defaultdict(set)
    for h, bins in host_bins.items():
        if blocked[h] or INFRA_RE.search(h) or SDK_RE.search(h):
            continue
        anchor_bins[registrable(h)] |= bins
    anchors = {a: bins for a, bins in anchor_bins.items() if len(bins) >= ANCHOR_MIN_BINS}

    out = []
    for h in sdk_hosts:
        s_bins = host_bins[h]
        own = registrable(h)
        scored = []
        for a, a_bins in anchors.items():
            if a == own:
                continue   # a host never attributes to its own domain
            both = len(s_bins & a_bins)
            if both < PAIR_MIN_BOTH or not n_bins:
                continue
            lift = (both / len(s_bins)) / (len(a_bins) / n_bins)
            score = lift * min(1.0, both / PAIR_FULL_WEIGHT)
            scored.append({"anchor": a, "lift": round(lift, 2), "n": both, "score": round(score, 3)})
        scored.sort(key=lambda d: (-d["score"], -d["n"], d["anchor"]))
        out.append({
            "host": h,
            "blocked": blocked[h],
            "allowed": allowed[h],
            "bins": len(s_bins),
            "known_sdk_pattern": bool(SDK_RE.search(h)),
            "top": scored[:TOP_K],
        })
    out.sort(key=lambda d: (-(d["blocked"] + d["allowed"]), d["host"]))
    return {"bins": n_bins, "queries": len(rows), "hosts": out}


# --------------------------------------------------------------------- output
def _fmt_local(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, LOCAL_TZ).strftime("%Y-%m-%d %H:%M %Z")


def render_text(result: dict, start_ts: int, end_ts: int, label: str, n_clients_cfg: int, n_clients_db: int) -> str:
    lines = [
        f"sdk-attribution report {label}",
        f"window: {_fmt_local(start_ts)} .. {_fmt_local(end_ts)}  "
        f"({(end_ts - start_ts) / 86400:.1f} d, {result['bins']} active 2-min bins, {result['queries']} queries, "
        f"clients configured {n_clients_cfg} / known to FTL {n_clients_db})",
        "scoring: lift = (both/S)/(A/N) x min(1, both/20); both >= 6; anchors >= 8 bins; top 3 per host",
        "",
        f"{'sdk host':<48} {'blocked':>7} {'allowed':>7}  top apps (lift, n)",
    ]
    for h in result["hosts"]:
        tops = "; ".join(f"{t['anchor']} ({t['lift']}, n={t['n']})" for t in h["top"]) or "-"
        flag = "" if h["known_sdk_pattern"] else " [by-count]"
        lines.append(f"{h['host']:<48} {h['blocked']:>7} {h['allowed']:>7}  {tops}{flag}")
    if not result["hosts"]:
        lines.append("(no SDK hosts in window)")
    return "\n".join(lines) + "\n"


def write_reports(report_dir: str, label: str, text: str, payload: dict) -> tuple[Path, Path]:
    d = Path(report_dir)
    d.mkdir(parents=True, exist_ok=True)
    txt, js = d / f"{label}.txt", d / f"{label}.json"
    txt.write_text(text)
    js.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return txt, js


# ---------------------------------------------------------------------- state
def load_state(path: str) -> dict:
    try:
        state = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    state.setdefault("version", 1)
    state.setdefault("hosts", {})
    return state


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, p)


def update_state(state: dict, result: dict, label: str) -> tuple[list[str], list[tuple[str, str, str, int]]]:
    """Fold a run into state. Returns (new_hosts, changes) where changes are
    (host, old_top, new_top, n). `top` in state is the last CONFIDENT top
    (n >= CHANGE_MIN_N); a weaker top is recorded as latest_top only, so the
    change alert fires once, when the evidence is there, and not on noise."""
    hosts = state["hosts"]
    seeding = not hosts
    new_hosts, changes = [], []
    for h in result["hosts"]:
        name = h["host"]
        entry = hosts.get(name)
        if entry is None:
            entry = hosts[name] = {"first_seen": label, "top": None, "top_n": 0}
            if not seeding:
                new_hosts.append(name)
        entry["last_seen"] = label
        best = h["top"][0] if h["top"] else None
        entry["latest_top"] = best["anchor"] if best else None
        entry["latest_n"] = best["n"] if best else 0
        if best and best["n"] >= CHANGE_MIN_N:
            if entry.get("top") and entry["top"] != best["anchor"] and not seeding:
                changes.append((name, entry["top"], best["anchor"], best["n"]))
            entry["top"], entry["top_n"] = best["anchor"], best["n"]
    state["last_run"] = label
    if seeding:
        state["seeded"] = label
    return new_hosts, changes


# --------------------------------------------------------------------- alerts
def send_alert(title: str, message: str) -> None:
    """One nudge on approval-gate (relay-nudge.py reads the ntfy URL/token
    itself; nothing secret passes through here). Never raises."""
    try:
        subprocess.run(
            [sys.executable, str(RELAY_NUDGE), title, message,
             "--priority", str(ALERT_PRIORITY), "--topic", ALERT_TOPIC],
            check=False, timeout=30,
        )
    except Exception as e:  # noqa: BLE001 -- an alert must never fail the report
        print(f"{LOG_PREFIX} alert not sent ({type(e).__name__})")


def alert_text_new(new_hosts: list[str], result: dict) -> str:
    by_host = {h["host"]: h for h in result["hosts"]}
    parts = []
    for name in new_hosts[:12]:
        h = by_host[name]
        top = h["top"][0] if h["top"] else None
        where = f"-> {top['anchor']} (lift {top['lift']}, n={top['n']})" if top else "-> no app attributed yet"
        parts.append(f"{name} (blocked {h['blocked']}, allowed {h['allowed']}) {where}")
    more = f" (+{len(new_hosts) - 12} more in the report)" if len(new_hosts) > 12 else ""
    return "New SDK/telemetry host(s) from the driver phone:\n" + "\n".join(parts) + more


def alert_text_changes(changes: list[tuple[str, str, str, int]]) -> str:
    return "SDK host now co-occurs with a different app:\n" + "\n".join(
        f"{h}: {old} -> {new} (n={n})" for h, old, new, n in changes
    )


# ----------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="attribute SDK/telemetry hosts to apps by DNS co-occurrence")
    ap.add_argument("--days", type=float, default=1.0, help="window length ending now (default 1)")
    ap.add_argument("--backfill-days", type=float, default=None,
                    help="one-shot: analyse up to N days (capped at DB retention), write one report, seed state, no alerts")
    ap.add_argument("--dry-run", action="store_true", help="print the report; write nothing, alert nothing")
    ap.add_argument("--report-dir", default=REPORT_DIR)
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--ftl-db", default=os.environ.get("SDK_ATTRIBUTION_FTL_DB", PIHOLE_DB))
    ap.add_argument("--env-file", default=os.environ.get("SDK_ATTRIBUTION_ENV_FILE", DISPATCH_ENV))
    a = ap.parse_args(argv)

    ips = client_ips(a.env_file)
    now = int(time.time())
    backfill = a.backfill_days is not None
    days = a.backfill_days if backfill else a.days
    start_ts = now - int(days * 86400)
    if backfill:
        oldest = db_oldest_ts(a.ftl_db)
        if oldest is not None and oldest > start_ts:
            print(f"{LOG_PREFIX} backfill capped at DB retention ({_fmt_local(oldest)})")
            start_ts = oldest
    end_label = dt.datetime.fromtimestamp(now, LOCAL_TZ).strftime("%Y-%m-%d")
    label = f"{end_label}-backfill" if backfill else end_label

    try:
        rows, n_db = load_rows(a.ftl_db, ips, start_ts, now)
    except sqlite3.Error as e:
        print(f"{LOG_PREFIX} FTL DB read failed: {type(e).__name__}: {e}")
        return 0
    result = analyse(rows)
    # 2026-10-24: belt and braces for the no-IP rule -- a query name that
    # survived the .arpa filter but still looks like a dotted quad is masked.
    text = _IP_RE.sub("x.x.x.x", render_text(result, start_ts, now, label, len(ips), n_db))
    payload = {
        "label": label, "window_start": start_ts, "window_end": now, "backfill": backfill,
        "clients_configured": len(ips), "clients_in_db": n_db, **result,
    }
    print(text, end="")

    if a.dry_run:
        print(f"{LOG_PREFIX} dry-run: no report, state or alert written")
        return 0

    txt, js = write_reports(a.report_dir, label, text, payload)
    state = load_state(a.state)
    seeding = not state["hosts"]
    new_hosts, changes = update_state(state, result, label)
    save_state(a.state, state)
    print(f"{LOG_PREFIX} wrote {txt} and {js}; state {a.state} ({len(state['hosts'])} known SDK hosts)")

    if seeding:
        print(f"{LOG_PREFIX} first run -- state seeded silently, no alerts")
        return 0
    if backfill:
        print(f"{LOG_PREFIX} backfill -- alerts suppressed ({len(new_hosts)} host(s) added to state)")
        return 0
    if new_hosts:
        print(f"{LOG_PREFIX} new SDK host(s): {new_hosts}")
        send_alert("SDK attribution -- new SDK host", alert_text_new(new_hosts, result))
    if changes:
        print(f"{LOG_PREFIX} top-app change(s): {[c[0] for c in changes]}")
        send_alert("SDK attribution -- app changed", alert_text_changes(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
