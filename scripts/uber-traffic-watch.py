#!/usr/bin/env python3
"""
uber-traffic-watch.py -- watches Pi-hole's live query log for the driver
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
STATE_FILE = "/var/lib/corporatetraveldc/uber_traffic_watch_state.json"
LOG_PREFIX = "uber-traffic-watch:"

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
KNOWN_BLOCKED = {"fpfcpn2u.uber.com", "ak04a6qc.uber.com"}

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
    "lens.usercontent.google.com": {"discovered": "2026-08-29", "kind": "novel-in-burst"},
    "rr1---sn-p5qs7nzr.gvt1.com": {"discovered": "2026-08-29", "kind": "novel-in-burst"},
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
    """Live CNAME + first A record for a domain. Returns (cname_or_None, ip_or_None)."""
    cname = subprocess.run(["dig", "+short", domain, "CNAME"], capture_output=True, text=True, timeout=10).stdout.strip()
    a = subprocess.run(["dig", "+short", domain, "A"], capture_output=True, text=True, timeout=10).stdout.strip()
    cname_target = cname.splitlines()[0].rstrip(".") if cname else None
    first_ip = a.splitlines()[0] if a else None
    return cname_target, first_ip


def classify_uber_domain(domain):
    """Returns one of: known-good, known-blocked, known-safe-direct, ANOMALOUS."""
    if domain in KNOWN_BLOCKED:
        return "known-blocked"
    if domain in KNOWN_SAFE_DIRECT:
        return "known-safe-direct"
    cname_target, ip = cname_chain(domain)
    if cname_target in KNOWN_GOOD_CNAME_TARGETS:
        return "known-good"
    if ip == "0.0.0.0":
        return "known-blocked"  # denylisted by something else already
    return f"ANOMALOUS (cname={cname_target!r}, ip={ip!r})"


def check_denylist_gaps():
    """Read-only check (gravity.db) for which TRACKED_ANOMALY_ENDPOINTS
    entries are NOT yet actually denylisted. This script cannot add them
    itself -- `pihole deny` requires root/the pihole user, and this
    script deliberately does not attempt any privilege escalation to get
    there (see module docstring). Returns the list of missing domains so
    main() can alert loudly with the exact command to run."""
    rows = query_pihole("SELECT domain FROM domainlist WHERE type=1;", db=GRAVITY_DB)
    denylisted = set(rows)
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
    req.add_header("X-Tags", "taxi,mag")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"{LOG_PREFIX} ntfy push failed (non-fatal): {e}")


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
            "Uber Traffic Watch -- denylist gap", priority=5,
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

    for ts, domain in hits:
        is_new_domain = domain not in seen_domains
        in_burst = any(abs(ts - wts) <= burst_window_s for wts, _ in reconnect_windows)

        if domain.endswith(".uber.com") and in_burst:
            verdict = classify_uber_domain(domain)
            if verdict.startswith("ANOMALOUS"):
                anomalies.append((domain, verdict))

        if is_new_domain and not first_run:
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
            f"Uber reconnect with non-standard routing: {anomalies}. "
            f"Doesn't match Uber's known frontends-cloud/cn-neg CNAME family."
            f"{pattern_note}",
            "Uber Traffic Watch -- ANOMALY", priority=5,
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
            f"New domain(s) never seen before from the driver phone, during "
            f"an Uber reconnect burst: {novel_in_burst}{pattern_note}",
            "Uber Traffic Watch -- new domain in burst", priority=4,
        )
    if novel_elsewhere:
        print(f"{LOG_PREFIX} new domain(s) outside any burst (informational): {novel_elsewhere}")


if __name__ == "__main__":
    main()
