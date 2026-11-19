"""common.agent_lapse -- agent-lapse sentinel with escalation and dismissal (2026-11-18, operator).

The 11-04..11-17 lapse was on the agent side (a cloud-provider subscription), so nothing an agent
runs could notice it. This runs on the box every 30 minutes and, per agent account, measures the
last activity it can see:

  board        last board post by the account          (board_messages.from_side)
  tools        last connector tool call by the account (audit_log agent.tool.call, detail.account)
  signed       last signed on-box request              (audit_log board.signed.request, detail.signer)

and escalates on silence (thresholds in hours, configurable):

  NUDGE_H    6   ntfy priority 2 on ops-health + a board coord post addressed to the agent
  EMAIL_H   12   ntfy priority 3 WITH email to the operator (ntfy's SMTP relay, X-Email)
  ESCALATE_H 24  priority 4 alert + board nudge + email, with escalatory information: last activity
                 of each kind, liveness factor states (inert marker, kill orders, signer active,
                 token revocations in the last 24 h), whether every account of one vendor is silent
                 together (a cloud-provider subscription lapse), and the operator's exact commands

Each stage is sent once per silence episode (the episode is the last-activity time), and again only
after activity resumes and a new silence begins. An inert account (team-liveness marker) is listed
in the report but not escalated: it is already known to be off (AGENT_LAPSE_ESCALATE_INERT=1 changes
that).

Dismissal, two ways, both the operator's own authentication:
  SSH   scripts/ctdc-agent-lapse.sh dismiss --account X [--for 24h] --reason TEXT  (operator account only)
  ntfy  the escalation push carries a "view" button that opens the operator console
        (/console?lapse=X); the console requires its normal sign-in (a human SSH-signed
        console-login approval) and a CSRF-checked form. A tap on the push alone never dismisses.
A dismissal is recorded (who, when, via, reason, until) and holds until it ends or the account
shows activity again.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from common import platform_sentinels as ps

NAME = "agent-lapse"
NUDGE_H = float(os.environ.get("AGENT_LAPSE_NUDGE_H", "6"))
EMAIL_H = float(os.environ.get("AGENT_LAPSE_EMAIL_H", "12"))
ESCALATE_H = float(os.environ.get("AGENT_LAPSE_ESCALATE_H", "24"))
LOOKBACK_S = 21 * 86400
MAX_DISMISS_S = 30 * 86400
STAGES = ("none", "nudge", "email", "escalate")
SERVICES = {a.strip() for a in os.environ.get("AGENT_LAPSE_EXCLUDE", "ctdc-agent-dispatch,ctdc-agent-llama").split(",")
            if a.strip()}
REGISTRY = Path(os.environ.get("CTDC_ACCOUNTS_CONF", "/etc/ctdc-accounts.conf"))
LIVENESS_DIR = Path(os.environ.get("CTDC_LIVENESS_DIR", "/var/lib/corporatetraveldc/team-liveness"))
REPO = os.environ.get("CTDC_REPO", "/opt/corporatetraveldc/private/ctdi-dispatch-internal")
CONSOLE_HOST = (os.environ.get("CONSOLE_HOSTS", "corporatetraveldc-dispatch.tailxxxxxxx.ts.net").split(",")[0]
                .strip())
ACCOUNT_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")


def ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return                                   # pg_schema/0088_platform_sentinels.sql
    c.execute("""CREATE TABLE IF NOT EXISTS agent_lapse_state (account TEXT PRIMARY KEY, last_activity REAL,
        stage TEXT NOT NULL, episode TEXT, stage_sent TEXT, detail TEXT, updated_at REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agent_lapse_dismissals (id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL, dismissed_at REAL NOT NULL, until_at REAL NOT NULL, by_who TEXT NOT NULL,
        via TEXT NOT NULL, reason TEXT NOT NULL)""")


def vendor_of(account: str) -> str:
    parts = account.split("-")
    return parts[2] if len(parts) >= 4 and parts[:2] == ["ctdc", "agent"] else "other"


def stage_for(silent_s: float | None) -> str:
    if silent_s is None:
        return "escalate"
    h = silent_s / 3600.0
    if h >= ESCALATE_H:
        return "escalate"
    if h >= EMAIL_H:
        return "email"
    if h >= NUDGE_H:
        return "nudge"
    return "none"


# ---------------------------------------------------------------- sources (read-only)
def accounts() -> list[str]:
    """Agent accounts: active board signers of kind agent, plus registry rows; services excluded."""
    from common import db
    names: set[str] = set()
    try:
        for s in db.board_signer_list():
            if s.get("active") and (s.get("kind") or "agent") == "agent":
                names.add(s["account"])
    except Exception as e:   # noqa: BLE001
        ps.log.warning("%s: signer list unavailable (%s)", NAME, type(e).__name__)
    try:
        for line in REGISTRY.read_text().splitlines():
            f = line.split()
            if f and not f[0].startswith("#") and f[0].startswith("ctdc-agent-"):
                names.add(f[0])
    except OSError:
        pass
    return sorted(n for n in names if n not in SERVICES and ACCOUNT_RE.fullmatch(n))


def last_activity(names: list[str], now: float) -> dict[str, dict]:
    from common import db
    out = {n: {"board": None, "tools": None, "signed": None} for n in names}
    since_iso = ps.dt.datetime.fromtimestamp(now - LOOKBACK_S, ps.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.conn() as c:
        for r in c.execute("SELECT from_side, MAX(ts) AS ts FROM board_messages WHERE ts >= ? GROUP BY from_side",
                           (since_iso,)).fetchall():
            if r["from_side"] in out:
                from common import flight_instance as fi
                out[r["from_side"]]["board"] = fi.to_epoch(r["ts"])
        rows = c.execute("SELECT action, event_time, detail FROM audit_log WHERE event_time >= ? "
                         "AND action IN ('agent.tool.call', 'board.signed.request')",
                         (now - LOOKBACK_S,)).fetchall()
    for r in rows:
        try:
            d = json.loads(r["detail"] or "{}")
        except ValueError:
            continue
        who = d.get("account") if r["action"] == "agent.tool.call" else d.get("signer")
        kind = "tools" if r["action"] == "agent.tool.call" else "signed"
        if who in out and r["event_time"] and (out[who][kind] or 0) < float(r["event_time"]):
            out[who][kind] = float(r["event_time"])
    return out


def liveness(account: str, now: float) -> dict:
    """Liveness factor STATES only (never key material or token values)."""
    st = {"inert": None, "kill_orders": None, "signer_active": None, "tokens_revoked_24h": None,
          "login_refresh": "root-only: see team-liveness.sh --status"}
    m = LIVENESS_DIR / "inert" / account
    try:
        st["inert"] = m.read_text().strip()[:160] if m.exists() else False
    except OSError:
        st["inert"] = "unreadable"
    od = LIVENESS_DIR / "orders" / account
    try:
        st["kill_orders"] = sum(1 for p in od.iterdir() if p.is_file()) if od.is_dir() else 0
    except OSError:
        st["kill_orders"] = "unreadable"
    try:
        from common import db
        row = db.board_signer_get(account)
        st["signer_active"] = bool(row and row.get("active"))
        st["tokens_revoked_24h"] = db.board_tokens_revoked_since(account, now - 86400)
    except Exception as e:   # noqa: BLE001
        st["signer_active"] = f"unavailable ({type(e).__name__})"
    return st


# ---------------------------------------------------------------- state + dismissals
def _state(account: str) -> dict | None:
    from common import db
    with db.conn() as c:
        ensure(c)
        r = c.execute("SELECT * FROM agent_lapse_state WHERE account = ?", (account,)).fetchone()
    return dict(r) if r else None


def _save(account: str, last: float | None, stage: str, episode: str, stage_sent: str, detail: dict,
          now: float) -> None:
    from common import db
    with db.conn() as c:
        ensure(c)
        c.execute("""INSERT INTO agent_lapse_state (account, last_activity, stage, episode, stage_sent, detail, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(account) DO UPDATE SET last_activity=excluded.last_activity, stage=excluded.stage,
                       episode=excluded.episode, stage_sent=excluded.stage_sent, detail=excluded.detail,
                       updated_at=excluded.updated_at""",
                  (account, last, stage, episode, stage_sent, json.dumps(detail, default=str), now))


def active_dismissal(account: str, last: float | None, now: float) -> dict | None:
    from common import db
    with db.conn() as c:
        ensure(c)
        rows = c.execute("SELECT * FROM agent_lapse_dismissals WHERE account = ? AND until_at > ? "
                         "ORDER BY dismissed_at DESC", (account, now)).fetchall()
    for r in rows:
        if last is None or float(r["dismissed_at"]) >= last:   # activity after a dismissal ends it
            return dict(r)
    return None


def parse_for(text: str | None) -> float:
    """'24h', '90m', '3d' -> seconds; default 24 h; bounded to 30 days."""
    t = (text or "24h").strip().lower()
    m = re.fullmatch(r"(\d{1,4})([mhd])", t)
    if not m:
        raise ValueError("--for must look like 90m, 24h or 3d")
    s = int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if s <= 0 or s > MAX_DISMISS_S:
        raise ValueError("--for must be between 1 minute and 30 days")
    return float(s)


def dismiss(account: str, for_s: float, reason: str, by: str, via: str, now: float | None = None) -> dict:
    account = (account or "").strip()
    reason = " ".join((reason or "").split())[:200]
    if not ACCOUNT_RE.fullmatch(account):
        raise ValueError("unknown account name")
    if not reason:
        raise ValueError("a reason is required (it is recorded)")
    if not (0 < for_s <= MAX_DISMISS_S):
        raise ValueError("dismissal window must be between 1 minute and 30 days")
    now = ps.now() if now is None else now
    from common import db
    with db.conn() as c:
        ensure(c)
        c.execute("INSERT INTO agent_lapse_dismissals (account, dismissed_at, until_at, by_who, via, reason) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (account, now, now + for_s, by[:80], via[:20], reason))
    try:
        from common import governance
        governance.audit("agent_lapse.dismissed", {"account": account, "by": by[:80], "via": via[:20],
                                                   "until": now + for_s})
    except Exception:   # noqa: BLE001
        pass
    return {"account": account, "until": now + for_s, "until_fmt": ps.fmt(now + for_s), "by": by, "via": via}


def current() -> list[dict]:
    """State rows for the console (newest silence first)."""
    from common import db
    with db.conn() as c:
        ensure(c)
        rows = c.execute("SELECT * FROM agent_lapse_state ORDER BY last_activity ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except ValueError:
            d["detail"] = {}
        out.append(d)
    return out


# ---------------------------------------------------------------- messages
def commands(account: str) -> list[str]:
    relink = f"{REPO}/scripts/relink/{account}.sh"
    cmds = [f"sudo /usr/local/libexec/ctdc/team-liveness.sh --status --user {account}",
            f"sudo /usr/local/libexec/ctdc/team-liveness.sh --reactivate {account} --i-am-the-operator",
            f"{REPO}/scripts/ctdc-agent-lapse.sh dismiss --account {account} --for 24h --reason \"why\""]
    if Path(relink).exists():
        cmds.insert(2, f"bash {relink}")
    return cmds


def escalation_text(account: str, act: dict, live: dict, silent_s: float | None, vendor_lapse: bool) -> str:
    lines = [f"Agent account {account} has been silent for {ps.hours(silent_s)}.",
             f"Last board post: {ps.fmt(act.get('board'))}",
             f"Last connector tool call: {ps.fmt(act.get('tools'))}",
             f"Last signed on-box request: {ps.fmt(act.get('signed'))}",
             f"Liveness: inert={live.get('inert')}, kill orders={live.get('kill_orders')}, "
             f"signer active={live.get('signer_active')}, tokens revoked in 24 h={live.get('tokens_revoked_24h')}, "
             f"login refresh={live.get('login_refresh')}"]
    if vendor_lapse:
        lines.append(f"Every {vendor_of(account)} agent account is silent together: this looks like a cloud-provider "
                     "subscription or login lapse rather than one stuck session.")
    lines.append("Check / reactivate:")
    lines += [f"  {c}" for c in commands(account)]
    lines.append(f"Dismiss from the console: https://{CONSOLE_HOST}/console?lapse={account}#agent-lapse")
    return "\n".join(lines)


def _board_nudge(account: str, silent_s: float | None, now: float) -> str | None:
    from common import db
    try:
        rec = db.board_insert(from_side="dispatch", to_side=account, thread="coord",
                              subject=f"Agent-lapse check: no activity seen for {ps.hours(silent_s)}",
                              body=(f"The platform's agent-lapse sentinel has seen no board post, connector tool "
                                    f"call or signed request from {account} since "
                                    f"{ps.fmt(now - silent_s) if silent_s is not None else 'the lookback window began'}. "
                                    "If you are reading this, post a short status on coord; that ends the lapse."))
        return rec.get("id")
    except Exception as e:   # noqa: BLE001
        ps.log.warning("%s: board nudge for %s failed (%s)", NAME, account, type(e).__name__)
        return None


# ---------------------------------------------------------------- the run
def run(now: float | None = None) -> dict:
    now = ps.now() if now is None else now
    names = accounts()
    acts = last_activity(names, now)
    escalate_inert = os.environ.get("AGENT_LAPSE_ESCALATE_INERT") == "1"
    rows = {}
    for n in names:
        a = acts.get(n, {})
        last = max([t for t in a.values() if t], default=None)
        silent = None if last is None else max(0.0, now - last)
        rows[n] = {"last": last, "silent": silent, "stage": stage_for(silent), "act": a, "live": liveness(n, now)}
    by_vendor: dict[str, list[str]] = {}
    for n, r in rows.items():
        if not r["live"].get("inert"):
            by_vendor.setdefault(vendor_of(n), []).append(n)
    report = {"generated": ps.fmt(now), "thresholds_h": {"nudge": NUDGE_H, "email": EMAIL_H, "escalate": ESCALATE_H},
              "accounts": {}}
    for n, r in rows.items():
        peers = by_vendor.get(vendor_of(n), [])
        vendor_lapse = len(peers) >= 2 and all(rows[p]["stage"] != "none" for p in peers)
        episode = str(int(r["last"])) if r["last"] else "none-in-window"
        prior = _state(n) or {}
        sent = prior.get("stage_sent") if prior.get("episode") == episode else "none"
        sent = sent if sent in STAGES else "none"
        target = r["stage"]
        inert = bool(r["live"].get("inert"))
        dis = active_dismissal(n, r["last"], now)
        shown = "dismissed" if dis else ("inert" if inert and not escalate_inert else target)
        detail = {"act": r["act"], "live": r["live"], "silent_s": r["silent"], "vendor_lapse": vendor_lapse,
                  "dismissal": dis}
        new_sent = sent
        if not dis and not (inert and not escalate_inert) and STAGES.index(target) > STAGES.index(sent):
            ent = f"{n}"
            det = {"stage": target, "episode": episode}
            if target == "nudge":
                ps.push("agent_lapse_nudge", f"{n}: no activity seen for {ps.hours(r['silent'])} "
                        f"(last {ps.fmt(r['last'])}). A board nudge was posted on coord.",
                        title=f"Agent quiet {ps.hours(r['silent'])}: {n}", priority=2, entity=ent, detail=det)
                _board_nudge(n, r["silent"], now)
            elif target == "email":
                ps.push("agent_lapse_email", f"{n}: no activity seen for {ps.hours(r['silent'])} "
                        f"(last {ps.fmt(r['last'])}).\n\n" + "\n".join(commands(n)),
                        title=f"Agent silent {ps.hours(r['silent'])}: {n}", priority=3, entity=ent, detail=det,
                        email=True)
            else:
                url = f"https://{CONSOLE_HOST}/console?lapse={n}#agent-lapse"
                ps.push("agent_lapse_escalate", escalation_text(n, r["act"], r["live"], r["silent"], vendor_lapse),
                        title=f"Agent lapse {ps.hours(r['silent'])}: {n}", priority=4, entity=ent, detail=det,
                        email=True, actions=f"view, Open console (dismiss), {url}, clear=true")
                _board_nudge(n, r["silent"], now)
            new_sent = target
        _save(n, r["last"], shown, episode, new_sent, detail, now)
        report["accounts"][n] = {"last_activity": ps.fmt(r["last"]), "silent": ps.hours(r["silent"]),
                                 "stage": shown, "stage_sent": new_sent, "vendor_lapse": vendor_lapse,
                                 "inert": r["live"].get("inert"), "dismissed_until": dis and ps.fmt(dis["until_at"])}
    ps.write_report(NAME, report)
    return report
