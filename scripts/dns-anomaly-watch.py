#!/usr/bin/env python3
"""
dns-anomaly-watch.py (renamed from uber-traffic-watch.py 2026-10-10) -- watches Pi-hole's live query log for the driver
phone (client 100.x.x.x) for Uber app reconnect events, and flags any
traffic riding alongside a reconnect that doesn't match Uber's own
established first-party CNAME infrastructure.

Added 2026-08-12 after finding two Uber subdomains (fpfcpn2u.uber.com,
ak04a6qc.uber.com) that skip Uber's normal internal routing convention
entirely -- no CNAME / a randomized-ID CDN CNAME, resolving straight to
generic Cloudflare/CloudFront infrastructure instead of Uber's own
frontends-cloud.uber.com / cn-neg-geo.cfe.uber.com family. Both are now on
the Pi-hole denylist. This exists to catch the NEXT one automatically,
rather than relying on another manual mining pass, and to build a running
picture of what fires specifically when the app comes back online (the
"reconnect burst") vs. background noise.

Consolidated 2026-08-29 -- operator directive after this script's own
novelty check flagged two genuinely new anomaly endpoints
(lens.usercontent.google.com, rr1---sn-p5qs7nzr.gvt1.com -- both riding
alongside an Uber reconnect burst, neither a *.uber.com domain so the
CNAME-family check never applied to them; caught by the novelty check
instead). Added two operational tracking capabilities on top of the
original CNAME/novelty detection so this becomes a running program, not
just a one-shot detector:

  3. Blocked-hit frequency -- once a domain is on TRACKED_ANOMALY_ENDPOINTS
     and denylisted, how often does the app/phone still try to reach it?
     Tracked per-domain (total hits, hits this pass, last-hit time) in
     STATE_FILE, using Pi-hole's own DENYLIST status code (5) as the
     "actually blocked, not merely queried" signal.
  4. Discovery-pattern tracking -- a running history of when each new
     anomaly was first confirmed, with simple gap statistics (mean/median
     days between discoveries) folded into the alert text. Descriptive
     bookkeeping from a handful of real events, not a forecast.

A generalized, platform-agnostic version of all four capabilities (this
script started from the same logic, parameterized instead of
Uber-specific) lives in the public agentic-management-tooling-mcp repo:
gig_mobility/endpoint_anomaly.py. Kept as two separate implementations
rather than one importing the other -- this script runs unattended on a
2-minute systemd timer and should not gain a runtime dependency on a
second repo; the public module is the documented, reusable twin for any
other gig-platform deployment (or public-repo consumer), not a shared
library this one imports.

This script does NOT have the privileges to add a domain to the Pi-hole
denylist itself (`pihole deny` requires root or the `pihole` user; this
script runs as `corporatetraveldc`, group-membership-only). When a
TRACKED_ANOMALY_ENDPOINTS entry isn't actually denylisted yet (checked
read-only against gravity.db, which this script's existing pihole-group
membership already permits), it fires a priority-5 alert with the exact
command to run rather than attempting any privilege escalation.

Two independent detection checks, both scoped to a window around a
detected reconnect (a KNOWN_MARKER_DOMAINS hit after RECONNECT_GAP_MIN of
silence from all of them):

  1. CNAME-family check -- every *.uber.com domain seen in the burst gets
     a live CNAME/A lookup. A hit is "known-good" if its CNAME target is
     in KNOWN_GOOD_CNAME_TARGETS (the legitimate frontends-cloud/cn-neg
     family confirmed 2026-08-12), "known-blocked" if it's already on the
     denylist (KNOWN_BLOCKED, resolves 0.0.0.0), or "known-safe-direct"
     for a small set of already-investigated exceptions (e.g.
     tracking.ibt.uber.com -> email-messaging.com, a confirmed but
     harmless email-tracking cloak). Anything else -- a brand-new
     *.uber.com name with no CNAME to the known family -- is flagged.

  2. Novelty check -- any domain (not just uber.com) queried by this
     client that has never appeared in the persisted baseline
     (STATE_FILE's seen_domains, seeded once from history on first run)
     gets flagged as new, with extra weight if it falls inside a
     reconnect burst window rather than at some unrelated time. This is
     the check that caught both non-uber.com anomalies above -- the
     CNAME-family check only ever looks at *.uber.com names.

Requires read access to /etc/pihole/pihole-FTL.db AND /etc/pihole/gravity.db
-- runs as a user in the `pihole` group (one-time `sudo usermod -aG pihole
corporatetraveldc` -- read-only, no write access needed or used here).
Runs via a 2-minute systemd --user timer, same shape as
thermal-ingest-guard.py / governor-watch.py.

Tunables (dispatch.env, all optional -- defaults shown are what's used
if the var is absent or unparsable):
  UBER_WATCH_ENABLED=true
  UBER_WATCH_CLIENT_IP=100.x.x.x
  UBER_WATCH_RECONNECT_GAP_MIN=20
  UBER_WATCH_BURST_WINDOW_S=120

DNS BYPASS class (2026-11-27, operator), OFF unless DNS_BYPASS_WATCH_JSON is set:
  DNS_BYPASS_WATCH_JSON=/var/lib/ctdc-dnsleak/latest.json   the root collector's output (config/dns-leak-watch/,
                                                            docs/DNS_LEAK_WATCH.md); schema 1, group-readable
  DNS_BYPASS_CORRELATE_MIN=10     a bypass finding within N minutes of a reconnect / novelty / CNAME-drift event is
                                  reported as correlated with it (priority 5 instead of 4)
  DNS_BYPASS_MAX_AGE_MIN=30       an older latest.json is ignored (the collector is not running)
  DNS_BYPASS_HOST=                when the collector watches the whole tailnet (TAILNET=yes), only findings from
                                  this tailnet host name are this watch's; empty = all, each line names its host
The collector records the watched client's NEW forwarded connections in an nft set; its "bypass_evidence" lists
destinations the device used to resolve names outside Pi-hole: dns53 (plain DNS to another server), dot / doq
(port 853, TCP / UDP), doh (443 to an address on the encrypted-resolver list), plus the dns-lock table's redirected
plain-DNS count. Each finding alerts ONCE (key class|dest|proto|port); the first pass with the feature on is a silent
baseline. Hard-coded destinations are DNS leak watch's own alerts; here they are only logged when they fall inside a
reconnect burst, as correlation context.
"""
import json
import os
import subprocess
import time
import urllib.request

DISPATCH_ENV = "/etc/corporatetraveldc/dispatch.env"
SECRETS_ENV = "/etc/corporatetraveldc/dispatch-secrets.env"
PIHOLE_DB = "/etc/pihole/pihole-FTL.db"
GRAVITY_DB = "/etc/pihole/gravity.db"
STATE_FILE = "/var/lib/corporatetraveldc/dns_anomaly_watch_state.json"
# 2026-10-10: renamed from uber-traffic-watch; the old state file is migrated on first run.
OLD_STATE_FILE = "/var/lib/corporatetraveldc/uber_traffic_watch_state.json"
LOG_PREFIX = "dns-anomaly-watch:"
# 2026-10-03: alerts were renamed from "Uber Traffic Watch" to neutral "DNS
# anomaly" wording (operator directive, backlog since 2026-09-24): the
# operator saw the alert name Uber while the app was verifiably off. The
# detector is unchanged -- it watches the driver phone's DNS for marker
# re-resolutions and novel domains -- only its CLAIM about the source is.
_HOSTNAME_RE = __import__("re").compile(r"^(?=.{1,253}$)([a-z0-9_-]+\.)+[a-z]{2,}$")
# A two-label token whose "TLD" is a common file extension is a filename the
# phone resolved (today's '20261003t011042z.md' -- .md is Moldova's ccTLD, so
# the hostname regex alone passes it). Three-plus labels are left alone.
_FILE_EXT_TLDS = {"md", "txt", "json", "pdf", "html", "htm", "csv", "log", "py", "sh",
                  "xml", "yml", "yaml", "png", "jpg", "jpeg", "gif", "svg", "zip", "gz", "tar"}

def _looks_like_hostname(token: str) -> bool:
    if not _HOSTNAME_RE.match(token):
        return False
    labels = token.split(".")
    return not (len(labels) == 2 and labels[-1] in _FILE_EXT_TLDS)

# Reconnect markers: real-time dispatch/connectivity domains, not static
# assets -- these fire specifically when the app is actively trying to
# reach Uber's backend (going online, location pings), so a fresh hit
# after a gap is a real "app just reconnected" signal.
KNOWN_MARKER_DOMAINS = {
    "tc2.uber.com", "cn-geo1.uber.com", "driver.android.mobile-config.uber.com",
}

# Confirmed 2026-08-12 -- Uber's own legitimate Cloudflare/GCP-fronted
# routing family. A *.uber.com domain CNAMEing here is normal.
KNOWN_GOOD_CNAME_TARGETS = {
    "frontends-cloud.uber.com", "cn-neg.cfe.uber.com", "cn-neg-geo.cfe.uber.com",
    "cloudflare-weighted.uber.com", "frontends-gcp-cloudflare-verification.uber.com",
    "frontends-cloudflare.uber.com",
}

# Already investigated and denylisted -- resolve 0.0.0.0 now. Don't
# re-alert on these every run; they're the reason this script exists.
# Only the *.uber.com CNAME-family check uses this set.
# 2026-11-12: brought to parity with every *.uber.com exact denylist entry in
# gravity.db (was only the original two); check_denylist_gaps() logs any
# future drift. cn-geo1 and driver.android.mobile-config are also reconnect
# markers -- being blocked does not stop them marking a reconnect.
KNOWN_BLOCKED = {
    "fpfcpn2u.uber.com", "ak04a6qc.uber.com", "driver.android.mobile-config.uber.com",
    "pusher.uber.com", "vehicles.uber.com", "cn-geo1.uber.com",
}

# Already investigated, confirmed benign despite not matching the
# CNAME-family pattern (see module docstring).
KNOWN_SAFE_DIRECT = {"tracking.ibt.uber.com"}

# Every anomaly ever confirmed by either check, tracked regardless of
# whether it's a *.uber.com name (the CNAME-family check's KNOWN_BLOCKED
# is uber.com-scoped; this is the broader set the blocked-hit-frequency
# and discovery-pattern monitors below operate over). "discovered" is the
# date this was first confirmed anomalous, "kind" is which check found
# it -- both purely informational, not read by any logic here.
TRACKED_ANOMALY_ENDPOINTS = {
    "fpfcpn2u.uber.com": {"discovered": "2026-08-12", "kind": "cname-drift"},
    "ak04a6qc.uber.com": {"discovered": "2026-08-12", "kind": "cname-drift"},
    # 2026-11-12: the four later Uber denylist entries, dated by their
    # gravity.db date_added. Blocked hits are counted and logged, never alerted.
    "driver.android.mobile-config.uber.com": {"discovered": "2026-08-15", "kind": "manual-denylist"},
    "pusher.uber.com": {"discovered": "2026-08-19", "kind": "manual-denylist"},
    "vehicles.uber.com": {"discovered": "2026-09-04", "kind": "novel-in-burst"},
    "cn-geo1.uber.com": {"discovered": "2026-09-10", "kind": "cname-drift"},
    "lens.usercontent.google.com": {"discovered": "2026-08-29", "kind": "novel-in-burst"},
    # rr1---sn-p5qs7nzr.gvt1.com removed 2026-09-28: it is a rotating Google
    # download-CDN edge node, not a stable anomaly endpoint -- blocking one
    # node of a rotating edge accomplishes nothing and re-fires the denylist-
    # gap nag forever once removed from Pi-hole. gvt1 fan-out is now handled
    # by shape-based detection (host/cluster ratio) in the generalized
    # endpoint-anomaly detector, not by pinning individual edge hosts.
}

# Pi-hole FTL status code for "blocked by an exact denylist entry" (as
# opposed to 2=FORWARDED, 3=CACHE, 9/10/11=gravity/regex/denylist-CNAME
# variants, 17=CACHE_STALE). Confirmed live 2026-08-29 against the two
# already-blocked uber.com entries -- this is the real code their
# post-denylisting hits carry.
STATUS_DENYLIST = 5


def _parse_env_file(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


def _cfg():
    env = {}
    env.update(_parse_env_file(DISPATCH_ENV))
    env.update(_parse_env_file(SECRETS_ENV))
    return env


def _bool(v, default):
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_state():
    import os, shutil
    if not os.path.exists(STATE_FILE) and os.path.exists(OLD_STATE_FILE):
        shutil.copy2(OLD_STATE_FILE, STATE_FILE)   # one-time migration from the pre-rename state file
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("seen_domains", [])
    state.setdefault("last_marker_ts", {})
    state.setdefault("last_run_ts", 0)
    state.setdefault("blocked_hit_counts", {})
    state.setdefault("blocked_hit_last_ts", {})
    state.setdefault("blocked_hit_first_seen_ts", {})
    state.setdefault("anomaly_discovery_log", [])
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def query_pihole(sql, db=PIHOLE_DB):
    # Wrapped in `sg pihole -c` rather than calling sqlite3 directly:
    # this process's own supplementary groups are cached from whenever
    # its parent (systemd --user, a long-running manager) last logged
    # in, which can predate a `usermod -aG pihole` grant by however long
    # that session has been up. `sg` re-reads /etc/group fresh on every
    # invocation, so it picks up the grant immediately with no relogin
    # or user@<uid>.service restart needed (the latter would restart
    # every other production service running under this same session).
    quoted = sql.replace("'", "'\\''")
    r = subprocess.run(
        ["sg", "pihole", "-c", f"sqlite3 -readonly '{db}' '{quoted}'"],
        check=False, capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(f"{LOG_PREFIX} sqlite3 query failed: {r.stderr.strip()}")
        return []
    return [line for line in r.stdout.splitlines() if line]


def cname_chain(domain):
    """Full live CNAME chain + first A record. Returns (chain_list, ip_or_None).
    2026-10-10: follows EVERY hop -- the old single-hop check flagged
    tb-static -> tb-static-migration -> frontends-cloud (Uber's own family) as
    anomalous because it stopped at the first unfamiliar name."""
    out = subprocess.run(["dig", "+short", domain, "A"], capture_output=True, text=True, timeout=10).stdout.split()
    chain = [x.rstrip(".") for x in out if x.endswith(".")]
    ips = [x for x in out if not x.endswith(".")]
    return chain, (ips[0] if ips else None)


def render_chain(domain, chain, ip):
    return " -> ".join([domain] + chain) + (f" ({ip})" if ip else "")


def classify_uber_domain(domain):
    """Returns one of: known-good, known-blocked, known-safe-direct, ANOMALOUS."""
    if domain in KNOWN_BLOCKED:
        return "known-blocked"
    if domain in KNOWN_SAFE_DIRECT:
        return "known-safe-direct"
    chain, ip = cname_chain(domain)
    if any(hop in KNOWN_GOOD_CNAME_TARGETS for hop in chain):
        return "known-good"
    if ip == "0.0.0.0":
        return "known-blocked"  # denylisted by something else already
    return f"ANOMALOUS: {render_chain(domain, chain, ip)}"


def check_denylist_gaps():
    """Read-only check (gravity.db) for which TRACKED_ANOMALY_ENDPOINTS
    entries are NOT yet actually denylisted. This script cannot add them
    itself -- `pihole deny` requires root/the pihole user, and this
    script deliberately does not attempt any privilege escalation to get
    there (see module docstring). Returns the list of missing domains so
    main() can alert loudly with the exact command to run."""
    rows = query_pihole("SELECT domain FROM domainlist WHERE type=1;", db=GRAVITY_DB)
    denylisted = set(rows)
    # Reverse drift (log only, no alert): a *.uber.com denylist entry added
    # in Pi-hole but not mirrored into KNOWN_BLOCKED / TRACKED_ANOMALY_ENDPOINTS.
    unmirrored = sorted(d for d in denylisted if d.endswith(".uber.com")
                        and (d not in KNOWN_BLOCKED or d not in TRACKED_ANOMALY_ENDPOINTS))
    if unmirrored:
        print(f"{LOG_PREFIX} denylisted but not in KNOWN_BLOCKED/TRACKED_ANOMALY_ENDPOINTS: {unmirrored}")
    return [d for d in TRACKED_ANOMALY_ENDPOINTS if d not in denylisted]


def query_blocked_hits(client_ip, since):
    """(timestamp, domain) rows for TRACKED_ANOMALY_ENDPOINTS domains
    actually blocked (STATUS_DENYLIST) for this client since `since`."""
    domain_list = ", ".join(f"'{d}'" for d in TRACKED_ANOMALY_ENDPOINTS)
    rows = query_pihole(
        f"SELECT timestamp, domain FROM queries "
        f"WHERE client = '{client_ip}' AND status = {STATUS_DENYLIST} "
        f"AND domain IN ({domain_list}) AND timestamp > {int(since)} "
        f"ORDER BY timestamp;"
    )
    hits = []
    for line in rows:
        try:
            ts_str, domain = line.split("|", 1)
            hits.append((int(float(ts_str)), domain))
        except ValueError:
            continue
    return hits


def track_blocked_hit_frequency(blocked_hits, state, now_ts):
    """Mirrors gig_mobility.endpoint_anomaly.track_blocked_hit_frequency's
    logic (see that module for the generalized, documented version) --
    duplicated here rather than imported, see module docstring for why.
    Mutates state's blocked_hit_* dicts in place; returns a per-domain
    summary for anything hit this pass."""
    counts = state["blocked_hit_counts"]
    last_ts = state["blocked_hit_last_ts"]
    first_seen = state["blocked_hit_first_seen_ts"]

    this_pass = {}
    for ts, domain in blocked_hits:
        this_pass[domain] = this_pass.get(domain, 0) + 1
        counts[domain] = counts.get(domain, 0) + 1
        last_ts[domain] = max(last_ts.get(domain, 0), ts)
        first_seen.setdefault(domain, ts)

    summary = {}
    for domain, hits_this_pass in this_pass.items():
        days_tracked = (now_ts - first_seen[domain]) / 86400 if domain in first_seen else None
        summary[domain] = {
            "hits_this_pass": hits_this_pass,
            "total_hits": counts[domain],
            "days_since_first_seen_blocked": round(days_tracked, 1) if days_tracked else None,
        }
    return summary


def track_discovery_pattern(state, new_anomaly, now_ts):
    """Mirrors gig_mobility.endpoint_anomaly.track_discovery_pattern's
    logic (see that module for the generalized, documented version).
    Mutates state['anomaly_discovery_log'] in place; returns gap stats."""
    log = state["anomaly_discovery_log"]
    if new_anomaly is not None:
        log.append({**new_anomaly, "ts": now_ts})
    if not log:
        return {"total_discovered": 0, "mean_gap_days": None, "days_since_last": None}
    ordered = sorted(log, key=lambda e: e["ts"])
    gaps_days = [(ordered[i + 1]["ts"] - ordered[i]["ts"]) / 86400 for i in range(len(ordered) - 1)]
    return {
        "total_discovered": len(ordered),
        "mean_gap_days": round(sum(gaps_days) / len(gaps_days), 1) if gaps_days else None,
        "days_since_last": round((now_ts - ordered[-1]["ts"]) / 86400, 1),
    }


def _host_ntfy_base(cfg):
    base = cfg.get("NTFY_URL", "http://host.containers.internal:2586")
    port = base.rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}"


def ntfy_alert(cfg, message, title, priority=4):
    base = _host_ntfy_base(cfg)
    token = cfg.get("NTFY_TOKEN", "").split(":")[0]
    url = f"{base}/ops-health"
    req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "text/plain; charset=utf-8")
    req.add_header("X-Priority", str(priority))
    req.add_header("X-Title", title)
    req.add_header("X-Tags", "mag")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"{LOG_PREFIX} ntfy push failed (non-fatal): {e}")


def _latest_bypass(cfg, client_ip, now):
    """The collector's latest.json, if the feature is on, fresh, and about this client. None otherwise (logged)."""
    path = cfg.get("DNS_BYPASS_WATCH_JSON", "").strip()
    if not path:
        return None
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        print(f"{LOG_PREFIX} bypass: cannot read {path} ({type(e).__name__}); skipped")
        return None
    if doc.get("schema") != 1:
        print(f"{LOG_PREFIX} bypass: {path} has schema {doc.get('schema')!r}, expected 1; skipped")
        return None
    max_age = _float(cfg.get("DNS_BYPASS_MAX_AGE_MIN"), 30.0) * 60
    if now - float(doc.get("generated", 0)) > max_age:
        print(f"{LOG_PREFIX} bypass: {path} is older than {max_age / 60:.0f} min (collector not running?); skipped")
        return None
    import ipaddress
    try:
        a = ipaddress.ip_address(client_ip)
        nets = [ipaddress.ip_network(n, strict=False) for n in doc.get("clients", [])]
    except ValueError:
        return None
    if not any(a.version == n.version and a in n for n in nets):
        print(f"{LOG_PREFIX} bypass: the collector does not watch this client; skipped")
        return None
    return doc


def bypass_pass(cfg, state, now, client_ip, event_ts):
    """Fold the collector's DNS bypass evidence into this watch's findings. event_ts: epoch times of this watch's own
    events (reconnect windows, novel / anomalous domains) to correlate with. Returns the alerted findings."""
    doc = _latest_bypass(cfg, client_ip, now)
    if doc is None:
        return []
    window = _float(cfg.get("DNS_BYPASS_CORRELATE_MIN"), 10.0) * 60
    events = sorted(set(event_ts) | {float(v) for v in state.get("last_marker_ts", {}).values()})
    near = lambda ts: any(abs(ts - e) <= window for e in events)
    ev = doc.get("bypass_evidence", {})
    findings = ev.get("findings", [])
    only = cfg.get("DNS_BYPASS_HOST", "").strip()
    if only:
        findings = [f for f in findings if f.get("host") == only]
    baseline = "bypass_alerted" not in state
    alerted = set(state.get("bypass_alerted", []))
    new = [f for f in findings if f.get("key") and f["key"] not in alerted]
    state["bypass_alerted"] = sorted(alerted | {f["key"] for f in findings if f.get("key")})
    for h in doc.get("hardcoded", {}).get("findings", []):
        if h.get("first_seen", 0) > now - 3600 and near(h["first_seen"]):
            print(f"{LOG_PREFIX} bypass: hard-coded destination {h['dest']} {h['proto']}/{h['port']} inside a "
                  f"reconnect/novelty window (DNS leak watch alerts on it)")
    if baseline:
        print(f"{LOG_PREFIX} bypass: first pass, {len(findings)} existing finding(s) recorded as baseline")
        return []
    red = int(ev.get("dnslock_redirect_last_delta") or 0)
    if red:
        print(f"{LOG_PREFIX} bypass: {red} hard-coded plain-DNS quer(ies) redirected to Pi-hole by the dns-lock table")
    if not new:
        return []
    correlated = [f for f in new if near(float(f.get("first_seen", 0)))]
    label = {"dns53": "plain DNS to another server", "dot": "DNS over TLS (853/tcp)", "doq": "DNS over QUIC (853/udp)",
             "doh": "DNS over HTTPS to a known resolver"}
    lines = [f"- {label.get(f['class'], f['class'])}: {f['dest']} {f['proto']}/{f['port']}"
             + (f" ({f['rdns']})" if f.get("rdns") else "") + (f" from {f['host']}" if f.get("host") and not only else "")
             + (" [during a reconnect/novelty window]" if f in correlated else "")
             for f in new[:10]]
    print(f"{LOG_PREFIX} DNS BYPASS: {len(new)} new, {len(correlated)} correlated")
    ntfy_alert(
        cfg,
        f"The {cfg.get('DNS_WATCH_DEVICE_LABEL', 'monitored device')} resolved names outside Pi-hole:\n" + "\n".join(lines)
        + (f"\n(+{len(new) - 10} more)" if len(new) > 10 else "")
        + (f"\n{red} hard-coded plain-DNS quer(ies) were also redirected to Pi-hole this pass." if red else "")
        + "\nSource attribution is NOT established (the connection log cannot name the app).",
        "DNS anomaly -- DNS bypass" + (" during a reconnect burst" if correlated else ""),
        priority=5 if correlated else 4,
    )
    return new


def main():
    cfg = _cfg()
    if not _bool(cfg.get("UBER_WATCH_ENABLED"), True):
        return

    client_ip = cfg.get("UBER_WATCH_CLIENT_IP", "100.x.x.x")
    reconnect_gap_min = _float(cfg.get("UBER_WATCH_RECONNECT_GAP_MIN"), 20.0)
    burst_window_s = _float(cfg.get("UBER_WATCH_BURST_WINDOW_S"), 120.0)

    state = load_state()
    seen_domains = set(state.get("seen_domains", []))
    last_marker_ts = state.get("last_marker_ts", {})
    last_run_ts = state.get("last_run_ts", 0)
    now = time.time()

    # Denylist-gap check runs every pass regardless of first-run status --
    # cheap, read-only, and the one check that can't wait for a reconnect
    # or a fresh query to trigger it.
    missing = check_denylist_gaps()
    if missing:
        cmd = "pihole deny --comment \"Tracked anomaly endpoint\" " + " ".join(missing)
        print(f"{LOG_PREFIX} NOT YET DENYLISTED: {missing}")
        ntfy_alert(
            cfg,
            f"Tracked anomaly endpoint(s) not yet on the Pi-hole denylist: {missing}. "
            f"This script cannot add them itself (needs root). Run:\n{cmd}",
            "DNS anomaly watch -- denylist gap", priority=5,
        )

    first_run = not seen_domains
    if first_run:
        # Real historical seed -- every distinct domain this client has
        # EVER queried, not just the last few minutes, so the very next
        # run isn't flooded with "new" alerts for perfectly ordinary
        # domains this phone has used for weeks. No timestamp filter and
        # no burst/anomaly checks on this pass -- purely populating the
        # baseline.
        print(f"{LOG_PREFIX} first run -- seeding domain baseline from full history")
        seed_rows = query_pihole(
            f"SELECT DISTINCT domain FROM queries WHERE client = '{client_ip}';"
        )
        seen_domains = set(seed_rows)
        state["seen_domains"] = sorted(seen_domains)
        state["last_run_ts"] = now
        save_state(state)
        print(f"{LOG_PREFIX} seeded {len(seen_domains)} known domains -- next run starts real monitoring")
        return

    # Blocked-hit-frequency pass -- independent of the reconnect/novelty
    # logic below, runs every pass.
    blocked_hits = query_blocked_hits(client_ip, last_run_ts if last_run_ts else now - 300)
    if blocked_hits:
        freq_summary = track_blocked_hit_frequency(blocked_hits, state, now)
        print(f"{LOG_PREFIX} blocked-hit activity: {freq_summary}")

    # Pull everything this client queried since the last check.
    since = last_run_ts if last_run_ts else (now - 300)
    rows = query_pihole(
        f"SELECT timestamp, domain FROM queries "
        f"WHERE client = '{client_ip}' AND timestamp > {int(since)} "
        f"ORDER BY timestamp;"
    )
    hits = []
    for line in rows:
        try:
            ts_str, domain = line.split("|", 1)
            # 2026-08-12: pihole's `timestamp` column carries fractional
            # seconds ("1786562578.17515") -- int() on that string raises
            # ValueError, which this except swallowed every single time,
            # silently dropping every row for ~2.5 hours straight (looked
            # like "no traffic at all" when 1,883 real queries had fired
            # in that window). float() first, then truncate for the
            # reconnect-gap/burst-window integer math below.
            hits.append((int(float(ts_str)), domain))
        except ValueError:
            continue

    if not hits:
        print(f"{LOG_PREFIX} no new queries since last check")
        bypass_pass(cfg, state, now, client_ip, [])
        state["seen_domains"] = sorted(seen_domains)
        state["last_run_ts"] = now
        save_state(state)
        return

    # Reconnect detection.
    reconnect_windows = []
    for ts, domain in hits:
        if domain not in KNOWN_MARKER_DOMAINS:
            continue
        prev = last_marker_ts.get(domain, 0)
        if ts - prev >= reconnect_gap_min * 60:
            reconnect_windows.append((ts, domain))
            print(f"{LOG_PREFIX} reconnect detected: {domain} after "
                  f"{(ts - prev) / 60.0:.1f}min gap" if prev else
                  f"{LOG_PREFIX} reconnect detected: {domain} (first-ever hit)")
        last_marker_ts[domain] = ts

    anomalies = []
    novel_in_burst = []
    novel_elsewhere = []
    event_ts = [ts for ts, _ in reconnect_windows]       # for the DNS bypass correlation (feature off: unused)

    for ts, domain in hits:
        # 2026-10-03: Pi-hole's query log carries whatever a client asked
        # for, hostname or not -- a vault note filename ('20261003t011042z.md')
        # was logged as a 'new domain' today. A hostname has a dot and a
        # TLD of 2+ letters; anything else is skipped for novelty purposes.
        if not _looks_like_hostname(domain):
            continue
        is_new_domain = domain not in seen_domains
        in_burst = any(abs(ts - wts) <= burst_window_s for wts, _ in reconnect_windows)

        if domain.endswith(".uber.com") and in_burst:
            verdict = classify_uber_domain(domain)
            if verdict.startswith("ANOMALOUS"):
                anomalies.append((domain, verdict))
                event_ts.append(ts)

        if is_new_domain and not first_run:
            event_ts.append(ts)
            if in_burst:
                novel_in_burst.append(domain)
            else:
                novel_elsewhere.append(domain)

        seen_domains.add(domain)

    state["seen_domains"] = sorted(seen_domains)
    state["last_marker_ts"] = last_marker_ts
    state["last_run_ts"] = now
    save_state(state)

    if reconnect_windows:
        print(f"{LOG_PREFIX} {len(reconnect_windows)} reconnect event(s) this pass")

    if anomalies:
        anomalies = list({d: (d, v) for d, v in anomalies}.values())   # one entry per domain
        print(f"{LOG_PREFIX} ANOMALY: {anomalies}")
        for domain, _ in anomalies:
            pattern = track_discovery_pattern(
                state, {"domain": domain, "kind": "cname-drift"} if domain not in TRACKED_ANOMALY_ENDPOINTS else None, now,
            )
        save_state(state)
        pattern_note = (
            f" (discovery #{pattern['total_discovered']}, "
            f"{pattern['days_since_last']}d since last" +
            (f", mean gap {pattern['mean_gap_days']}d)" if pattern['mean_gap_days'] is not None else ")")
        )
        ntfy_alert(
            cfg,
            f"DNS burst from the {cfg.get('DNS_WATCH_DEVICE_LABEL', 'monitored device')} with non-standard routing:\n"
            + "\n".join(f"- {v}" for v in dict.fromkeys(v for _, v in anomalies)) +
            f"\nNo hop in the chain matches {cfg.get('DNS_WATCH_FAMILY_LABEL', 'the watched vendor')}'s known routing family. "
            f"Source attribution is NOT established (DNS cannot name the app)."
            + (f" Note: {cfg['DNS_WATCH_FAMILY_NOTE']}." if cfg.get("DNS_WATCH_FAMILY_NOTE") else "")
            + f"{pattern_note}",
            "DNS anomaly -- non-standard routing in a burst", priority=5,
        )
    if novel_in_burst:
        print(f"{LOG_PREFIX} new domain(s) during reconnect burst: {novel_in_burst}")
        for domain in novel_in_burst:
            pattern = track_discovery_pattern(
                state, {"domain": domain, "kind": "novel-in-burst"} if domain not in TRACKED_ANOMALY_ENDPOINTS else None, now,
            )
        save_state(state)
        pattern_note = (
            f" (discovery #{pattern['total_discovered']}, "
            f"{pattern['days_since_last']}d since last" +
            (f", mean gap {pattern['mean_gap_days']}d)" if pattern['mean_gap_days'] is not None else ")")
        )
        ntfy_alert(
            cfg,
            f"Domain(s) never seen before from the driver phone, during a DNS "
            f"reconnect burst (marker domains re-resolved after a gap; the app that caused "
            f"the burst is not established): {novel_in_burst}{pattern_note}",
            "DNS anomaly -- new domain in burst", priority=4,
        )
    if novel_elsewhere:
        print(f"{LOG_PREFIX} new domain(s) outside any burst (informational): {novel_elsewhere}")

    if cfg.get("DNS_BYPASS_WATCH_JSON", "").strip():
        bypass_pass(cfg, state, now, client_ip, event_ts)
        save_state(state)


if __name__ == "__main__":
    main()
