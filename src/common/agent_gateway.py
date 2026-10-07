"""common.agent_gateway -- OAuth 2.1 + remote-MCP gateway for CLOUD agents
(2026-10-05). Schema: pg_schema/0071_agent_gateway.sql (sqlite twin: ensure()).
Routes: src/web/agent_gateway_routes.py. Operator CLI: scripts/agent-gateway.sh.

One MCP endpoint per agent identity (``/mcp/<slug>`` -> a team account). A
vendor (Claude custom connector -- Cowork / claude.ai / Claude Code; ChatGPT
developer-mode MCP or a GPT Action) links once through OAuth; the consent is
the operator's SSH-signed approval (kind ``connector-link``), never a click.
After that the vendor renews on its own: 1 h access tokens, rotating refresh
tokens -- no weekly nonce.

Authority (operator, 2026-10-05): OUR side decides.
  * Every renewal and every tool call checks the ACCOUNT: board signer active
    (the liveness switch and ``board-signer-ctl.sh deactivate`` both clear
    it), and the operator dead-man (operator logged in within
    OPERATOR_DEADMAN_S). Fail -> the connection is revoked (account) or
    refused (dead-man, not revoked: it resumes when the operator is back).
  * The VENDOR going quiet (a subscription lapsing between paychecks, a
    silently disabled feature) only makes the SESSION ``dormant``: nothing
    is revoked and the account's liveness is untouched (cloud agents are
    service-kind identities: their liveness is signer + tokens + kill orders).
    Without a hold, our refresh grant idles out after REFRESH_IDLE_S.
  * An operator-signed HOLD (kind ``connector-hold``) keeps our side of the
    link open -- client registration and refresh grant do not expire -- until
    ``hold_until``; the vendor's next refresh resumes the session at once.
  * Revocation on our side always wins over a hold.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import uuid

from common import db, governance

ACCESS_TTL_S = 3600
REFRESH_IDLE_S = 30 * 86400
# vendors renew on use, not on a clock: an idle-but-healthy agent can be quiet
# for days. 7 days with no renewal or call = treat the vendor side as lapsed.
DORMANT_AFTER_S = 7 * 86400
HOLD_MAX_S = 90 * 86400
OPERATOR_DEADMAN_S = 14 * 86400
PENDING_TTL_S = 15 * 60               # authorize request waits this long for a signature
CODE_TTL_S = 10 * 60
OPERATOR_SEEN_FILE = os.environ.get(
    "OPERATOR_SEEN_FILE", "/var/lib/corporatetraveldc/team-liveness/operator-last-login")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class GatewayError(Exception):
    def __init__(self, status: int, error: str, detail: str = ""):
        super().__init__(detail or error)
        self.status, self.error, self.detail = status, error, detail or error


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return
    c.execute("""CREATE TABLE IF NOT EXISTS agent_connectors (slug TEXT PRIMARY KEY, account TEXT NOT NULL,
        vendor TEXT NOT NULL, created_at REAL NOT NULL, disabled_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS oauth_clients (client_id TEXT PRIMARY KEY, client_name TEXT,
        redirect_uris TEXT NOT NULL, source TEXT NOT NULL, created_at REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS oauth_pending (req_id TEXT PRIMARY KEY, slug TEXT NOT NULL,
        client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, code_challenge TEXT NOT NULL, state TEXT,
        scope TEXT, approval_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', code_hash TEXT,
        created_at REAL NOT NULL, expires_at REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agent_connections (id TEXT PRIMARY KEY, slug TEXT NOT NULL,
        account TEXT NOT NULL, client_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        created_at REAL NOT NULL, last_renewal_at REAL, last_call_at REAL, hold_until REAL,
        hold_approval_id TEXT, revoked_at REAL, revoke_reason TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS oauth_tokens (token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
        connection_id TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
        rotated_at REAL, revoked_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agent_gateway_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at REAL NOT NULL, updated_by TEXT)""")


def _q(sql: str, args: tuple = (), one: bool = False):
    with db.conn() as c:
        ensure(c)
        cur = c.execute(sql, args)
        if sql.lstrip().upper().startswith("SELECT"):
            rows = [dict(r) for r in cur.fetchall()]
            return (rows[0] if rows else None) if one else rows
        return cur.rowcount


# -- connectors -------------------------------------------------------------------

def connector_add(slug: str, account: str, vendor: str) -> None:
    if not _SLUG_RE.match(slug or ""):
        raise GatewayError(400, "invalid_request", "slug: [a-z0-9-], 2-32 chars")
    if not db.board_signer_get(account):
        raise GatewayError(400, "invalid_request", f"{account} has no board signer -- register it first")
    _q("DELETE FROM agent_connectors WHERE slug = ?", (slug,))
    _q("INSERT INTO agent_connectors (slug, account, vendor, created_at) VALUES (?, ?, ?, ?)",
       (slug, account, vendor, time.time()))


def connector_set_disabled(slug: str, disabled: bool) -> int:
    """Disable (refuses new links and every call on that slug) or re-enable a
    connector. Disabling also revokes its live connections."""
    if disabled:
        for r in _q("SELECT id FROM agent_connections WHERE slug = ? AND status != 'revoked'", (slug,)):
            revoke_connection(r["id"], "connector disabled by operator")
    governance.audit("agent.connector.disabled" if disabled else "agent.connector.enabled", {"connector": slug})
    return _q("UPDATE agent_connectors SET disabled_at = ? WHERE slug = ?", (time.time() if disabled else None, slug))


def connector_get(slug: str) -> dict | None:
    r = _q("SELECT * FROM agent_connectors WHERE slug = ?", (slug,), one=True)
    return r if r and not r.get("disabled_at") else None


def connector_list() -> list[dict]:
    return _q("SELECT * FROM agent_connectors ORDER BY slug")


# -- authority checks ------------------------------------------------------------------

def frozen() -> bool:
    r = _q("SELECT value FROM agent_gateway_settings WHERE key = 'frozen'", one=True)
    return bool(r and r["value"] == "1")


def _check_open() -> None:
    if frozen():
        raise GatewayError(503, "temporarily_unavailable", "agent gateway is frozen by the operator")


def kill_all(actor: str, reason: str = "gateway kill-all") -> int:
    """Full-compromise switch: freeze the gateway (every OAuth/MCP endpoint
    refuses), revoke every connection and disable every connector. Undo =
    request_thaw() + the operator's signature, then re-enable connectors and
    re-link each vendor (a fresh signed connector-link)."""
    _set_frozen(True, actor)
    rows = _q("SELECT id FROM agent_connections WHERE status != 'revoked'")
    for r in rows:
        revoke_connection(r["id"], reason)
    _q("UPDATE agent_connectors SET disabled_at = ? WHERE disabled_at IS NULL", (time.time(),))
    governance.audit("gateway.killed", {"actor": actor, "connections_revoked": len(rows), "reason": reason[:200]})
    _notify("agent gateway KILLED", f"{actor}: {len(rows)} connection(s) revoked, every connector disabled, gateway frozen.")
    return len(rows)


def _set_frozen(on: bool, actor: str) -> None:
    _q("DELETE FROM agent_gateway_settings WHERE key = 'frozen'")
    _q("INSERT INTO agent_gateway_settings (key, value, updated_at, updated_by) VALUES ('frozen', ?, ?, ?)",
       ("1" if on else "0", time.time(), actor))


def request_thaw(requester: str = "operator-cli") -> dict:
    """Unfreezing is the dangerous direction: it needs the operator's SSH signature."""
    spec = json.dumps({"type": "gateway-thaw", "ts": int(time.time())}, sort_keys=True, separators=(",", ":"))
    appr = governance.create_approval("gateway-thaw", spec, kind="gateway-thaw", requester=requester,
                                      reasoning="re-open the agent gateway after a kill-all / freeze",
                                      ttl_seconds=3600)
    return {"approval_id": appr["id"]}


def operator_seen_ok(now: float | None = None) -> tuple[bool, str]:
    """Dead-man: the operator logged in within OPERATOR_DEADMAN_S (file written
    hourly by the root team-liveness run). A missing/unreadable file fails
    CLOSED only after the dead-man window from this process's first look."""
    now = time.time() if now is None else now
    try:
        seen = float(open(OPERATOR_SEEN_FILE).read().split()[0])
    except (OSError, ValueError, IndexError):
        return False, "operator presence unknown (team-liveness has not recorded the operator's last login)"
    if seen <= 0 or now - seen > OPERATOR_DEADMAN_S:
        return False, "operator dead-man: no operator login within 14 days"
    return True, ""


def account_live(account: str) -> tuple[bool, str]:
    row = db.board_signer_get(account)
    if not row:
        return False, f"{account} has no board signer"
    if not row.get("active"):
        return False, f"{account}'s signer is inactive (deactivated or inert)"
    return True, ""


# -- clients -----------------------------------------------------------------------------

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def _parse_redirect(u: str):
    from urllib.parse import urlsplit
    try:
        p = urlsplit(u)
        p.port                                   # raises on a malformed port
    except (ValueError, TypeError):
        return None
    if p.fragment or "@" in p.netloc:
        return None
    return p


def _loopback(p) -> bool:
    host = p.netloc.rsplit(":", 1)[0] if not p.netloc.startswith("[") else p.netloc.split("]")[0] + "]"
    return p.scheme == "http" and host in _LOOPBACK_HOSTS


def redirect_allowed(u: str) -> bool:
    """https anywhere, or plain http ONLY to a loopback host (RFC 8252 native
    apps -- Claude Code's CLI finishes OAuth on http://localhost:<port>/callback).
    2026-10-05: before this, only https was accepted and the CLI could not link."""
    p = _parse_redirect(u) if isinstance(u, str) else None
    return bool(p and p.netloc and ((p.scheme == "https") or _loopback(p)))


def redirect_matches(registered: list[str], requested: str) -> bool:
    """Exact match; for loopback http the PORT may differ (RFC 8252 s7.3: the
    client picks a free port per run) but scheme, host and path may not."""
    if requested in registered:
        return True
    q = _parse_redirect(requested)
    if not q or not _loopback(q):
        return False
    for r in registered:
        p = _parse_redirect(r)
        if p and _loopback(p) and p.hostname == q.hostname and (p.path or "/") == (q.path or "/"):
            return True
    return False


def register_client(meta: dict) -> dict:
    uris = meta.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris or not all(redirect_allowed(u) for u in uris):
        raise GatewayError(400, "invalid_redirect_uri", "redirect_uris must be https, or http to localhost only")
    if len(uris) > 10:
        raise GatewayError(400, "invalid_client_metadata", "too many redirect_uris")
    cid = "dcr_" + secrets.token_urlsafe(18)
    name = str(meta.get("client_name") or "")[:120]
    _q("INSERT INTO oauth_clients (client_id, client_name, redirect_uris, source, created_at) VALUES (?, ?, ?, 'dcr', ?)",
       (cid, name, json.dumps(uris), time.time()))
    return {"client_id": cid, "client_name": name, "redirect_uris": uris,
            "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "client_id_issued_at": int(time.time())}


def _fetch_cimd(client_id: str) -> dict:
    import urllib.request
    # 2026-10-05: claude.ai answers 403 to the default "Python-urllib" user
    # agent (bot filtering) -- the first live Claude link failed with a 500.
    req = urllib.request.Request(client_id, headers={
        "Accept": "application/json",
        "User-Agent": "ctdi-dispatch-agent-gateway/1 (+https://agents.example.com)"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = r.read(65536)
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001 -- surface as an OAuth error, never a 500
        raise GatewayError(400, "invalid_client", f"could not fetch client metadata ({type(e).__name__})") from None


def resolve_client(client_id: str, fetch=None) -> dict:
    row = _q("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,), one=True)
    if row:
        row["redirect_uris"] = json.loads(row["redirect_uris"])
        return row
    if not client_id.startswith("https://"):
        raise GatewayError(400, "invalid_client", "unknown client_id")
    doc = (fetch or _fetch_cimd)(client_id)
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        raise GatewayError(400, "invalid_client", "client metadata document does not name itself")
    uris = [u for u in (doc.get("redirect_uris") or []) if redirect_allowed(u)]
    if not uris:
        raise GatewayError(400, "invalid_client", "client metadata has no usable redirect_uris")
    _q("INSERT INTO oauth_clients (client_id, client_name, redirect_uris, source, created_at) VALUES (?, ?, ?, 'cimd', ?)",
       (client_id, str(doc.get("client_name") or "")[:120], json.dumps(uris), time.time()))
    return {"client_id": client_id, "client_name": doc.get("client_name"), "redirect_uris": uris, "source": "cimd"}


# -- authorize: consent = operator's signed approval -------------------------------------

def authorize_start(slug: str, client_id: str, redirect_uri: str, code_challenge: str,
                    method: str, state: str | None, scope: str | None, fetch=None) -> dict:
    _check_open()
    con = connector_get(slug)
    if not con:
        raise GatewayError(404, "invalid_request", "unknown connector")
    ok, why = account_live(con["account"])
    if not ok:
        raise GatewayError(403, "access_denied", why)
    client = resolve_client(client_id, fetch=fetch)
    if not redirect_matches(client["redirect_uris"], redirect_uri):
        raise GatewayError(400, "invalid_request", "redirect_uri is not registered for this client")
    if method != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", code_challenge or ""):
        raise GatewayError(400, "invalid_request", "PKCE S256 code_challenge required")
    spec = json.dumps({"type": "connector-link", "connector": slug, "account": con["account"],
                       "vendor": con["vendor"], "client_id": client_id,
                       "client_name": client.get("client_name") or "", "redirect_uri": redirect_uri,
                       "scope": scope or "dispatch"}, sort_keys=True, separators=(",", ":"))
    appr = governance.create_approval(f"connector-link-{slug}", spec, kind="connector-link",
                                      requester=f"oauth:{client_id}"[:200],
                                      reasoning=f"link {client.get('client_name') or client_id} to {con['account']}",
                                      ttl_seconds=PENDING_TTL_S)
    req_id = "ar_" + secrets.token_urlsafe(16)
    now = time.time()
    _q("INSERT INTO oauth_pending (req_id, slug, client_id, redirect_uri, code_challenge, state, scope, approval_id, "
       "status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
       (req_id, slug, client_id, redirect_uri, code_challenge, state, scope, appr["id"], now, now + PENDING_TTL_S))
    return {"req_id": req_id, "approval_id": appr["id"], "account": con["account"],
            "client_name": client.get("client_name") or client_id}


def _redirect(uri: str, **params) -> str:
    from urllib.parse import urlencode
    params = {k: v for k, v in params.items() if v is not None}
    return uri + ("&" if "?" in uri else "?") + urlencode(params)


def authorize_status(req_id: str) -> dict:
    """{state: pending|redirect, location?}. Issues the code exactly once,
    only after the approval is ALLOWED by a human signature."""
    p = _q("SELECT * FROM oauth_pending WHERE req_id = ?", (req_id,), one=True)
    if not p:
        raise GatewayError(404, "invalid_request", "unknown authorization request")
    if p["status"] != "pending":
        return {"state": "done"}
    appr = governance.approval_view(p["approval_id"]) or {}
    st = appr.get("status")
    if st == "pending" and time.time() < p["expires_at"]:
        return {"state": "pending", "approval_id": p["approval_id"]}
    if st == "allowed":
        code = secrets.token_urlsafe(32)
        n = _q("UPDATE oauth_pending SET status = 'approved', code_hash = ? WHERE req_id = ? AND status = 'pending'",
               (_h(code), req_id))
        if n != 1:
            return {"state": "done"}
        return {"state": "redirect", "location": _redirect(p["redirect_uri"], code=code, state=p["state"])}
    _q("UPDATE oauth_pending SET status = 'denied' WHERE req_id = ?", (req_id,))
    return {"state": "redirect", "location": _redirect(p["redirect_uri"], error="access_denied", state=p["state"])}


# -- tokens ---------------------------------------------------------------------------------

def _issue(conn_id: str, now: float, refresh_until: float) -> dict:
    access, refresh = "dga_" + secrets.token_urlsafe(32), "dgr_" + secrets.token_urlsafe(40)
    _q("INSERT INTO oauth_tokens (token_hash, kind, connection_id, created_at, expires_at) VALUES (?, 'access', ?, ?, ?)",
       (_h(access), conn_id, now, now + ACCESS_TTL_S))
    _q("INSERT INTO oauth_tokens (token_hash, kind, connection_id, created_at, expires_at) VALUES (?, 'refresh', ?, ?, ?)",
       (_h(refresh), conn_id, now, refresh_until))
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL_S,
            "refresh_token": refresh, "scope": "dispatch"}


def exchange_code(code: str, client_id: str, redirect_uri: str, verifier: str, now: float | None = None) -> dict:
    _check_open()
    now = time.time() if now is None else now
    p = _q("SELECT * FROM oauth_pending WHERE code_hash = ?", (_h(code or ""),), one=True)
    if not p or p["status"] != "approved" or p["client_id"] != client_id or p["redirect_uri"] != redirect_uri:
        raise GatewayError(400, "invalid_grant", "unknown, used or mismatched code")
    if now - p["created_at"] > PENDING_TTL_S + CODE_TTL_S:
        raise GatewayError(400, "invalid_grant", "code expired")
    challenge = base64.urlsafe_b64encode(hashlib.sha256((verifier or "").encode()).digest()).rstrip(b"=").decode()
    if challenge != p["code_challenge"]:
        raise GatewayError(400, "invalid_grant", "PKCE verification failed")
    if _q("UPDATE oauth_pending SET status = 'used' WHERE req_id = ? AND status = 'approved'", (p["req_id"],)) != 1:
        raise GatewayError(400, "invalid_grant", "code already used")
    con = connector_get(p["slug"])
    if not con:
        raise GatewayError(400, "invalid_grant", "connector disabled")
    ok, why = account_live(con["account"])
    if not ok:
        raise GatewayError(400, "invalid_grant", why)
    cid = "cx_" + uuid.uuid4().hex[:16]
    _q("INSERT INTO agent_connections (id, slug, account, client_id, status, created_at, last_renewal_at) "
       "VALUES (?, ?, ?, ?, 'active', ?, ?)", (cid, p["slug"], con["account"], client_id, now, now))
    governance.audit("agent.link.approved", {"connection": cid, "connector": p["slug"], "account": con["account"],
                                             "client_id": client_id[:200], "approval_id": p["approval_id"]})
    return _issue(cid, now, now + REFRESH_IDLE_S)


def _connection(conn_id: str) -> dict | None:
    return _q("SELECT * FROM agent_connections WHERE id = ?", (conn_id,), one=True)


def revoke_connection(conn_id: str, reason: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _q("UPDATE agent_connections SET status = 'revoked', revoked_at = ?, revoke_reason = ? WHERE id = ? AND status != 'revoked'",
       (now, reason, conn_id))
    _q("UPDATE oauth_tokens SET revoked_at = ? WHERE connection_id = ? AND revoked_at IS NULL", (now, conn_id))
    governance.audit("agent.link.revoked", {"connection": conn_id, "reason": (reason or "")[:200]})


def revoke_account(account: str, reason: str) -> int:
    rows = _q("SELECT id FROM agent_connections WHERE account = ? AND status != 'revoked'", (account,))
    for r in rows:
        revoke_connection(r["id"], reason)
    return len(rows)


def refresh(refresh_token: str, client_id: str, now: float | None = None) -> dict:
    _check_open()
    now = time.time() if now is None else now
    t = _q("SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", (_h(refresh_token or ""),), one=True)
    if not t or t["revoked_at"] or t["rotated_at"]:
        raise GatewayError(400, "invalid_grant", "unknown, revoked or already-rotated refresh token")
    cx = _connection(t["connection_id"])
    if not cx or cx["status"] == "revoked" or cx["client_id"] != client_id:
        raise GatewayError(400, "invalid_grant", "connection revoked")
    ok, why = account_live(cx["account"])
    if not ok:                                   # our side said no: the chain ends here
        revoke_connection(cx["id"], why, now)
        raise GatewayError(400, "invalid_grant", why)
    ok, why = operator_seen_ok(now)
    if not ok:                                   # refuse but keep: resumes when the operator is back
        raise GatewayError(400, "invalid_grant", why)
    held = bool(cx.get("hold_until") and cx["hold_until"] > now)
    if t["expires_at"] <= now and not held:
        raise GatewayError(400, "invalid_grant", "refresh grant idled out (no hold) -- re-link the connector")
    _q("UPDATE oauth_tokens SET rotated_at = ? WHERE token_hash = ?", (now, t["token_hash"]))
    resumed = cx["status"] in ("dormant", "held")
    _q("UPDATE agent_connections SET status = 'active', last_renewal_at = ?, hold_until = NULL, hold_approval_id = NULL "
       "WHERE id = ?", (now, cx["id"]))
    if resumed:
        _notify(f"{cx['account']} reconnected", f"connector {cx['slug']} renewed after being {cx['status']}; session active again.")
    return _issue(cx["id"], now, now + REFRESH_IDLE_S)


def authenticate(access_token: str, slug: str, now: float | None = None) -> dict:
    _check_open()
    now = time.time() if now is None else now
    t = _q("SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'access'", (_h(access_token or ""),), one=True)
    if not t or t["revoked_at"] or t["expires_at"] <= now:
        raise GatewayError(401, "invalid_token", "missing, expired or revoked access token")
    cx = _connection(t["connection_id"])
    if not cx or cx["status"] == "revoked" or cx["slug"] != slug:
        raise GatewayError(401, "invalid_token", "connection revoked or token not for this connector")
    ok, why = account_live(cx["account"])
    if not ok:
        revoke_connection(cx["id"], why, now)
        raise GatewayError(403, "access_denied", why)
    ok, why = operator_seen_ok(now)
    if not ok:
        raise GatewayError(403, "access_denied", why)
    _q("UPDATE agent_connections SET last_call_at = ?, status = 'active' WHERE id = ?", (now, cx["id"]))
    return cx


# -- dormancy + operator holds --------------------------------------------------------------

def sweep(now: float | None = None) -> list[dict]:
    """Mark quiet sessions dormant and expired holds released. Never revokes:
    vendor silence is not an attack and not our decision."""
    now = time.time() if now is None else now
    changed = []
    for cx in _q("SELECT * FROM agent_connections WHERE status IN ('active', 'held')"):
        last = max(cx.get("last_renewal_at") or 0, cx.get("last_call_at") or 0)
        if cx["status"] == "held" and (cx.get("hold_until") or 0) <= now:
            _q("UPDATE agent_connections SET status = 'dormant', hold_until = NULL WHERE id = ?", (cx["id"],))
            changed.append({**cx, "status": "dormant", "why": "hold expired"})
        elif cx["status"] == "active" and now - last > DORMANT_AFTER_S:
            _q("UPDATE agent_connections SET status = 'dormant' WHERE id = ?", (cx["id"],))
            changed.append({**cx, "status": "dormant", "why": "vendor quiet"})
    return changed


def request_hold(slug: str, days: float, requester: str = "operator-cli") -> dict:
    if not (0 < days * 86400 <= HOLD_MAX_S):
        raise GatewayError(400, "invalid_request", f"hold must be 1..{HOLD_MAX_S // 86400} days")
    cx = _q("SELECT * FROM agent_connections WHERE slug = ? AND status != 'revoked' ORDER BY created_at DESC",
            (slug,), one=True)
    if not cx:
        raise GatewayError(404, "invalid_request", f"no live connection on {slug}")
    until = int(time.time() + days * 86400)
    spec = json.dumps({"type": "connector-hold", "connection": cx["id"], "connector": slug,
                       "account": cx["account"], "hold_until": until}, sort_keys=True, separators=(",", ":"))
    appr = governance.create_approval(f"connector-hold-{slug}", spec, kind="connector-hold", requester=requester,
                                      reasoning=f"keep {cx['account']}'s link open through a vendor outage",
                                      ttl_seconds=24 * 3600)
    return {"approval_id": appr["id"], "connection": cx["id"], "hold_until": until}


def release_hold(slug: str) -> int:
    return _q("UPDATE agent_connections SET hold_until = NULL, hold_approval_id = NULL, "
              "status = CASE WHEN status = 'held' THEN 'dormant' ELSE status END WHERE slug = ? AND status != 'revoked'",
              (slug,))


def on_approval_resolved(row: dict) -> None:
    """governance._on_resolved hook for kinds connector-link / connector-hold / gateway-thaw."""
    if row.get("kind") == "gateway-thaw":
        if row.get("status") == "allowed":
            _set_frozen(False, row.get("resolved_by") or "operator")
            governance.audit("gateway.reopened", {"approval_id": row.get("id"), "signer": row.get("resolved_by")})
            _notify("agent gateway thawed", f"{row.get('resolved_by')} re-opened the gateway; connectors stay "
                    "disabled until re-enabled, and each vendor must re-link.")
        return
    if row.get("kind") != "connector-hold" or row.get("status") != "allowed":
        return                                   # connector-link is polled by authorize_status
    spec = json.loads(row["command"])
    until = float(spec["hold_until"])
    _q("UPDATE agent_connections SET status = 'held', hold_until = ?, hold_approval_id = ? WHERE id = ? AND status != 'revoked'",
       (until, row["id"], spec["connection"]))
    # keep our side of the link open: the refresh grant may not idle out before the hold ends
    _q("UPDATE oauth_tokens SET expires_at = ? WHERE connection_id = ? AND kind = 'refresh' AND revoked_at IS NULL "
       "AND rotated_at IS NULL AND expires_at < ?", (until, spec["connection"], until))
    _notify(f"{spec['account']} held", f"connector {spec['connector']} held open until "
            f"{time.strftime('%Y-%m-%d', time.gmtime(until))}; the session resumes on the vendor's next refresh.")


def status(slug: str | None = None) -> list[dict]:
    rows = _q("SELECT * FROM agent_connections" + (" WHERE slug = ?" if slug else "") + " ORDER BY created_at DESC",
              ((slug,) if slug else ()))
    return [{k: r[k] for k in ("id", "slug", "account", "status", "created_at", "last_renewal_at",
                               "last_call_at", "hold_until", "revoke_reason")} for r in rows]


def _notify(title: str, body: str) -> None:
    try:
        db.board_insert("agent-gateway", "dispatch", "coord", title, body)
    except Exception:  # noqa: BLE001 -- notification only
        pass
