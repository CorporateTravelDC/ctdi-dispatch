"""common.gateway_hold -- the cloud-agent link keeper (2026-11-27, operator).

Cloud agents (Cowork, ChatGPT) reach the platform only through the agent gateway, under their own OAuth link. The
link is kept open by an operator-signed HOLD (`agent-gateway.sh hold SLUG --days 90`, at most 90 days; since
2026-11-27 it lasts its full term). This sentinel runs daily on the box and, for each watched connector, reminds the
operator BEFORE anything lapses, so there is always a week to act:

  gateway_hold_ending   the hold ends within REMIND_DAYS (7)         -> renew it now: hold SLUG --days 90
  gateway_relink_due    no live hold, and the refresh grant idles out within REMIND_DAYS
                                                                     -> hold it now, or the vendor must re-link
  gateway_hold_missing  a watched connector has no hold at all        -> hold it (once a week until it is)
  gateway_link_quiet    the TRIPWIRE, per connector (GATEWAY_LINK_QUIET, hours; 0 = off): no call or renewal for
                        that long. Cowork runs scheduled tasks every day, so its daily call is its check-in and
                        30 h of silence means a task missed it (default cowork=30). ChatGPT is used now and then,
                        so it has no quiet tripwire (chatgpt=0): its 90-day hold and the reminders keep it linked.

Each reminder is sent once per target (the hold's end date, the grant's end date, the ISO week, the quiet episode)
as an ops-health push plus a board coord post. A revoked or never-linked connector is reported, not reminded: that
needs a re-link, which only the operator starts from the vendor's settings.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

from common import platform_sentinels as ps

NAME = "gateway-hold"
REMIND_DAYS = float(os.environ.get("GATEWAY_HOLD_REMIND_DAYS", "7"))
QUIET_H = {k.strip(): float(v) for k, v in (x.split("=", 1) for x in os.environ.get(
    "GATEWAY_LINK_QUIET", "cowork=30,chatgpt=0").split(",") if "=" in x)}
WATCH = [s.strip() for s in os.environ.get("GATEWAY_HOLD_SLUGS", "cowork,chatgpt").split(",") if s.strip()]
STATE = Path(os.environ.get("GATEWAY_HOLD_STATE", str(ps.REPORTS_DIR / "gateway-hold-state.json")))
DAY = 86400


def _day(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
    os.replace(tmp, STATE)


def connections(gw=None) -> list[dict]:
    """The newest non-revoked connection per watched slug (or the newest of any state, to report a revocation)."""
    from common import agent_gateway
    gw = gw or agent_gateway
    out = []
    for slug in WATCH:
        rows = gw.status(slug)
        live = [r for r in rows if r["status"] != "revoked"]
        r = (live or rows or [None])[0]
        if r is None:
            out.append({"slug": slug, "status": "never-linked", "account": ""})
            continue
        r = dict(r)
        r["grant_until"] = gw.refresh_grant_until(r["id"]) if r["status"] != "revoked" else None
        out.append(r)
    return out


def due(rows: list[dict], now: float) -> list[dict]:
    """Reminders that are due now: [{kind, slug, key, title, message, priority}] (not yet de-duplicated)."""
    week = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%G-W%V")
    out = []
    for r in rows:
        slug, acct = r["slug"], r.get("account") or r["slug"]
        hold_cmd = f"scripts/agent-gateway.sh hold {slug} --days 90"
        if r["status"] in ("revoked", "never-linked"):
            out.append({"kind": "gateway_hold_missing", "slug": slug, "key": f"{r['status']}:{week}", "priority": 3,
                        "title": f"Agent link {slug}: {r['status']}",
                        "message": f"{acct} ({slug}) is {r['status']}: it needs a re-link from the vendor's own connector "
                                   f"settings, then your signature (scripts/approve.sh allow <id>). Then: {hold_cmd}"})
            continue
        hold = r.get("hold_until") or 0
        grant = r.get("grant_until") or 0
        last = max(r.get("last_renewal_at") or 0, r.get("last_call_at") or 0)
        if hold > now and hold - now <= REMIND_DAYS * DAY:
            out.append({"kind": "gateway_hold_ending", "slug": slug, "key": f"hold:{_day(hold)}", "priority": 3,
                        "title": f"Agent link {slug}: hold ends {_day(hold)}",
                        "message": f"The hold on {acct} ({slug}) ends {_day(hold)} UTC, in {(hold - now) / DAY:.1f} days. "
                                   f"Renew it now (your signature): {hold_cmd}"})
        elif hold <= now and grant and grant - now <= REMIND_DAYS * DAY:
            out.append({"kind": "gateway_relink_due", "slug": slug, "key": f"grant:{_day(grant)}", "priority": 4,
                        "title": f"Agent link {slug}: re-link needed by {_day(grant)}",
                        "message": f"{acct} ({slug}) has no hold and its refresh grant idles out {_day(grant)} UTC "
                                   f"({(grant - now) / DAY:.1f} days), after which the vendor must re-link. Hold it now: {hold_cmd}"})
        elif hold <= now:
            out.append({"kind": "gateway_hold_missing", "slug": slug, "key": f"nohold:{week}", "priority": 2,
                        "title": f"Agent link {slug}: no hold",
                        "message": f"{acct} ({slug}) has no hold, so a quiet week makes it dormant and 30 quiet days need "
                                   f"a re-link. Hold it (your signature): {hold_cmd}"})
        qh = QUIET_H.get(slug, 0)
        if qh and last and now - last >= qh * 3600:
            out.append({"kind": "gateway_link_quiet", "slug": slug, "key": f"quiet:{_day(last)}", "priority": 3,
                        "title": f"Agent link {slug}: quiet since {_day(last)}",
                        "message": f"{acct} ({slug}) has made no connector call or renewal since "
                                   f"{dt.datetime.fromtimestamp(last, dt.timezone.utc):%Y-%m-%d %H:%MZ}. Its scheduled tasks "
                                   f"are not reaching the connector (a daily task missed its check-in): check the connector in "
                                   f"the vendor's settings (re-link if it says signed out), and that each task ends with "
                                   f"workspace_contribute."})
    return out


def run(now: float | None = None, gw=None) -> list[dict]:
    """One pass: send each due reminder once (ops-health push + board coord post); returns what was sent."""
    from common import db
    now = now or time.time()
    rows = connections(gw)
    st = _load()
    sent = []
    for r in due(rows, now):
        sk = f"{r['slug']}:{r['kind']}"
        if st.get(sk) == r["key"]:
            continue
        ps.push(r["kind"], r["message"], title=r["title"], priority=r["priority"], entity=r["slug"],
                detail={"slug": r["slug"], "key": r["key"]})
        try:
            db.board_insert(from_side="agent-gateway", to_side="dispatch", thread="coord", subject=r["title"], body=r["message"])
        except Exception as e:   # noqa: BLE001 -- the push already went out
            ps.log.warning("%s: board post failed (%s)", NAME, type(e).__name__)
        st[sk] = r["key"]
        sent.append(r)
    _save(st)
    ps.write_report(NAME, {"generated": now, "connections": [
        {k: x.get(k) for k in ("slug", "account", "status", "hold_until", "grant_until", "last_renewal_at", "last_call_at")}
        for x in rows], "sent": sent})
    return sent


def current(gw=None) -> list[dict]:
    return connections(gw)
