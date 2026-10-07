"""common.es_invites -- reader access for The Executive Standard members edition
(2026-10-05). Schema: pg_schema/0072_es_invites.sql (sqlite twin: ensure()).
Used by the members gate (executivestandard-website/verifier/verify.py, which
runs on the poller image), the operator CLI (scripts/es-invite.sh) and the
operator console (/console, src/web/routes/console.py).

Sibling of common.agent_gateway, same shape:
  * the PUBLIC invite host (invite.executivestandard.example.com)
    takes a personal invite link or a promo code and, on a button press (a
    POST -- link previews in iMessage/Slack only GET, so they never burn a
    code), mints a single-use hand-off valid EXCHANGE_TTL_S;
  * the members host swaps the hand-off for a session cookie scoped to itself.
    Nothing reusable ever travels in a URL into the members site, and only
    HASHES of invite codes, promo codes, hand-offs and sessions are stored.

Operator directives:
  * a personal invite is PERMANENT by default and ends only when the operator /
    publisher revokes it -- or it is issued for a short period (days=N);
  * promo codes are short-lived (the code works <= PROMO_CODE_MAX_DAYS; each
    redemption grants <= PROMO_ACCESS_MAX_DAYS, never permanent), capped by
    uses, handed out freely, all managed locally;
  * revoke / freeze / kill-all are the publisher's alone: the CLI runs in the
    operator's rootless podman, the console needs the operator's SSH-signed
    sign-in (kind console-login).
A personal invite signs in up to max_devices devices; one more signs out the
oldest, so a forwarded link defeats itself and shows up as claims > devices.
Grants carry an exec_standard_sources tag, so `es-access source disable` and
source caps keep working.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time

from common import db

EXCHANGE_TTL_S = 120
SESSION_IDLE_S = 90 * 86400          # a device unused this long signs in again (permanent link still works)
TOUCH_MIN_S = 300                    # last_seen writes are throttled: a page pulls many assets
INVITE_DEFAULT_DEVICES = 3
PROMO_DEFAULT_CODE_DAYS = 7
PROMO_DEFAULT_ACCESS_DAYS = 7
PROMO_CODE_MAX_DAYS = 30
PROMO_ACCESS_MAX_DAYS = 30
PROMO_MAX_USES = 10000
INVITE_BASE = os.environ.get("ES_INVITE_BASE", "https://invite.executivestandard.example.com")
MEMBERS_BASE = os.environ.get("ES_MEMBERS_BASE", "https://members.executivestandard.example.com")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")


class InviteError(Exception):
    """status = HTTP status for the public pages; detail = safe to show a reader."""
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return
    c.execute("""CREATE TABLE IF NOT EXISTS exec_standard_sources (source TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1, cap INTEGER, label TEXT, notes TEXT)""")
    c.execute("INSERT OR IGNORE INTO exec_standard_sources (source, label) VALUES ('direct', 'Direct invite')")
    c.execute("INSERT OR IGNORE INTO exec_standard_sources (source, label) VALUES ('promo', 'Promo code')")
    c.execute("""CREATE TABLE IF NOT EXISTS es_grants (id TEXT PRIMARY KEY, kind TEXT NOT NULL, email TEXT,
        label TEXT, source TEXT NOT NULL, campaign TEXT, promo_id TEXT, code_hash TEXT UNIQUE,
        max_devices INTEGER NOT NULL DEFAULT 3, created_at REAL NOT NULL, created_by TEXT, expires_at REAL,
        claimed_at REAL, claims INTEGER NOT NULL DEFAULT 0, last_seen REAL, revoked_at REAL, revoked_by TEXT,
        revoke_reason TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS es_promos (id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE,
        label TEXT, source TEXT NOT NULL, campaign TEXT, created_at REAL NOT NULL, created_by TEXT,
        expires_at REAL NOT NULL, access_days INTEGER NOT NULL, max_uses INTEGER NOT NULL,
        uses INTEGER NOT NULL DEFAULT 0, revoked_at REAL, revoked_by TEXT, revoke_reason TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS es_exchanges (code_hash TEXT PRIMARY KEY, grant_id TEXT NOT NULL,
        expires_at REAL NOT NULL, used_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS es_sessions (id_hash TEXT PRIMARY KEY, grant_id TEXT NOT NULL,
        created_at REAL NOT NULL, last_seen REAL NOT NULL, revoked_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS es_invite_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at REAL NOT NULL, updated_by TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS es_invite_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
        actor TEXT, action TEXT NOT NULL, target TEXT, detail TEXT)""")


def _q(sql: str, args: tuple = (), one: bool = False):
    with db.conn() as c:
        ensure(c)
        cur = c.execute(sql, args)
        if sql.lstrip().upper().startswith("SELECT"):
            rows = [dict(r) for r in cur.fetchall()]
            return (rows[0] if rows else None) if one else rows
        return cur.rowcount


# 2026-10-07: the reader plane's global switches and revocations also go to the
# hash-chained audit_log (target ids only; no emails or codes).
_AUDITED = {"freeze", "thaw", "kill-all", "revoke", "revoke-promo", "sign-out", "invite", "invite-batch", "reissue", "promo"}


def _event(actor: str | None, action: str, target: str = "", detail: str = "") -> None:
    _q("INSERT INTO es_invite_events (ts, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)",
       (time.time(), actor, action, target, detail[:500]))
    if action in _AUDITED:
        from common import governance
        governance.audit(f"reader.{action}", {"actor": actor, "target": target})


def events(limit: int = 50) -> list[dict]:
    return _q("SELECT * FROM es_invite_events ORDER BY ts DESC LIMIT ?", (max(1, min(int(limit), 500)),))


# -- publisher switches ------------------------------------------------------------------

def frozen() -> bool:
    r = _q("SELECT value FROM es_invite_settings WHERE key = 'frozen'", one=True)
    return bool(r and r["value"] == "1")


def _set(key: str, value: str, actor: str) -> None:
    _q("DELETE FROM es_invite_settings WHERE key = ?", (key,))
    _q("INSERT INTO es_invite_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
       (key, value, time.time(), actor))


def freeze(actor: str) -> None:
    """No new sign-ins anywhere (invites, promos, hand-offs); signed-in readers keep reading."""
    _set("frozen", "1", actor)
    _event(actor, "freeze")


def thaw(actor: str) -> None:
    _set("frozen", "0", actor)
    _event(actor, "thaw")


def kill_all(actor: str, reason: str = "kill-all") -> int:
    """Freeze AND sign out every device. Grants survive: after a thaw, readers
    with a permanent invite sign in again with the same link."""
    freeze(actor)
    now = time.time()
    n = _q("UPDATE es_sessions SET revoked_at = ? WHERE revoked_at IS NULL", (now,))
    _q("UPDATE es_exchanges SET used_at = ? WHERE used_at IS NULL", (now,))
    _event(actor, "kill-all", "", f"{n} session(s) ended; {reason}")
    return n or 0


# -- issuance ----------------------------------------------------------------------------

def _source_ok(source: str) -> None:
    s = _q("SELECT * FROM exec_standard_sources WHERE source = ?", (source,), one=True)
    if not s:
        raise InviteError(400, f"unknown source {source!r}")
    if not s["enabled"]:
        raise InviteError(400, f"source {source!r} is disabled")
    if s.get("cap"):
        active = _q("SELECT count(*) AS n FROM es_grants WHERE source = ? AND revoked_at IS NULL", (source,), one=True)["n"]
        if active >= s["cap"]:
            raise InviteError(400, f"source {source!r} is at its cap ({s['cap']})")


def invite_url(code: str) -> str:
    return f"{INVITE_BASE}/i/{code}"


def invite_issue(actor: str, email: str | None = None, label: str | None = None, *, source: str = "direct",
                 campaign: str | None = None, days: float | None = None,
                 max_devices: int = INVITE_DEFAULT_DEVICES) -> dict:
    """A personal invite: permanent unless days is given. Returns the link ONCE."""
    email = (email or "").strip().lower() or None
    if email and not _EMAIL_RE.match(email):
        raise InviteError(400, "that does not look like an email address")
    if not email and not label:
        raise InviteError(400, "give an email or a label so you can find and revoke it later")
    if days is not None and not (0 < float(days) <= 3650):
        raise InviteError(400, "days must be 1..3650 (omit for permanent)")
    if not (1 <= int(max_devices) <= 10):
        raise InviteError(400, "devices must be 1..10")
    _source_ok(source)
    now = time.time()
    gid, code = "g_" + secrets.token_hex(5), secrets.token_urlsafe(24)
    _q("INSERT INTO es_grants (id, kind, email, label, source, campaign, code_hash, max_devices, created_at, "
       "created_by, expires_at) VALUES (?, 'invite', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
       (gid, email, label, source, campaign, _h(code), int(max_devices), now, actor,
        (now + float(days) * 86400) if days else None))
    _event(actor, "invite", gid, f"{email or label}; {'%sd' % days if days else 'permanent'}; {max_devices} device(s)")
    return {"grant": gid, "url": invite_url(code), "expires_at": (now + float(days) * 86400) if days else None}


BATCH_MAX = 500


def parse_batch(text: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """'email[,name]' per line; blank lines and # comments skipped. Returns
    (entries, problems); problems name the line, never echo other lines."""
    entries, problems = [], []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        email, _, name = line.partition(",")
        email, name = email.strip().lower(), name.strip().strip('"') or None
        if not _EMAIL_RE.match(email):
            problems.append(f"line {n}: not an email address")
        elif name and len(name) > 120:
            problems.append(f"line {n}: name longer than 120 characters")
        else:
            entries.append((email, name))
    if len(entries) > BATCH_MAX:
        problems.append(f"{len(entries)} entries; the limit is {BATCH_MAX} per batch")
    return entries, problems


def invite_batch(actor: str, text: str, *, source: str = "direct", campaign: str | None = None,
                 days: float | None = None, max_devices: int = INVITE_DEFAULT_DEVICES) -> dict:
    """Issue one personal invite per line. All-or-nothing on bad input (nothing
    is issued if any line is invalid); repeats in the file and emails that
    already hold a live invite are skipped. Returns the links ONCE."""
    entries, problems = parse_batch(text)
    if problems:
        raise InviteError(400, "nothing issued -- " + "; ".join(problems[:20]))
    if not entries:
        raise InviteError(400, "nothing to issue: the list is empty")
    live = {r["email"] for r in _q("SELECT email FROM es_grants WHERE kind = 'invite' AND revoked_at IS NULL "
                                     "AND email IS NOT NULL AND (expires_at IS NULL OR expires_at > ?)", (time.time(),))}
    issued, skipped, seen = [], [], set()
    for email, name in entries:
        if email in seen or email in live:
            skipped.append({"email": email, "why": "repeated in the list" if email in seen else "already has a live invite"})
            seen.add(email)
            continue
        seen.add(email)
        r = invite_issue(actor, email, name, source=source, campaign=campaign, days=days, max_devices=max_devices)
        issued.append({"email": email, "name": name or "", **r})
    _event(actor, "invite-batch", campaign or "", f"{len(issued)} issued, {len(skipped)} skipped")
    return {"issued": issued, "skipped": skipped}


def invite_reissue(actor: str, grant_id: str) -> dict:
    """New link for the same reader (lost or leaked link): the old link dies,
    signed-in devices stay signed in."""
    g = _grant(grant_id)
    if g["kind"] != "invite" or g["revoked_at"]:
        raise InviteError(400, "only a live personal invite can get a new link")
    code = secrets.token_urlsafe(24)
    _q("UPDATE es_grants SET code_hash = ? WHERE id = ?", (_h(code), g["id"]))
    _event(actor, "reissue", g["id"])
    return {"grant": g["id"], "url": invite_url(code)}


def _promo_code() -> str:
    raw = "".join(secrets.choice(_CROCKFORD) for _ in range(10))      # 50 bits
    return f"ES-{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize_promo(code: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]", "", code or "").upper().translate(str.maketrans("OIL", "011"))
    if len(s) == 12 and s.startswith("ES"):
        s = s[2:]
    return s if len(s) == 10 and all(ch in _CROCKFORD for ch in s) else ""


def promo_create(actor: str, label: str, *, uses: int, code_days: float = PROMO_DEFAULT_CODE_DAYS,
                 access_days: int = PROMO_DEFAULT_ACCESS_DAYS, source: str = "promo",
                 campaign: str | None = None) -> dict:
    if not label:
        raise InviteError(400, "a promo needs a label (where you are handing it out)")
    if not (1 <= int(uses) <= PROMO_MAX_USES):
        raise InviteError(400, f"uses must be 1..{PROMO_MAX_USES}")
    if not (0 < float(code_days) <= PROMO_CODE_MAX_DAYS):
        raise InviteError(400, f"a promo code works at most {PROMO_CODE_MAX_DAYS} days")
    if not (1 <= int(access_days) <= PROMO_ACCESS_MAX_DAYS):
        raise InviteError(400, f"promo access is 1..{PROMO_ACCESS_MAX_DAYS} days, never permanent")
    _source_ok(source)
    now = time.time()
    pid, code = "p_" + secrets.token_hex(4), _promo_code()
    _q("INSERT INTO es_promos (id, code_hash, label, source, campaign, created_at, created_by, expires_at, "
       "access_days, max_uses) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
       (pid, _h(normalize_promo(code)), label, source, campaign, now, actor, now + float(code_days) * 86400,
        int(access_days), int(uses)))
    _event(actor, "promo", pid, f"{label}; {uses} use(s); code {code_days}d; access {access_days}d")
    return {"promo": pid, "code": code, "url": f"{INVITE_BASE}/p?code={code}",
            "expires_at": now + float(code_days) * 86400}


# -- the public flow ---------------------------------------------------------------------

_GONE = "This invitation is no longer valid. Ask the publisher for a new one."


def _handoff(grant_id: str, now: float) -> str:
    x = secrets.token_urlsafe(32)
    _q("INSERT INTO es_exchanges (code_hash, grant_id, expires_at) VALUES (?, ?, ?)",
       (_h(x), grant_id, now + EXCHANGE_TTL_S))
    return f"{MEMBERS_BASE}/redeem?x={x}"


def _grant_live(g: dict | None, now: float) -> bool:
    if not g or g["revoked_at"] or (g["expires_at"] and g["expires_at"] <= now):
        return False
    s = _q("SELECT enabled FROM exec_standard_sources WHERE source = ?", (g["source"],), one=True)
    return bool(s and s["enabled"])


def claim_invite(code: str, now: float | None = None) -> str:
    """POST /i/<code> on the invite host -> the members hand-off URL."""
    now = time.time() if now is None else now
    if frozen():
        raise InviteError(503, "Sign-ins are paused right now. Please try again later.")
    if not code or len(code) > 64:
        raise InviteError(404, _GONE)
    g = _q("SELECT * FROM es_grants WHERE code_hash = ? AND kind = 'invite'", (_h(code),), one=True)
    if not _grant_live(g, now):
        raise InviteError(404, _GONE)
    return _handoff(g["id"], now)


def redeem_promo(code: str, now: float | None = None) -> str:
    """POST /p on the invite host: one use of a promo -> a fresh anonymous,
    short-lived grant -> the members hand-off URL."""
    now = time.time() if now is None else now
    if frozen():
        raise InviteError(503, "Sign-ins are paused right now. Please try again later.")
    norm = normalize_promo(code)
    if not norm:
        raise InviteError(400, "That code doesn't look right. It reads like ES-XXXX-XXXX-XX.")
    p = _q("SELECT * FROM es_promos WHERE code_hash = ?", (_h(norm),), one=True)
    if not p or p["revoked_at"]:
        raise InviteError(404, "That code isn't valid.")
    if p["expires_at"] <= now:
        raise InviteError(410, "That code has expired.")
    s = _q("SELECT enabled FROM exec_standard_sources WHERE source = ?", (p["source"],), one=True)
    if not (s and s["enabled"]):
        raise InviteError(410, "That code is no longer being accepted.")
    if (_q("UPDATE es_promos SET uses = uses + 1 WHERE id = ? AND uses < max_uses AND revoked_at IS NULL "
           "AND expires_at > ?", (p["id"], now)) or 0) != 1:
        raise InviteError(410, "That code has been fully redeemed.")
    gid = "g_" + secrets.token_hex(5)
    _q("INSERT INTO es_grants (id, kind, label, source, campaign, promo_id, max_devices, created_at, created_by, "
       "expires_at) VALUES (?, 'promo', ?, ?, ?, ?, 2, ?, 'promo', ?)",
       (gid, p["label"], p["source"], p["campaign"], p["id"], now, now + p["access_days"] * 86400))
    return _handoff(gid, now)


def exchange(x: str, now: float | None = None) -> tuple[str, str]:
    """GET /redeem?x= on the members host -> (session secret for the cookie, grant id).
    Single use; enforces the device cap by signing out the least recently used."""
    now = time.time() if now is None else now
    if not x or len(x) > 64 or frozen():
        raise InviteError(401, _GONE)
    if (_q("UPDATE es_exchanges SET used_at = ? WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
           (now, _h(x), now)) or 0) != 1:
        raise InviteError(401, _GONE)
    gid = _q("SELECT grant_id FROM es_exchanges WHERE code_hash = ?", (_h(x),), one=True)["grant_id"]
    g = _q("SELECT * FROM es_grants WHERE id = ?", (gid,), one=True)
    if not _grant_live(g, now):
        raise InviteError(401, _GONE)
    live = _q("SELECT id_hash FROM es_sessions WHERE grant_id = ? AND revoked_at IS NULL AND last_seen > ? "
              "ORDER BY last_seen DESC", (gid, now - SESSION_IDLE_S))
    for old in live[max(0, g["max_devices"] - 1):]:
        _q("UPDATE es_sessions SET revoked_at = ? WHERE id_hash = ?", (now, old["id_hash"]))
    secret = secrets.token_urlsafe(32)
    _q("INSERT INTO es_sessions (id_hash, grant_id, created_at, last_seen) VALUES (?, ?, ?, ?)",
       (_h(secret), gid, now, now))
    _q("UPDATE es_grants SET claims = claims + 1, claimed_at = COALESCE(claimed_at, ?), last_seen = ? WHERE id = ?",
       (now, now, gid))
    return secret, gid


def session_check(secret: str, now: float | None = None) -> str | None:
    """auth_request on the members host: the cookie's session -> grant id, or None."""
    now = time.time() if now is None else now
    if not secret or len(secret) > 64:
        return None
    s = _q("SELECT * FROM es_sessions WHERE id_hash = ?", (_h(secret),), one=True)
    if not s or s["revoked_at"] or s["last_seen"] <= now - SESSION_IDLE_S:
        return None
    g = _q("SELECT * FROM es_grants WHERE id = ?", (s["grant_id"],), one=True)
    if not _grant_live(g, now):
        return None
    if now - s["last_seen"] > TOUCH_MIN_S:
        _q("UPDATE es_sessions SET last_seen = ? WHERE id_hash = ?", (now, s["id_hash"]))
        _q("UPDATE es_grants SET last_seen = ? WHERE id = ?", (now, g["id"]))
    return g["id"]


# -- publisher: revoke / list ---------------------------------------------------------------

def _grant(ref: str) -> dict:
    ref = (ref or "").strip()
    rows = _q("SELECT * FROM es_grants WHERE id = ?", (ref,)) or \
        _q("SELECT * FROM es_grants WHERE email = ? AND revoked_at IS NULL", (ref.lower(),))
    if not rows:
        raise InviteError(404, f"no grant {ref!r}")
    if len(rows) > 1:
        raise InviteError(409, f"{len(rows)} live grants for {ref!r} -- use the grant id")
    return rows[0]


def _end_sessions(grant_id: str, now: float) -> int:
    return _q("UPDATE es_sessions SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL", (now, grant_id)) or 0


def revoke_grant(actor: str, ref: str, reason: str = "revoked by publisher") -> dict:
    g, now = _grant(ref), time.time()
    _q("UPDATE es_grants SET revoked_at = ?, revoked_by = ?, revoke_reason = ? WHERE id = ? AND revoked_at IS NULL",
       (now, actor, reason, g["id"]))
    n = _end_sessions(g["id"], now)
    _event(actor, "revoke", g["id"], f"{g['email'] or g['label']}; {n} device(s) signed out; {reason}")
    return {"grant": g["id"], "devices_signed_out": n}


def sign_out(actor: str, ref: str) -> int:
    """End a reader's devices but keep the grant (a permanent link signs in again)."""
    g = _grant(ref)
    n = _end_sessions(g["id"], time.time())
    _event(actor, "sign-out", g["id"], f"{n} device(s)")
    return n


def revoke_promo(actor: str, promo_id: str, reason: str = "revoked by publisher", readers: bool = False) -> dict:
    """Stop a promo code. readers=True also ends every grant it already handed out."""
    p = _q("SELECT * FROM es_promos WHERE id = ?", (promo_id,), one=True)
    if not p:
        raise InviteError(404, f"no promo {promo_id!r}")
    now = time.time()
    _q("UPDATE es_promos SET revoked_at = ?, revoked_by = ?, revoke_reason = ? WHERE id = ? AND revoked_at IS NULL",
       (now, actor, reason, promo_id))
    ended = 0
    if readers:
        for g in _q("SELECT id FROM es_grants WHERE promo_id = ? AND revoked_at IS NULL", (promo_id,)):
            _q("UPDATE es_grants SET revoked_at = ?, revoked_by = ?, revoke_reason = ? WHERE id = ?",
               (now, actor, f"promo {promo_id} revoked", g["id"]))
            _end_sessions(g["id"], now)
            ended += 1
    _event(actor, "revoke-promo", promo_id, f"{p['label']}; {ended} reader(s) ended; {reason}")
    return {"promo": promo_id, "readers_ended": ended}


def grants(include_revoked: bool = False, kind: str | None = None, limit: int = 200) -> list[dict]:
    now = time.time()
    where, args = [], []
    if not include_revoked:
        where.append("g.revoked_at IS NULL")
    if kind:
        where.append("g.kind = ?")
        args.append(kind)
    rows = _q("SELECT g.id, g.kind, g.email, g.label, g.source, g.campaign, g.promo_id, g.max_devices, g.created_at, "
              "g.expires_at, g.claimed_at, g.claims, g.last_seen, g.revoked_at, g.revoke_reason, "
              "(SELECT count(*) FROM es_sessions s WHERE s.grant_id = g.id AND s.revoked_at IS NULL AND s.last_seen > ?) "
              "AS devices FROM es_grants g" + (" WHERE " + " AND ".join(where) if where else "") +
              " ORDER BY g.created_at DESC LIMIT ?", (now - SESSION_IDLE_S, *args, max(1, min(int(limit), 1000))))
    for r in rows:
        r["status"] = ("revoked" if r["revoked_at"] else "expired" if r["expires_at"] and r["expires_at"] <= now
                       else "unclaimed" if not r["claimed_at"] else "active")
    return rows


def promos(include_revoked: bool = False) -> list[dict]:
    now = time.time()
    rows = _q("SELECT id, label, source, campaign, created_at, expires_at, access_days, max_uses, uses, revoked_at "
              "FROM es_promos" + ("" if include_revoked else " WHERE revoked_at IS NULL") + " ORDER BY created_at DESC")
    for r in rows:
        r["status"] = ("revoked" if r["revoked_at"] else "expired" if r["expires_at"] <= now
                       else "used up" if r["uses"] >= r["max_uses"] else "live")
    return rows


def summary() -> dict:
    g = grants()
    return {"frozen": frozen(),
            "invites": sum(1 for r in g if r["kind"] == "invite" and r["status"] != "expired"),
            "promo_readers": sum(1 for r in g if r["kind"] == "promo" and r["status"] == "active"),
            "devices": sum(r["devices"] for r in g),
            "live_promos": sum(1 for p in promos() if p["status"] == "live")}
