"""common.governance -- human-signed approvals, council/arena convenes and the
shared ghostwriting workspace (Wave 2, 2026-10-04).

Postgres schema: pg_schema/0069_signed_approvals_council_workspace.sql; the
sqlite twin is ensure() below. Routes: src/web/main.py ("Signed approvals",
"Council / arena", "Shared workspace"). Clients: scripts/approve.sh,
scripts/council.sh, scripts/workspace-grants.sh, scripts/approver-ctl.sh.
Design + runbook: docs/AGENT_SEGMENTATION.md "Approvals, council/arena and the
shared workspace".

Approvals
---------
"An agent can request it, but it has to be signed off by a human, preferably
with a clear signed message, so that any future approval gate to the phone
can't be directly bypassed" (operator, 2026-10-04). An approval is an SSH
signature (namespace ``corporatetraveldc-approval``) over the canonical text
of ONE request:

    corporatetraveldc-approval v2
    id: <request id>
    action: allow|deny
    kind: <sudo|council|council-close|...>
    requester: <account or ->
    expires_at: <unix seconds, integer>
    command-sha256: <sha256 hex of the exact command / council spec>
    pattern-sha256: <sha256 hex of the command pattern>
    reason-sha256: <sha256 hex of the reasoning shown to the approver>

SUPERSEDED 2026-10-07: v1 had no pattern or reason lines, so the reason the
approver read was not part of what they signed (docs/AGENT_TRUST_MODEL.md).

It verifies only against an ACTIVE row in ``approval_signers`` whose account
is also an ACTIVE ``kind=human`` board signer (so the liveness switch revokes
it with everything else), whose key is NOT any board key (the operator's
board key is passphrase-less and usable by every process running as the
operator, Claude Code included), and whose account is not the requester.
Change any field -- one participant, the deadline, one character of the
command -- and the hash no longer matches. Each request resolves once.

The phone tap can still DENY (a deny link is harmless); allow needs the
signature (db.resolve_approval_request refuses action=allow).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from common import board_sign, db

NAMESPACE_APPROVAL = "corporatetraveldc-approval"
APPROVAL_KINDS = ("sudo", "council", "council-close", "connector-link", "connector-hold",   # common/agent_gateway.py
                  "gateway-thaw", "console-login")                                    # gateway kill switch; web/routes/console.py
COUNCIL_MODES = ("council", "arena")
COUNCIL_APPROVAL_TTL_S = 24 * 3600
WORKSPACE_ROOT = "01-Sources/personal-notes/Series"
CONTRIB_ROOT = f"{WORKSPACE_ROOT}/contributions"
MAX_CONTRIBUTION_BYTES = 256 * 1024
_TASK_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ACCT_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class GovernanceError(Exception):
    """status (HTTP-ish int) + a detail that is safe to return to a client."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# -- schema (sqlite twin of 0069) -------------------------------------------

def ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return
    db._ensure_board_auth(c)
    c.execute("""CREATE TABLE IF NOT EXISTS approval_signers (
        account TEXT PRIMARY KEY, pubkey TEXT NOT NULL, key_comment TEXT,
        active INTEGER NOT NULL DEFAULT 1, registered_at REAL, deactivated_at REAL, note TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS council_sessions (
        id TEXT PRIMARY KEY, mode TEXT NOT NULL, subject TEXT NOT NULL, brief TEXT NOT NULL DEFAULT '',
        participants TEXT NOT NULL, requester TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'requested',
        created_at REAL NOT NULL, deadline REAL NOT NULL, activated_at REAL, closed_at REAL,
        approval_id TEXT, close_approval_id TEXT, missed_reported_at REAL)""")
    fresh = not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspace_grants'").fetchone()
    c.execute("""CREATE TABLE IF NOT EXISTS workspace_grants (
        id TEXT PRIMARY KEY, effect TEXT NOT NULL, account TEXT NOT NULL, task TEXT NOT NULL DEFAULT '*',
        until REAL, created_at REAL NOT NULL, created_by TEXT, note TEXT)""")
    if fresh:   # seed once, like the migration -- a revoked default must stay revoked
        c.execute("INSERT INTO workspace_grants (id, effect, account, task, until, created_at, created_by, note) "
                  "VALUES ('wg-default-all', 'grant', '*', '*', NULL, ?, 'migration-0069', "
                  "'default: every team account may contribute')", (time.time(),))


def _ensure_approvals() -> None:
    db.init_db_v21()   # approval_requests (+ the 0069 columns on sqlite)
    with db.conn() as c:
        ensure(c)


# -- approval signers ---------------------------------------------------------

def approval_signer_get(account: str) -> dict | None:
    with db.conn() as c:
        ensure(c)
        r = c.execute("SELECT account, pubkey, key_comment, active, registered_at, deactivated_at, note "
                      "FROM approval_signers WHERE account = ?", (account,)).fetchone()
    return ({**dict(r), "active": bool(r["active"])} if r else None)


def _board_pubkey_blobs() -> set[bytes]:
    with db.conn() as c:
        db._ensure_board_auth(c)
        rows = c.execute("SELECT pubkey FROM board_signers").fetchall()
    out = set()
    for r in rows:
        try:
            out.add(board_sign.pubkey_line_to_blob(r["pubkey"])[1])
        except board_sign.BoardSignError:
            pass
    return out


def approval_signer_upsert(account: str, pubkey: str, key_comment: str | None = None,
                           note: str | None = None) -> None:
    """Register (or re-key) a HUMAN's approval key. Refuses: a non-human or
    unregistered account, a non-ed25519 key, and a key equal to ANY board
    signer key (approval keys are separate on purpose -- module docstring)."""
    if not _ACCT_RE.match(account or ""):
        raise GovernanceError(400, "bad account name")
    row = db.board_signer_get(account)
    if not row or row.get("kind") != "human":
        raise GovernanceError(400, f"{account} is not a registered human board signer (board-signer-ctl.sh register ... --kind human)")
    ktype, blob, _ = board_sign.pubkey_line_to_blob(pubkey)
    if ktype != "ssh-ed25519":
        raise GovernanceError(400, "only ssh-ed25519 approval keys are accepted")
    if blob in _board_pubkey_blobs():
        raise GovernanceError(400, "that key is a board signing key -- an approval key must be a separate, passphrase-protected (or off-box) key")
    now = time.time()
    with db.conn() as c:
        ensure(c)
        if c.execute("SELECT 1 FROM approval_signers WHERE account = ?", (account,)).fetchone():
            c.execute("UPDATE approval_signers SET pubkey = ?, key_comment = ?, active = ?, deactivated_at = NULL, "
                      "note = COALESCE(?, note) WHERE account = ?", (pubkey, key_comment, True, note, account))
        else:
            c.execute("INSERT INTO approval_signers (account, pubkey, key_comment, active, registered_at, note) "
                      "VALUES (?, ?, ?, ?, ?, ?)", (account, pubkey, key_comment, True, now, note))


def approval_signer_set_active(account: str, active: bool, note: str | None = None) -> bool:
    with db.conn() as c:
        ensure(c)
        cur = c.execute("UPDATE approval_signers SET active = ?, deactivated_at = ?, note = COALESCE(?, note) "
                        "WHERE account = ?", (active, None if active else time.time(), note, account))
        return (cur.rowcount or 0) > 0


def approval_signer_list() -> list[dict]:
    with db.conn() as c:
        ensure(c)
        rows = c.execute("SELECT account, key_comment, active, registered_at, deactivated_at FROM approval_signers "
                         "ORDER BY account").fetchall()
    return [{**dict(r), "active": bool(r["active"])} for r in rows]


# -- approval requests --------------------------------------------------------

def command_sha256(command: str) -> str:
    return hashlib.sha256((command or "").encode("utf-8")).hexdigest()


def approval_canonical(row: dict, action: str) -> bytes:
    """The exact bytes a human signs. Built from the stored row; the client
    (scripts/approve.sh) rebuilds it from the request it DISPLAYED. v2
    (2026-10-07) also binds the command pattern and the reasoning text."""
    return "\n".join([
        "corporatetraveldc-approval v2",
        f"id: {row['id']}",
        f"action: {action}",
        f"kind: {row.get('kind') or 'sudo'}",
        f"requester: {row.get('requester') or '-'}",
        f"expires_at: {int(row['expires_at'])}",
        f"command-sha256: {command_sha256(row['command'])}",
        f"pattern-sha256: {command_sha256(row.get('command_pattern') or '')}",
        f"reason-sha256: {command_sha256(row.get('reasoning') or '')}",
    ]).encode("utf-8")


# 2026-10-07: upper bound on how long any request can wait for a signature,
# per kind. A caller may ask for less, never more -- a pending request must
# not become latent authority (docs/AGENT_TRUST_MODEL.md, invariant 2).
MAX_TTL_S = {
    "sudo": 600, "console-login": 600, "connector-link": 900, "gateway-thaw": 3600,
    "connector-hold": 24 * 3600, "council": COUNCIL_APPROVAL_TTL_S, "council-close": COUNCIL_APPROVAL_TTL_S,
}
DEFAULT_MAX_TTL_S = 600


def create_approval(command_pattern: str, command: str, *, kind: str = "sudo", requester: str | None = None,
                    reasoning: str = "", ttl_seconds: float = 600.0) -> dict:
    """create_approval_request + kind/requester. Returns the db result (with
    the one-time tap keys; only the DENY key is still useful)."""
    if kind not in APPROVAL_KINDS:
        raise GovernanceError(400, f"kind must be one of {APPROVAL_KINDS}")
    cap = MAX_TTL_S.get(kind, DEFAULT_MAX_TTL_S)
    if not (0 < float(ttl_seconds) <= cap):
        raise GovernanceError(400, f"ttl_seconds for kind {kind} must be in (0, {cap}]")
    _ensure_approvals()
    rid = str(uuid.uuid4())
    res = db.create_approval_request(rid, command_pattern, command, reasoning=reasoning, ttl_seconds=ttl_seconds)
    with db.conn() as c:
        c.execute("UPDATE approval_requests SET kind = ?, requester = ? WHERE id = ?", (kind, requester, rid))
    audit("approval.requested", {"id": rid, "kind": kind, "requester": requester, "pattern": command_pattern,
                                 "command_sha256": command_sha256(command), "ttl_s": float(ttl_seconds)})
    return res


def audit(action: str, detail: dict) -> None:
    """Governance events into the hash-chained audit_log (2026-10-07). Never
    raises: an audit write failing must not change an authorization result,
    and it is logged instead."""
    try:
        db.audit(action, "governance", None, None, detail)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("audit write failed for %s", action)


def approval_view(request_id: str) -> dict | None:
    """Public view (no key hashes) incl. command + the sha the client re-derives."""
    _ensure_approvals()
    row = db.get_approval_request(request_id)   # applies expiry-on-read
    if row is None:
        return None
    row = {k: v for k, v in row.items() if k != "resolution_sig"}
    row["command_sha256"] = command_sha256(row.get("command") or "")
    row["kind"] = row.get("kind") or "sudo"
    return row


def approvals_pending(limit: int = 50) -> list[dict]:
    _ensure_approvals()
    with db.conn() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM approval_requests WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),)).fetchall()]
    out = [approval_view(i) for i in ids]
    return [r for r in out if r and r["status"] == "pending"]


def resolve_signed(request_id: str, action: str, signer: str, signature_b64: str,
                   now: float | None = None) -> dict:
    """Resolve a pending request with a human signature. Raises
    GovernanceError (403 for every authorization failure, 404/409 otherwise)
    WITHOUT changing state unless everything checks out."""
    if action not in ("allow", "deny"):
        raise GovernanceError(400, "action must be allow or deny")
    _ensure_approvals()
    now = time.time() if now is None else now
    with db.conn() as c:
        r = c.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
    if r is None:
        raise GovernanceError(404, "approval request not found")
    row = dict(r)
    if row["status"] != "pending" or row["expires_at"] <= now:
        raise GovernanceError(409, f"request is {row['status'] if row['status'] != 'pending' else 'expired'}, not pending")
    appr = approval_signer_get(signer)
    if not appr or not appr["active"]:
        raise GovernanceError(403, "unknown or inactive approval signer")
    board = db.board_signer_get(signer)
    if not board or not board.get("active") or board.get("kind") != "human":
        raise GovernanceError(403, "approval signer is not an active human account (liveness chain)")
    if row.get("requester") and row["requester"] == signer:
        raise GovernanceError(403, "the requester cannot approve its own request")
    try:
        _, ablob, _ = board_sign.pubkey_line_to_blob(appr["pubkey"])
        if ablob in _board_pubkey_blobs():
            raise GovernanceError(403, "approval key equals a board key -- re-register a separate approval key")
        ok = board_sign.verify(appr["pubkey"], signature_b64, approval_canonical(row, action),
                               signer, namespace=NAMESPACE_APPROVAL)
    except board_sign.BoardSignError as e:
        raise GovernanceError(403, f"approval signature rejected: {e}")
    if not ok:
        raise GovernanceError(403, "approval signature does not verify for this exact request")
    status = "allowed" if action == "allow" else "denied"
    with db.conn() as c:
        cur = c.execute("UPDATE approval_requests SET status = ?, resolved_at = ?, resolved_by = ?, resolution_sig = ? "
                        "WHERE id = ? AND status = 'pending' AND expires_at > ?",
                        (status, now, signer, signature_b64, request_id, now))
        if (cur.rowcount or 0) != 1:
            raise GovernanceError(409, "request was resolved concurrently")
    row.update(status=status, resolved_at=now, resolved_by=signer)
    audit("approval.resolved", {"id": request_id, "kind": row.get("kind") or "sudo", "status": status,
                                "signer": signer, "via": "signature", "requester": row.get("requester")})
    _on_resolved(row)
    return approval_view(request_id)


# -- council / arena ------------------------------------------------------------

def _council_spec(mode: str, subject: str, brief: str, participants: list[dict], deadline: int,
                  requester: str) -> str:
    """Canonical JSON of a convene -- this IS the approval's command, so the
    human's signature covers every field."""
    return json.dumps({"type": "council-convene", "mode": mode, "subject": subject,
                       "brief_sha256": hashlib.sha256(brief.encode()).hexdigest(),
                       "participants": participants, "deadline": deadline,
                       "requester": requester}, sort_keys=True, separators=(",", ":"))


def council_create(requester: str, mode: str, subject: str, brief: str, participants: list,
                   deadline_hours: float, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if mode not in COUNCIL_MODES:
        raise GovernanceError(400, f"mode must be one of {COUNCIL_MODES}")
    subject = (subject or "").strip()
    if not subject or len(subject) > 300:
        raise GovernanceError(400, "subject (a workspace path or task id) is required, <= 300 chars")
    if len(brief or "") > 8000:
        raise GovernanceError(400, "brief is limited to 8000 chars")
    if not (1 <= float(deadline_hours) <= 336):
        raise GovernanceError(400, "deadline_hours must be 1..336")
    parts, seen = [], set()
    for p in participants or []:
        acct = (p.get("account") if isinstance(p, dict) else str(p)).strip()
        if not _ACCT_RE.match(acct) or acct in seen:
            raise GovernanceError(400, f"bad or duplicate participant {acct!r}")
        if not db.board_signer_get(acct):
            raise GovernanceError(400, f"participant {acct} is not a registered board signer")
        seen.add(acct)
        parts.append({"account": acct, "required": bool(p.get("required")) if isinstance(p, dict) else False})
    if not (1 <= len(parts) <= 12):
        raise GovernanceError(400, "1..12 participants")
    parts.sort(key=lambda d: d["account"])
    deadline = int(now + float(deadline_hours) * 3600)
    cid = "c" + uuid.uuid4().hex[:10]
    spec = _council_spec(mode, subject, brief or "", parts, deadline, requester)
    appr = create_approval(f"council-{mode}", spec, kind="council", requester=requester,
                           reasoning=f"{mode} on {subject}", ttl_seconds=COUNCIL_APPROVAL_TTL_S)
    with db.conn() as c:
        ensure(c)
        c.execute("INSERT INTO council_sessions (id, mode, subject, brief, participants, requester, status, "
                  "created_at, deadline, approval_id) VALUES (?, ?, ?, ?, ?, ?, 'requested', ?, ?, ?)",
                  (cid, mode, subject, brief or "", json.dumps(parts), requester, now, deadline, appr["id"]))
    db.board_insert("council", "dispatch", "council", f"[{mode}] approval needed: {subject}",
                    f"{requester} requests a {mode} (id {cid}) on {subject}; participants "
                    f"{', '.join(p['account'] + ('*' if p['required'] else '') for p in parts)}. "
                    f"Nothing happens until a human signs it: scripts/approve.sh show {appr['id']} "
                    f"then scripts/approve.sh allow {appr['id']}.")
    return {"id": cid, "status": "requested", "approval_id": appr["id"], "deadline": deadline,
            "mode": mode, "participants": parts}


def council_get(cid: str) -> dict | None:
    with db.conn() as c:
        ensure(c)
        r = c.execute("SELECT * FROM council_sessions WHERE id = ?", (cid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["participants"] = json.loads(d["participants"] or "[]")
    d["task"] = f"council-{d['id']}"
    d["output"] = f"{CONTRIB_ROOT}/council-{d['id']}/<account>/"
    return d


def council_list(status: str | None = None, limit: int = 50) -> list[dict]:
    with db.conn() as c:
        ensure(c)
        if status:
            rows = c.execute("SELECT id FROM council_sessions WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                             (status, limit)).fetchall()
        else:
            rows = c.execute("SELECT id FROM council_sessions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [council_get(r["id"]) for r in rows]


def council_for_approval(approval_id: str) -> dict | None:
    with db.conn() as c:
        ensure(c)
        r = c.execute("SELECT id FROM council_sessions WHERE approval_id = ? OR close_approval_id = ?",
                      (approval_id, approval_id)).fetchone()
    return council_get(r["id"]) if r else None


def council_request_close(cid: str, requester: str) -> dict:
    cs = council_get(cid)
    if not cs:
        raise GovernanceError(404, "council not found")
    if cs["status"] != "active":
        raise GovernanceError(409, f"council is {cs['status']}, not active")
    spec = json.dumps({"type": "council-close", "id": cid, "mode": cs["mode"], "subject": cs["subject"],
                       "requester": requester}, sort_keys=True, separators=(",", ":"))
    appr = create_approval(f"council-close-{cs['mode']}", spec, kind="council-close", requester=requester,
                           reasoning=f"close {cs['mode']} {cid}", ttl_seconds=COUNCIL_APPROVAL_TTL_S)
    with db.conn() as c:
        c.execute("UPDATE council_sessions SET close_approval_id = ? WHERE id = ?", (appr["id"], cid))
    return {"id": cid, "close_approval_id": appr["id"]}


def _on_resolved(row: dict) -> None:
    kind = row.get("kind") or "sudo"
    if kind in ("connector-link", "connector-hold", "gateway-thaw"):
        from common import agent_gateway
        agent_gateway.on_approval_resolved(row)
        return
    if kind not in ("council", "council-close"):
        return
    cs = council_for_approval(row["id"])
    if not cs:
        return
    now = time.time()
    task = f"council-{cs['id']}"
    if kind == "council":
        if row["status"] != "allowed":
            with db.conn() as c:
                c.execute("UPDATE council_sessions SET status = 'denied' WHERE id = ?", (cs["id"],))
            return
        with db.conn() as c:
            c.execute("UPDATE council_sessions SET status = 'active', activated_at = ? WHERE id = ?", (now, cs["id"]))
            for p in cs["participants"]:
                c.execute("DELETE FROM workspace_grants WHERE id = ?", (f"wg-{task}-{p['account']}",))
                c.execute("INSERT INTO workspace_grants (id, effect, account, task, until, created_at, created_by, note) "
                          "VALUES (?, 'grant', ?, ?, ?, ?, ?, ?)",
                          (f"wg-{task}-{p['account']}", p["account"], task, float(cs["deadline"]), now,
                           row.get("resolved_by"), f"{cs['mode']} participant"))
        rules = ("ARENA: work blind -- you cannot read other participants' contributions until a human closes the round."
                 if cs["mode"] == "arena" else
                 "COUNCIL: collaborative -- read the others' contributions as they land and build on them.")
        for p in cs["participants"]:
            db.board_insert("council", p["account"], "council",
                            f"[{cs['mode']}] {'REQUIRED' if p['required'] else 'invited'}: {cs['subject']}",
                            f"Convene {cs['id']} approved by {row.get('resolved_by')}. Subject: {cs['subject']}\n"
                            f"Deadline: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(cs['deadline']))}\n"
                            f"Contribute with task={task} (POST /api/v1/workspace/contribute, signed).\n{rules}\n"
                            f"Brief:\n{cs['brief']}")
    elif kind == "council-close" and row["status"] == "allowed":
        with db.conn() as c:
            c.execute("UPDATE council_sessions SET status = 'closed', closed_at = ? WHERE id = ?", (now, cs["id"]))
            c.execute("DELETE FROM workspace_grants WHERE task = ?", (task,))
        for p in cs["participants"]:
            db.board_insert("council", p["account"], "council", f"[{cs['mode']}] closed: {cs['subject']}",
                            f"Convene {cs['id']} closed by {row.get('resolved_by')}. "
                            f"All contributions under {CONTRIB_ROOT}/{task}/ are now readable by every participant.")


def council_missed(lister, now: float | None = None) -> list[dict]:
    """Active convenes past their deadline: which REQUIRED participants have
    no contribution. `lister(path)` returns the file list under a
    business-relative vault path. Marks each convene reported once (a miss is
    reported to the operator, never a kill -- non-response is not an attack)."""
    now = time.time() if now is None else now
    out = []
    for cs in council_list("active", 200):
        if cs["deadline"] > now or cs.get("missed_reported_at"):
            continue
        task = f"council-{cs['id']}"
        missing = [p["account"] for p in cs["participants"]
                   if p["required"] and not lister(f"{CONTRIB_ROOT}/{task}/{p['account']}")]
        with db.conn() as c:
            c.execute("UPDATE council_sessions SET missed_reported_at = ? WHERE id = ?", (now, cs["id"]))
        out.append({"id": cs["id"], "mode": cs["mode"], "subject": cs["subject"], "missing": missing})
    return out


# -- shared workspace -----------------------------------------------------------

def grant_add(effect: str, account: str, task: str = "*", until: float | None = None,
              created_by: str | None = None, note: str | None = None, gid: str | None = None) -> str:
    if effect not in ("grant", "deny"):
        raise GovernanceError(400, "effect must be grant or deny")
    if account != "*" and not _ACCT_RE.match(account):
        raise GovernanceError(400, "bad account")
    if task != "*" and not _TASK_RE.match(task):
        raise GovernanceError(400, "bad task id (lowercase letters, digits, '-')")
    gid = gid or "wg-" + uuid.uuid4().hex[:12]
    with db.conn() as c:
        ensure(c)
        c.execute("DELETE FROM workspace_grants WHERE id = ?", (gid,))
        c.execute("INSERT INTO workspace_grants (id, effect, account, task, until, created_at, created_by, note) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (gid, effect, account, task, until, time.time(), created_by, note))
    return gid


def grant_remove(gid: str) -> bool:
    with db.conn() as c:
        ensure(c)
        cur = c.execute("DELETE FROM workspace_grants WHERE id = ?", (gid,))
        return (cur.rowcount or 0) > 0


def grant_list() -> list[dict]:
    with db.conn() as c:
        ensure(c)
        return [dict(r) for r in c.execute("SELECT * FROM workspace_grants ORDER BY created_at").fetchall()]


def write_allowed(account: str, task: str, now: float | None = None) -> tuple[bool, str]:
    """Deny wins; an expired row is ignored both ways; no grant = no write."""
    now = time.time() if now is None else now
    rows = [g for g in grant_list()
            if g["account"] in (account, "*") and g["task"] in (task, "*")
            and (g["until"] is None or g["until"] > now)]
    deny = [g for g in rows if g["effect"] == "deny"]
    if deny:
        return False, f"denied by grant {deny[0]['id']}" + (f" ({deny[0]['note']})" if deny[0].get("note") else "")
    if any(g["effect"] == "grant" for g in rows):
        return True, "granted"
    return False, f"no grant for {account} on task {task}"


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return (s or "contribution")[:60]


def contribution_path(account: str, task: str, title: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    return f"{CONTRIB_ROOT}/{task}/{account}/{stamp}-{_slug(title)}.md"


def check_contribution(account: str, task: str, content: str) -> None:
    """Every refusal a contribution can hit before any write. Council tasks
    additionally need an ACTIVE convene that names the account."""
    if not _TASK_RE.match(task or ""):
        raise GovernanceError(400, "task must match [a-z0-9][a-z0-9-]{0,63}")
    if len((content or "").encode("utf-8")) > MAX_CONTRIBUTION_BYTES:
        raise GovernanceError(413, f"contribution larger than {MAX_CONTRIBUTION_BYTES} bytes")
    if not (content or "").strip():
        raise GovernanceError(400, "empty contribution")
    if task.startswith("council-"):
        cs = council_get(task[len("council-"):])
        if not cs or cs["status"] != "active":
            raise GovernanceError(403, f"{task} is not an active convene")
        if account not in {p["account"] for p in cs["participants"]}:
            raise GovernanceError(403, f"{account} is not a participant of {task}")
    ok, why = write_allowed(account, task)
    if not ok:
        raise GovernanceError(403, why)


def render_contribution(account: str, task: str, title: str, content: str, signature_b64: str,
                        now: float | None = None) -> str:
    now = time.time() if now is None else now
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    fm = ["---", f"account: {account}", f"task: {task}", f"title: {json.dumps(title or '')}",
          f"content_sha256: {sha}", f"submitted_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}",
          f"request_signature: {signature_b64}", "status: draft -- agents never publish", "---", ""]
    return "\n".join(fm) + content.rstrip("\n") + "\n"


def arena_read_allowed(path: str, signer: str | None) -> tuple[bool, str]:
    """Blind arena: while a convene in arena mode is active, a participant may
    read only its own folder under contributions/council-<id>/, and a
    key-only (unattributed) reader none of it."""
    prefix = f"{CONTRIB_ROOT}/council-"
    if not path.startswith(prefix):
        return True, ""
    rest = path[len(prefix):]
    cid = rest.split("/", 1)[0]
    cs = council_get(cid)
    if not cs or cs["mode"] != "arena" or cs["status"] != "active":
        return True, ""
    sub = rest[len(cid):].lstrip("/")
    if not sub:
        return True, ""   # the convene folder itself (depth-1 file list; folders are not listed)
    owner = sub.split("/", 1)[0]
    if signer and owner == signer:
        return True, ""
    return False, "arena is blind until a human closes it: only your own contributions are readable"
