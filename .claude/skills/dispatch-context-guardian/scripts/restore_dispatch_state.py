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
    score = cps.get("score", "?")
    state = cps.get("state", "?")
    go = cps.get("go_no_go", "?")
    return f"{go.upper()} (score={score}, state={state})"


def fmt_feeds(feeds: dict) -> str:
    if not feeds:
        return "unavailable"
    lines = []
    feed_list = feeds.get("feeds", feeds)
    if isinstance(feed_list, list):
        for f in feed_list:
            name = f.get("name", "?")
            ok = "✓" if f.get("healthy", f.get("ok", False)) else "✗"
            age = f.get("age_seconds", "?")
            lines.append(f"  {ok} {name} (age={age}s)")
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
        notam = t.get("notam_id", t.get("id", "?"))
        ftype = t.get("type", "?")
        out.append(f"  • {notam} [{ftype}]")
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
        event = a.get("event", a.get("type", "?"))
        area = a.get("areaDesc", a.get("area", "?"))
        out.append(f"  • {event} — {area}")
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
            icao = s.get("station_id", s.get("icao", "?"))
            raw = s.get("raw_text", s.get("metar", ""))[:80]
            lines.append(f"  {icao}: {raw}")
        return "\n".join(lines)
    return str(wx)[:200]


def fmt_runsheet(rs) -> str:
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
        status = am.get("status", am.get("board_status", "?"))
        return str(status)[:120]
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
    print(f"  Status : {health.get('status', 'unknown')}")
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


def _check_ssh_key(saved_pubkey: str | None) -> None:
    """Ensure ~/.ssh/id_ed25519 exists; generate if missing. Always print pubkey."""
    import subprocess

    ssh_key   = os.path.expanduser("~/.ssh/cowork_ed25519")
    ssh_pub   = os.path.expanduser("~/.ssh/cowork_ed25519.pub")
    ssh_dir   = os.path.expanduser("~/.ssh")

    key_existed = os.path.exists(ssh_key)
    regenerated = False

    if not key_existed:
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
        result = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-C", "claude-cowork-dispatch",
             "-f", ssh_key, "-N", ""],
            capture_output=True, text=True
        )
        regenerated = result.returncode == 0

    # Read current pubkey
    current_pubkey = None
    if os.path.exists(ssh_pub):
        try:
            with open(ssh_pub) as f:
                current_pubkey = f.read().strip()
        except Exception:
            pass

    print()
    print("─── SSH KEY (claude-cowork-dispatch) ────────────────────")
    if regenerated:
        print("  STATUS : REGENERATED (was missing after compact)")
        print("  ACTION : Add this public key to Pi authorized_keys:")
        print()
        print(f"  echo \"{current_pubkey}\" >> ~/.ssh/authorized_keys")
    elif saved_pubkey and current_pubkey and saved_pubkey != current_pubkey:
        print("  STATUS : KEY CHANGED since last save")
        print("  ACTION : Re-add public key to Pi authorized_keys:")
        print()
        print(f"  echo \"{current_pubkey}\" >> ~/.ssh/authorized_keys")
    else:
        print("  STATUS : OK (key matches pre-compact snapshot)")

    if current_pubkey:
        print()
        print("  Public key:")
        print(f"  {current_pubkey}")
    print("─" * 65)


if __name__ == "__main__":
    main()
