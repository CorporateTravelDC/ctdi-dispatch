#!/usr/bin/env python3
"""scripts/agent_timecard.py -- daily TIME CARD per agent (2026-10-11, operator).

The agent analogue of a call-center agent's daily performance sheet and of the
platform's own daily run sheet. Run at 03:50 UTC (after the 03:20 root agent-home
snapshot and the 03:40 backup) for the previous UTC day:

  shift       first/last activity (UTC + ET), active span
  work        gateway tool calls by tool and outcome, board posts, workspace
              contributions, signed requests (board.signed.request), council seats
  exceptions  refused / failed calls (what a supervisor reads first)
  custody     that day's home snapshot (SHA-256, size) and the backup archive

Outputs: /var/lib/corporatetraveldc/agent-timecards/<date>.json (0600), a
Markdown card in the vault (04-Syntheses/agent-timecards/<date>.md), and the
card's SHA-256 in the private backup ledger (tamper-evident). Read-only on the DB.
Run via scripts/with-dispatch-env.sh (DB + vault credentials).
"""
from __future__ import annotations

import argparse
import calendar
import collections
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = Path("/var/lib/corporatetraveldc/agent-timecards")
SNAP = Path("/var/lib/corporatetraveldc/agent-snapshots")


def _et(ts: float) -> str:
    from zoneinfo import ZoneInfo
    u = datetime.fromtimestamp(ts, timezone.utc)
    return f"{u:%H:%MZ} / {u.astimezone(ZoneInfo('America/New_York')):%H:%M ET}"


def summarize(rows: list[tuple[float, str, dict]], agents: list[str]) -> dict:
    """rows: (event_time, action, detail). Pure function -- tested."""
    cards = {a: {"calls": collections.Counter(), "exceptions": [], "signed": collections.Counter(),
                 "first": None, "last": None} for a in agents}
    for t, action, d in rows:
        a = d.get("account") if action == "agent.tool.call" else d.get("signer") if action == "board.signed.request" else None
        if a not in cards:
            continue
        c = cards[a]
        c["first"] = t if c["first"] is None else min(c["first"], t)
        c["last"] = t if c["last"] is None else max(c["last"], t)
        if action == "agent.tool.call":
            c["calls"][f"{d.get('tool')}:{d.get('outcome')}"] += 1
            if d.get("outcome") in ("refused", "failed"):
                c["exceptions"].append({"at": t, "tool": d.get("tool"), "outcome": d.get("outcome")})
        else:
            c["signed"][d.get("route", "?")] += 1
    out = {}
    for a, c in cards.items():
        tot = sum(c["calls"].values()) + sum(c["signed"].values())
        out[a] = {"active": tot > 0, "actions": tot,
                  "first": c["first"], "last": c["last"],
                  "span_h": round((c["last"] - c["first"]) / 3600, 2) if c["first"] else 0,
                  "calls": dict(sorted(c["calls"].items())), "signed_requests": dict(c["signed"]),
                  "contributions": c["calls"].get("workspace_contribute:ok", 0) + sum(v for k, v in c["signed"].items() if "workspace/contribute" in k),
                  "board_posts": c["calls"].get("board_post:ok", 0) + sum(v for k, v in c["signed"].items() if k.endswith("/api/v1/board")),
                  "exceptions": c["exceptions"]}
    return out


def snapshot_of(agent: str, day: str) -> dict | None:
    stamp = day.replace("-", "")
    fs = sorted((SNAP / agent).glob(f"{agent}-*.tar.zst")) if (SNAP / agent).is_dir() else []
    fs = [f for f in fs if f.name.split("-")[-1][:8] >= stamp] or fs[-1:]
    if not fs:
        return None
    f = fs[0]
    sha = (f.with_suffix(f.suffix + ".sha256").read_text().strip() if f.with_suffix(f.suffix + ".sha256").exists() else None)
    return {"file": f.name, "bytes": f.stat().st_size, "sha256": sha}


def render(day: str, cards: dict, archive: str | None) -> str:
    L = [f"# Agent time cards — {day} (UTC day)", "", f"Backup archive covering the snapshots: `{archive or 'n/a'}`.", "",
         "| Agent | Active | Shift (first → last) | Actions | Contributions | Board posts | Exceptions |", "|---|---|---|---|---|---|---|"]
    for a, c in sorted(cards.items()):
        shift = f"{_et(c['first'])} → {_et(c['last'])}" if c["first"] else "—"
        L.append(f"| {a} | {'yes' if c['active'] else 'no'} | {shift} | {c['actions']} | {c['contributions']} | {c['board_posts']} | {len(c['exceptions'])} |")
    for a, c in sorted(cards.items()):
        if not c["active"] and not c.get("snapshot"):
            continue
        L += ["", f"## {a}", f"- calls: {', '.join(f'{k} ×{v}' for k, v in c['calls'].items()) or 'none'}"]
        if c["signed_requests"]:
            L.append(f"- signed requests: {', '.join(f'{k} ×{v}' for k, v in c['signed_requests'].items())}")
        for e in c["exceptions"][:10]:
            L.append(f"- EXCEPTION {_et(e['at'])}: {e['tool']} {e['outcome']}")
        if c.get("snapshot"):
            s = c["snapshot"]; L.append(f"- custody: `{s['file']}` {s['bytes']} B, SHA-256 `{(s['sha256'] or '?')[:16]}…`")
    L += ["", "Generated by scripts/agent_timecard.py from the hash-chained audit_log (read-only). Hashes only; no message content."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--day", help="YYYY-MM-DD (UTC); default yesterday"); ap.add_argument("--no-vault", action="store_true")
    a = ap.parse_args(argv)
    day = a.day or time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    t0 = calendar.timegm(time.strptime(day, "%Y-%m-%d")); t1 = t0 + 86400
    sys.path.insert(0, str(REPO / "src"))
    from common import db
    with db.conn() as c:
        agents = [r["account"] for r in c.execute("SELECT account FROM board_signers WHERE account LIKE 'ctdc-agent-%' ORDER BY account").fetchall()]
        raw = c.execute("SELECT event_time, action, detail FROM audit_log WHERE event_time >= ? AND event_time < ? "
                        "AND action IN ('agent.tool.call', 'board.signed.request') ORDER BY event_time", (t0, t1)).fetchall()
    rows = [(r["event_time"], r["action"], r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")) for r in raw]
    cards = summarize(rows, agents)
    for ag in cards:
        cards[ag]["snapshot"] = snapshot_of(ag, day)
    arch = subprocess.run([sys.executable, str(REPO / "scripts/backup/backup_ledger.py"), "last", "backup"], capture_output=True, text=True).stdout
    archive = ((json.loads(arch or "{}").get("data") or {}).get("archive") or {}).get("name")
    doc = {"day": day, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "archive": archive, "agents": cards}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    blob = json.dumps(doc, indent=1, sort_keys=True).encode()
    f = OUT / f"{day}.json"; f.write_bytes(blob); f.chmod(0o600)
    md = render(day, cards, archive)
    if not a.no_vault:
        from second_brain import scrub_gate, webdav_client as w
        scrub_gate.gate(md, source="agent-timecard")
        p = f"{w.BUSINESS_ROOT}/04-Syntheses/agent-timecards/{day}.md"; w.mkdirs(p.rsplit("/", 1)[0]); w.put(p, md)
    subprocess.run([sys.executable, str(REPO / "scripts/backup/backup_ledger.py"), "append", "note", "--json",
                    json.dumps({"what": "agent-timecard", "day": day, "json_sha256": hashlib.sha256(blob).hexdigest(),
                                "md_sha256": hashlib.sha256(md.encode()).hexdigest()})], check=False, capture_output=True)
    act = [k for k, v in cards.items() if v["active"]]
    print(f"agent-timecard {day}: {len(cards)} agents, {len(act)} active, {sum(len(v['exceptions']) for v in cards.values())} exceptions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
