#!/usr/bin/env python3
"""Summary-only restore; pure legacy display formatters kept for callers."""
from datetime import datetime,timezone

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

def _idle_by_design(f: dict, name: str, err, fresh_push: set):
    """2026-10-20: a feed that is not failing but idle by design, from the API's OWN per-feed
    fields (/api/v1/feeds): push_covered (the REST fallback while its push twin is fresh --
    NOTAM's push feed is push:fns, so pairing by identical name showed the notam REST
    fallback as failing), optional (DCA/IAD FIDS) and error 'awaiting_credentials' (a source
    not configured yet). The name pairing stays only for snapshots saved before the API
    carried push_covered."""
    if f.get("push_covered"):
        return "○", " (REST fallback idle; push covers)"
    if f.get("optional"):
        return "○", " (optional)"
    if err == "awaiting_credentials":
        return "○", " (not configured)"
    if "push_covered" not in f and not err and name in fresh_push:
        return "○", " (REST fallback idle; push covers)"
    return None

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
            if ok == "✗":
                ok, tail = _idle_by_design(f, name, err, fresh_push) or (ok, tail)
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
    import sys
    from context_hook import main as hook_main
    sys.argv.extend(["--action","restore"])
    return hook_main()

if __name__=="__main__":raise SystemExit(main())
