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

Two independent checks, both scoped to a window around a detected
reconnect (a KNOWN_MARKER_DOMAINS hit after RECONNECT_GAP_MIN of silence
from all of them):

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
     reconnect burst window rather than at some unrelated time.

Requires read access to /etc/pihole/pihole-FTL.db -- runs as a user in
the `pihole` group (one-time `sudo usermod -aG pihole corporatetraveldc`
-- read-only, no write access needed or used here). Runs via a 2-minute
systemd --user timer, same shape as thermal-ingest-guard.py /
governor-watch.py.

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
KNOWN_BLOCKED = {"fpfcpn2u.uber.com", "ak04a6qc.uber.com"}

# Already investigated, confirmed benign despite not matching the
# CNAME-family pattern (see module docstring).
KNOWN_SAFE_DIRECT = {"tracking.ibt.uber.com"}


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
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen_domains": [], "last_marker_ts": {}, "last_run_ts": 0}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def query_pihole(sql):
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
        ["sg", "pihole", "-c", f"sqlite3 -readonly '{PIHOLE_DB}' '{quoted}'"],
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
        last_run_ts = now
        save_state({"seen_domains": sorted(seen_domains), "last_marker_ts": last_marker_ts, "last_run_ts": now})
        print(f"{LOG_PREFIX} seeded {len(seen_domains)} known domains -- next run starts real monitoring")
        return

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
        save_state({"seen_domains": sorted(seen_domains), "last_marker_ts": last_marker_ts, "last_run_ts": now})
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

    save_state({
        "seen_domains": sorted(seen_domains),
        "last_marker_ts": last_marker_ts,
        "last_run_ts": now,
    })

    if reconnect_windows:
        print(f"{LOG_PREFIX} {len(reconnect_windows)} reconnect event(s) this pass")
    if anomalies:
        print(f"{LOG_PREFIX} ANOMALY: {anomalies}")
        ntfy_alert(
            cfg,
            f"Uber reconnect with non-standard routing: {anomalies}. "
            f"Doesn't match Uber's known frontends-cloud/cn-neg CNAME family.",
            "Uber Traffic Watch -- ANOMALY", priority=5,
        )
    if novel_in_burst:
        print(f"{LOG_PREFIX} new domain(s) during reconnect burst: {novel_in_burst}")
        ntfy_alert(
            cfg,
            f"New domain(s) never seen before from the driver phone, during "
            f"an Uber reconnect burst: {novel_in_burst}",
            "Uber Traffic Watch -- new domain in burst", priority=4,
        )
    if novel_elsewhere:
        print(f"{LOG_PREFIX} new domain(s) outside any burst (informational): {novel_elsewhere}")


if __name__ == "__main__":
    main()
