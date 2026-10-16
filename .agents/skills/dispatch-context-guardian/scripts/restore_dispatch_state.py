#!/usr/bin/env python3
"""
Read the dispatch state snapshot and print a structured context injection.
Run after /compact or /clear to restore dispatch situational awareness.
"""

import json
import os
import sys
from datetime import datetime, timezone

STATE_FILE = os.path.expanduser("~/.config/Claude/dispatch_state_snapshot.json")


def age_str(saved_at: str) -> str:
    try:
        saved = datetime.fromisoformat(saved_at)
        now = datetime.now(timezone.utc)
        delta = now - saved
        mins = int(delta.total_seconds() / 60)
        if mins < 60:
            return f"{mins}m ago"
        return f"{mins // 60}h {mins % 60}m ago"
    except Exception:
        return saved_at


def fmt_cps(cps: dict) -> str:
    if not cps:
        return "unavailable"
    # API shape (2026-10-03): {score, label, factors{...}, narrative, computed_at}
    score = cps.get("score", "?")
    label = cps.get("label", cps.get("state", "?"))
    out = f"{label} (score={score})"
    if cps.get("narrative"):
        out += f"\n  {str(cps['narrative'])[:160]}"
    return out


def fmt_feeds(feeds: dict) -> str:
    if not feeds:
        return "unavailable"
    lines = []
    feed_list = feeds.get("feeds", feeds)
    if isinstance(feed_list, list):
        # REST pull feeds (nws, amtrak, notam, tfr, nas) are failover
        # fallbacks for a push:<name> twin -- idle-by-design while the push
        # path is fresh (ingest/failover.py). /healthz already excludes
        # them from "stale"; show them as idle rather than failed.
        fresh_push = set()
        for f in feed_list:
            n = f.get("feed_name", "")
            a, t = f.get("age_seconds"), f.get("stale_threshold_seconds")
            if n.startswith("push:") and a is not None and t is not None and a <= t:
                fresh_push.add(n[5:])
        for f in feed_list:
            # API shape (2026-10-03): feed_name / age_seconds /
            # stale_threshold_seconds / error / consecutive_failures
            name = f.get("feed_name", f.get("name", "?"))
            age = f.get("age_seconds")
            thr = f.get("stale_threshold_seconds")
            err = f.get("error") or f.get("pull_error")
            fresh = (age is not None and thr is not None and age <= thr)
            ok = "✓" if (fresh and not err) else "✗"
            tail = f" err={str(err)[:40]}" if err else ""
            if ok == "✗" and not err and name in fresh_push:
                ok, tail = "○", " (REST fallback idle; push covers)"
            lines.append(f"  {ok} {name} (age={age}s/{thr}s){tail}")
    elif isinstance(feed_list, dict):
        for name, info in feed_list.items():
            ok = "✓" if (info or {}).get("healthy", False) else "✗"
            lines.append(f"  {ok} {name}")
    return "\n".join(lines) if lines else "no feeds"


def fmt_tfr(tfr) -> str:
    if not tfr:
        return "none active"
    tfrs = tfr if isinstance(tfr, list) else tfr.get("tfrs", [])
    if not tfrs:
        return "none active"
    out = []
    for t in tfrs[:10]:  # cap at 10
        notam = t.get("tfr_id", t.get("notam_id", t.get("id", "?")))
        ftype = "VIP" if t.get("is_vip") else t.get("type", "")
        end = str(t.get("effective_end") or "")[:16]
        line = f"  • {notam}"
        if ftype:
            line += f" [{ftype}]"
        if end:
            line += f" ends {end}"
        out.append(line)
    if len(tfrs) > 10:
        out.append(f"  ... and {len(tfrs) - 10} more")
    return "\n".join(out)


def fmt_alerts(alerts) -> str:
    if not alerts:
        return "none"
    items = alerts if isinstance(alerts, list) else alerts.get("alerts", [])
    if not items:
        return "none"
    out = []
    for a in items[:5]:
        event = a.get("event_type", a.get("event", a.get("type", "?")))
        area = a.get("area_desc", a.get("areaDesc", a.get("area", "?")))
        sev = a.get("severity")
        out.append(f"  • {event}{' ('+sev+')' if sev else ''} — {str(area)[:70]}")
    if len(items) > 5:
        out.append(f"  ... and {len(items) - 5} more")
    return "\n".join(out)


def fmt_weather(wx) -> str:
    if not wx:
        return "unavailable"
    stations = wx if isinstance(wx, list) else wx.get("stations", wx.get("metars", []))
    if isinstance(stations, list) and stations:
        lines = []
        for s in stations[:6]:
            icao = s.get("station", s.get("station_id", s.get("icao", "?")))
            raw = s.get("raw_text", s.get("metar"))
            if raw:
                lines.append(f"  {icao}: {raw[:80]}")
            else:
                lines.append(f"  {icao}: ceil {s.get('ceiling_ft','?')}ft  vis {s.get('visibility_sm','?')}sm"
                             f"  wind {s.get('wind_kt','?')}kt  precip {s.get('precip_code') or '-'}")
        return "\n".join(lines)
    return str(wx)[:200]


def fmt_runsheet(rs) -> str:
    if rs is None:
        return "not captured (Tier-1 /api/v1/runsheet 403 -- no DISPATCH_ADMIN_TOKEN in hook env)"
    if not rs:
        return "no active trips"
    trips = rs if isinstance(rs, list) else rs.get("trips", rs.get("entries", []))
    if not trips:
        return "no active trips"
    out = []
    for t in trips[:5]:
        name = t.get("client", t.get("name", "?"))
        pu = t.get("pickup_time", t.get("time", "?"))
        out.append(f"  • {name} @ {pu}")
    return "\n".join(out)


def fmt_amtrak(am) -> str:
    if not am:
        return "unavailable"
    if isinstance(am, dict):
        summ = am.get("summary") or am.get("status") or am.get("board_status") or "?"
        trains = am.get("trains") or []
        late = [t for t in trains if (t.get("delay_minutes") or 0) >= 15]
        out = f"{str(summ)[:120]}"
        if trains:
            out += f"\n  {len(trains)} trains tracked, {len(late)} delayed >=15m"
            for t in late[:5]:
                out += f"\n  • {t.get('train_num','?')} {t.get('route','')} +{t.get('delay_minutes')}m {t.get('status','')}"
        return out
    return str(am)[:120]


def main():
    if not os.path.exists(STATE_FILE):
        print("No dispatch state snapshot found.")
        print(f"Expected: {STATE_FILE}")
        print("Run save_dispatch_state.py first, or re-poll dispatch endpoints.")
        sys.exit(0)

    with open(STATE_FILE) as f:
        state = json.load(f)

    saved_at = state.get("saved_at", "unknown")
    token_est = state.get("session_token_estimate")

    print("=" * 65)
    print("DISPATCH STATE SNAPSHOT — CONTEXT RESTORED")
    print("=" * 65)
    print(f"Snapshot age : {age_str(saved_at)}  ({saved_at})")
    if token_est:
        print(f"Saved at     : {token_est:,} tokens")
    print()

    print("─── SERVICE HEALTH ───────────────────────────────────────")
    health = state.get("health") or {}
    print(f"  Status : {health.get('status', 'unknown')}"
          + (f"  ({health.get('reason')})" if health.get("reason") else ""))
    snap_age = health.get("snapshot_age_seconds", health.get("age", "?"))
    print(f"  Snap   : {snap_age}s old")

    print()
    print("─── FEED STATUS ──────────────────────────────────────────")
    print(fmt_feeds(state.get("feeds")))

    print()
    print("─── CRITICAL PREDICTABILITY STATE (CPS) ─────────────────")
    print(f"  {fmt_cps(state.get('cps'))}")

    print()
    print("─── ACTIVE TFRs ──────────────────────────────────────────")
    print(fmt_tfr(state.get("tfr")))

    print()
    print("─── NWS ALERTS ───────────────────────────────────────────")
    print(fmt_alerts(state.get("alerts")))

    print()
    print("─── WEATHER (DC-AREA METARs) ─────────────────────────────")
    print(fmt_weather(state.get("weather")))

    print()
    print("─── AMTRAK (WAS) ─────────────────────────────────────────")
    print(f"  {fmt_amtrak(state.get('amtrak'))}")

    print()
    print("─── RUNSHEET ─────────────────────────────────────────────")
    print(fmt_runsheet(state.get("runsheet")))

    print()
    print("=" * 65)
    print("NOTE: This snapshot may be stale. Re-poll if needed.")
    print(f"      Full state: {STATE_FILE}")
    print("=" * 65)

    # SSH key check — always run after restore
    _check_ssh_key(state.get("ssh_pubkey"))


def _signing_key_paths() -> tuple[str, str]:
    """The account's OWN signing key, ~/.ssh/<account>_ed25519 (team
    segmentation 2026-10-04: one key per account, comment account@host).
    The shared cowork_ed25519 is retired as an identity and is never used."""
    import getpass
    key = os.path.expanduser(f"~/.ssh/{getpass.getuser()}_ed25519")
    return key, key + ".pub"


def _check_ssh_key(saved_pubkey: str | None) -> None:
    """Report the account's signing key. Never generates one and never
    suggests editing authorized_keys: keys are provisioned by the operator
    (plan.sh) and a self-authorized key would defeat attribution."""
    ssh_key, ssh_pub = _signing_key_paths()
    current_pubkey = None
    if os.path.exists(ssh_pub):
        try:
            with open(ssh_pub) as f:
                current_pubkey = f.read().strip()
        except Exception:
            pass

    print()
    print(f"─── SIGNING KEY ({os.path.basename(ssh_key)}) ─────────────────")
    if not os.path.exists(ssh_key) or not current_pubkey:
        print("  STATUS : MISSING -- do not generate one; tell the operator")
        print("           (plan.sh provisions it and registers the signer)")
    elif saved_pubkey and saved_pubkey != current_pubkey:
        print("  STATUS : CHANGED since the pre-compact snapshot -- stop and")
        print("           tell the operator (board-signer-ctl.sh show <account>)")
    else:
        print("  STATUS : OK (matches pre-compact snapshot)")
    if current_pubkey:
        print(f"  Public : {current_pubkey}")
    print("─" * 65)


if __name__ == "__main__":
    main()
