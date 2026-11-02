"""common.withheld -- withheld categories and the passes that reveal them (2026-11-02).

Operator 2026-11-02: privileged aircraft (FAA LADD), privileged trains and privileged
vessels are withheld from every caller that does not hold that category's pass,
human or agent account alike. A direct lookup answers "No aircraft / train / vessel
available", the same as for something we have no data on, so the answer never
confirms a listing. The public demo stays fully scrubbed.

This supersedes the 2026-10-20 rule "public use is LADD-restricted, internal use is
not": a network-origin default close set before any per-caller control existed.
FAA terms allow internal display; a pass makes "internal" a verified holder.

Categories (CATEGORIES; a future category is one more entry, and the master grant
covers it automatically):
  aircraft  grant ladd.view    lists: faa_ladd_aircraft (the FAA LADD import, with PIA
                               call signs) + withheld_identifiers 'aircraft' (opt-out
                               requests from any country); rule: a non-US private aircraft
                               follows its own country's default (privacy_jurisdictions)
  rail      grant rail.view    list: withheld_identifiers, category 'rail' (operator-kept)
  vessel    grant vessel.view  list: withheld_identifiers, category 'vessel' (operator-kept)

Passes and the master token:
  * A pass is a bearer token whose allowed_actions name one or more category grants.
    Issued with the operator's SSH-signed `withheld-pass` approval and a one-time
    pairing code; agents get a grant only through mint-agent-api-token.sh.
  * The master token (allowed_actions `withheld.master`) reveals every category,
    present and future. It is honoured only while ALL of these hold: its row in
    withheld_masters exists (minted by request_master/pair, never by a plain token
    mint), the request it was minted from carries BOTH the operator's SSH approval
    signature and a GPG signature by the pinned operator primary key (the agent
    delegate key is refused), and its holder is still an active human board signer
    with role admin. Every kill wipes master tokens (rows deleted, not revoked); a
    thaw never restores one -- mint again.

Kill switch: kill_all() freezes every category, revokes every category pass and wipes
every master token. scripts/withheld.sh off, scripts/lockdown.sh and
agent_gateway.kill_all() all call it. A thaw needs the operator's signed
`withheld-thaw` approval.

Audit (withheld_audit), keyed digests only, never an identifier:
  * kind 'reveal'  : a holder was shown a withheld item; one row per identifier per
    user per minute, digest = HMAC-SHA-512(key, "<minute>|<user>|<category>:<kind>:<value>").
  * kind 'lockdown': while frozen, every request that touched a withheld item, one
    row per identifier, digest = HMAC-SHA-512(key, "<category>:<kind>:<value>").
HMAC rather than a bare SHA-512: identifier spaces (registrations, train numbers,
MMSIs) are small enough to reverse an unkeyed hash by enumeration. The key is
WITHHELD_AUDIT_KEY (production secrets, >= 32 chars); without it nothing is revealed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from common import db

log = logging.getLogger(__name__)

MASTER_ACTION = "withheld.master"
PASS_HEADER = "X-CTDC-Withheld-Pass"
MAX_PASS_HOURS = 12.0
PAIR_TTL_S = 600
KEY_ENV = "WITHHELD_AUDIT_KEY"
_DEVICE_RE = re.compile(r"^[A-Za-z0-9._-]{2,40}$")
_USER_RE = re.compile(r"^[a-z0-9-]{2,64}$")
_ROOT = Path(__file__).resolve().parents[2]
TRUSTED_KEY = _ROOT / "security" / "trusted-signing-key.pub.asc"
SIGNING_ENV = _ROOT / "security" / "signing.env"
SIGNING_PIN = Path("/etc/corporatetraveldc/signing-pin")


class WithheldError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class WithheldUnavailable(Exception):
    """A list lookup failed; the caller must withhold (fail closed)."""


# -- categories -----------------------------------------------------------------------

def _ladd_lookup(by_kind: dict[str, set[str]]) -> set[tuple[str, str]]:
    """FAA LADD (with PIA call signs, db.faa_ladd_listed_batch) plus the operator-kept
    aircraft list: opt-out requests from any country (2026-11-02)."""
    out = _list_lookup("aircraft")(by_kind)
    hexes = by_kind.get("hex", set())
    ids = by_kind.get("reg", set()) | by_kind.get("callsign", set())
    lh = {h.lower() for h in (db.faa_ladd_hex_batch(hexes) if hexes else set())}
    li = {i.upper() for i in (db.faa_ladd_listed_batch(ids) if ids else set())}
    out |= {("hex", h) for h in hexes if h in lh}
    out |= {(k, v) for k in ("reg", "callsign") for v in by_kind.get(k, set()) if v in li}
    return out


def _list_lookup(category: str) -> Callable[[dict[str, set[str]]], set[tuple[str, str]]]:
    def lookup(by_kind: dict[str, set[str]]) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        with db.conn() as c:
            ensure(c)
            for kind, values in by_kind.items():
                vals = sorted(values)
                for i in range(0, len(vals), 500):
                    chunk = vals[i:i + 500]
                    rows = c.execute("SELECT value FROM withheld_identifiers WHERE category = ? AND kind = ? "
                                     "AND value IN (" + ",".join("?" * len(chunk)) + ")",
                                     (category, kind, *chunk)).fetchall()
                    out |= {(kind, r["value"]) for r in rows}
        return out
    return lookup


def _country_rule(item: dict) -> bool:
    """Non-US private aircraft follow their own country's default (privacy_jurisdictions)."""
    from common import privacy_jurisdictions
    return privacy_jurisdictions.withheld_by_country(item)


@dataclass(frozen=True)
class Category:
    name: str
    grant: str
    noun: str
    keys: tuple                     # ((kind, (item keys...), lower?), ...)
    lookup: Callable
    item_rule: Optional[Callable[[dict], bool]] = None   # per-item default (e.g. country policy)

    @property
    def not_available(self) -> str:
        return f"No {self.noun} available"


CATEGORIES: dict[str, Category] = {
    "aircraft": Category("aircraft", "ladd.view", "aircraft",
                         (("reg", ("registration", "r"), False),
                          ("callsign", ("callsign", "identifier", "flight_id", "flight"), False),
                          ("hex", ("hex", "hex_id"), True)), _ladd_lookup, _country_rule),
    "rail": Category("rail", "rail.view", "train",
                     (("train", ("train_num", "train_number", "train_id", "identifier"), False),), _list_lookup("rail")),
    "vessel": Category("vessel", "vessel.view", "vessel",
                       (("mmsi", ("mmsi", "identifier"), False), ("imo", ("imo",), False),
                        ("name", ("name",), False), ("callsign", ("callsign",), False)), _list_lookup("vessel")),
}
NOT_AVAILABLE = CATEGORIES["aircraft"].not_available       # "No aircraft available"


def category(name: str) -> Category:
    try:
        return CATEGORIES[name]
    except KeyError:
        raise WithheldError(400, f"unknown category {name!r}; one of {', '.join(CATEGORIES)}") from None


@dataclass(frozen=True)
class WithheldView:
    grants: frozenset = field(default_factory=frozenset)    # categories this caller may see
    frozen: frozenset = field(default_factory=frozenset)    # categories under the kill switch
    user: Optional[str] = None
    device: Optional[str] = None
    master: bool = False
    path: str = ""

    def allowed(self, cat: str) -> bool:
        return cat in self.grants and cat not in self.frozen


ANON = WithheldView()


def view_of(v) -> WithheldView:
    """A route called directly (tests, internal callers) gets the Depends marker as its
    default; anything that is not a WithheldView is the anonymous view."""
    return v if isinstance(v, WithheldView) else ANON


# -- storage ------------------------------------------------------------------------

def ensure(c) -> None:
    from common import db_backend
    if db_backend.backend() == "postgres":
        return                                   # pg_schema/0085_withheld.sql
    c.execute("""CREATE TABLE IF NOT EXISTS withheld_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at REAL NOT NULL, updated_by TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS withheld_identifiers (category TEXT NOT NULL, kind TEXT NOT NULL,
        value TEXT NOT NULL, added_at REAL NOT NULL, added_by TEXT, PRIMARY KEY (category, kind, value))""")
    c.execute("""CREATE TABLE IF NOT EXISTS withheld_pairings (code_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
        categories TEXT NOT NULL, approval_id TEXT NOT NULL, device_label TEXT NOT NULL, hours REAL NOT NULL,
        issued_by TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL,
        gpg_fpr TEXT, gpg_verified_at REAL, token_hash TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS withheld_masters (token_hash TEXT PRIMARY KEY, user_label TEXT NOT NULL,
        device_label TEXT NOT NULL, approval_id TEXT NOT NULL, gpg_fpr TEXT NOT NULL, created_at REAL NOT NULL,
        expires_at REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS withheld_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
        minute TEXT NOT NULL, kind TEXT NOT NULL, category TEXT NOT NULL, user_label TEXT, device_label TEXT,
        path TEXT, digest TEXT NOT NULL)""")
    try:
        c.execute("ALTER TABLE auth_tokens ADD COLUMN allowed_actions TEXT")
    except Exception:  # noqa: BLE001 -- already there
        pass


def _q(sql: str, args: tuple = (), one: bool = False):
    with db.conn() as c:
        ensure(c)
        cur = c.execute(sql, args)
        if sql.lstrip().upper().startswith("SELECT"):
            rows = [dict(r) for r in cur.fetchall()]
            return (rows[0] if rows else None) if one else rows
        return cur.rowcount


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# -- operator-kept lists (rail, vessel) ------------------------------------------------

def _norm(cat: Category, kind: str, value: str) -> str:
    lower = next((lo for k, _, lo in cat.keys if k == kind), None)
    if lower is None:
        raise WithheldError(400, f"{cat.name}: kind must be one of {', '.join(k for k, _, _ in cat.keys)}")
    v = str(value or "").strip()
    return v.lower() if lower else v.upper()


def list_add(cat_name: str, kind: str, value: str, actor: str) -> int:
    cat = category(cat_name)
    v = _norm(cat, kind, value)
    if not v or len(v) > 64:
        raise WithheldError(400, "value: 1-64 characters")
    n = _q("DELETE FROM withheld_identifiers WHERE category = ? AND kind = ? AND value = ?", (cat.name, kind, v))
    _q("INSERT INTO withheld_identifiers (category, kind, value, added_at, added_by) VALUES (?, ?, ?, ?, ?)",
       (cat.name, kind, v, time.time(), actor))
    _gov_audit("withheld.list.added", {"category": cat.name, "kind": kind, "actor": actor[:64]})
    return 0 if n else 1


def list_remove(cat_name: str, kind: str, value: str, actor: str) -> int:
    cat = category(cat_name)
    n = _q("DELETE FROM withheld_identifiers WHERE category = ? AND kind = ? AND value = ?",
           (cat.name, kind, _norm(cat, kind, value)))
    _gov_audit("withheld.list.removed", {"category": cat.name, "kind": kind, "actor": actor[:64], "removed": n})
    return n


def list_counts() -> dict[str, int]:
    rows = _q("SELECT category, count(*) AS n FROM withheld_identifiers GROUP BY category")
    out = {r["category"]: int(r["n"]) for r in rows}
    try:
        out["aircraft"] = int(db.faa_ladd_count())
    except Exception:  # noqa: BLE001
        out["aircraft"] = -1
    return out


# -- the switch -----------------------------------------------------------------------

def frozen_set() -> frozenset:
    """Categories under the kill switch. A read failure counts as all frozen."""
    try:
        rows = _q("SELECT key, value FROM withheld_settings WHERE key LIKE 'frozen%'")
    except Exception as e:  # noqa: BLE001
        log.warning("withheld: switch unreadable, treated as frozen: %s", type(e).__name__)
        return frozenset(CATEGORIES)
    on = {r["key"] for r in rows if r["value"] == "1"}
    if "frozen" in on:
        return frozenset(CATEGORIES)
    return frozenset(c for c in CATEGORIES if f"frozen:{c}" in on)


def frozen() -> bool:
    return bool(frozen_set())


def _set(key: str, on: bool, actor: str) -> None:
    _q("DELETE FROM withheld_settings WHERE key = ?", (key,))
    _q("INSERT INTO withheld_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
       (key, "1" if on else "0", time.time(), actor))


def _grant_tokens() -> list[dict]:
    rows = _q("SELECT token_hash, user_label, device_label, expires_at, allowed_actions "
              "FROM auth_tokens WHERE revoked_at IS NULL AND allowed_actions IS NOT NULL")
    return [r for r in rows if grants_of(r.get("allowed_actions")) or MASTER_ACTION in _actions(r.get("allowed_actions"))]


def wipe_masters(reason: str) -> int:
    """Delete every master token outright (rows gone, not revoked)."""
    rows = _q("SELECT token_hash FROM withheld_masters")
    for r in rows:
        _q("DELETE FROM auth_tokens WHERE token_hash = ?", (r["token_hash"],))
    _q("DELETE FROM withheld_masters")
    _q("DELETE FROM auth_tokens WHERE revoked_at IS NULL AND allowed_actions LIKE ?", (f"%{MASTER_ACTION}%",))
    if rows:
        _gov_audit("withheld.master.wiped", {"count": len(rows), "reason": reason[:200]})
    return len(rows)


def kill_all(actor: str, reason: str = "withheld kill-all", categories: Iterable[str] | None = None) -> int:
    """Freeze categories (all when None), revoke the passes carrying their grants (a
    mixed-scope token is revoked whole) and, for a full kill, wipe every master token.
    Returns the number of tokens revoked or wiped."""
    cats = [category(c).name for c in categories] if categories else list(CATEGORIES)
    full = set(cats) == set(CATEGORIES)
    if full:
        _set("frozen", True, actor)
    for c in cats:
        _set(f"frozen:{c}", True, actor)
    now = time.time()
    n = 0
    for r in _grant_tokens():
        if MASTER_ACTION in _actions(r.get("allowed_actions")) and not grants_of(r.get("allowed_actions")):
            continue                                         # masters are handled by wipe_masters
        if grants_of(r.get("allowed_actions")) & set(cats):
            n += _q("UPDATE auth_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                    (now, r["token_hash"]))
    if full:
        n += wipe_masters(reason)
    _q("UPDATE withheld_pairings SET used_at = ? WHERE used_at IS NULL", (now,))
    _gov_audit("withheld.killed", {"actor": actor[:64], "categories": cats, "tokens": n, "reason": reason[:200]})
    _notify("withheld categories KILLED", f"{actor}: {', '.join(cats)} frozen; {n} pass(es)/master token(s) "
            "revoked or wiped. Thaw needs a signature; masters must be re-minted.")
    return n


def revoke_device(device: str, actor: str) -> int:
    rows = [r for r in _grant_tokens() if (r.get("device_label") or "") == device]
    for r in rows:
        _q("UPDATE auth_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
           (time.time(), r["token_hash"]))
        _q("DELETE FROM withheld_masters WHERE token_hash = ?", (r["token_hash"],))
    _gov_audit("withheld.pass.revoked", {"actor": actor[:64], "device": device[:40], "tokens": len(rows)})
    return len(rows)


def revoke_token(raw: str) -> int:
    """The holder gives its own pass up (runner 'remove from this device')."""
    th = _h(raw)
    _q("DELETE FROM withheld_masters WHERE token_hash = ?", (th,))
    return _q("UPDATE auth_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL", (time.time(), th))


def request_thaw(requester: str = "operator-cli") -> dict:
    from common import governance
    spec = json.dumps({"type": "withheld-thaw", "ts": int(time.time())}, sort_keys=True, separators=(",", ":"))
    appr = governance.create_approval("withheld-thaw", spec, kind="withheld-thaw", requester=requester,
                                      reasoning="re-open the withheld categories after a kill-all / lockdown",
                                      ttl_seconds=governance.approval_ttl("withheld-thaw"))
    return {"approval_id": appr["id"]}


def on_approval_resolved(row: dict) -> None:
    """governance._on_resolved hook. Passes and masters are checked at pair()."""
    if row.get("kind") == "withheld-thaw" and row.get("status") == "allowed":
        actor = row.get("resolved_by") or "operator"
        _set("frozen", False, actor)
        for c in CATEGORIES:
            _set(f"frozen:{c}", False, actor)
        _gov_audit("withheld.reopened", {"approval_id": row.get("id"), "signer": row.get("resolved_by")})
        _notify("withheld categories thawed", f"{actor} re-opened them; every pass must be re-issued and the "
                "master token re-minted.")


def status() -> dict:
    now = time.time()
    live = [r for r in _grant_tokens() if (r.get("expires_at") or 0) > now]
    masters = {r["token_hash"] for r in _q("SELECT token_hash FROM withheld_masters")}
    return {"frozen": sorted(frozen_set()), "audit_key": _key() is not None, "lists": list_counts(),
            "passes": [{"user": r["user_label"], "device": r.get("device_label"), "expires_at": r.get("expires_at"),
                        "categories": sorted(grants_of(r.get("allowed_actions"))),
                        "master": r["token_hash"] in masters} for r in live]}


# -- issuing ---------------------------------------------------------------------------

def _check_issue(device: str, hours: float, issued_by: str) -> float:
    if not _DEVICE_RE.match(device or ""):
        raise WithheldError(400, "device: letters, digits, . _ -, 2-40 chars")
    if not _USER_RE.match(issued_by or ""):
        raise WithheldError(400, "issued_by must be an account name")
    hours = float(hours)
    if not (0 < hours <= MAX_PASS_HOURS):
        raise WithheldError(400, f"hours must be in (0, {MAX_PASS_HOURS:g}]")
    return hours


def _new_pairing(kind: str, cats: list[str], device: str, hours: float, issued_by: str) -> dict:
    from common import governance
    code = secrets.token_urlsafe(18)
    akind = "withheld-master" if kind == "master" else "withheld-pass"
    spec = json.dumps({"type": akind, "categories": cats, "device": device, "hours": hours, "issued_by": issued_by,
                       "code_sha256": _h(code), "nonce": secrets.token_hex(8), "ts": int(time.time())},
                      sort_keys=True, separators=(",", ":"))
    # requester is the CLI, not the holder: governance refuses a self-approved request,
    # and the holder signs these themselves (the holder is named inside the signed spec).
    appr = governance.create_approval(akind, spec, kind=akind, requester="withheld-cli",
                                      reasoning=f"{'MASTER withheld token' if kind == 'master' else 'withheld pass'} "
                                                f"for {device}, {hours:g} h ({', '.join(cats)})",
                                      ttl_seconds=governance.approval_ttl(akind))
    now = time.time()
    _q("INSERT INTO withheld_pairings (code_hash, kind, categories, approval_id, device_label, hours, issued_by, "
       "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
       (_h(code), kind, ",".join(cats), appr["id"], device, hours, issued_by, now, now + PAIR_TTL_S))
    return {"approval_id": appr["id"], "code": code, "spec": spec, "pair_expires_at": now + PAIR_TTL_S}


def request_pass(device: str, hours: float, issued_by: str, categories: Iterable[str]) -> dict:
    """Signed request for a pass over named categories. Returns the approval id and the
    one-time pairing code (only its sha256 is stored and signed)."""
    hours = _check_issue(device, hours, issued_by)
    cats = sorted({category(c).name for c in categories})
    if not cats:
        raise WithheldError(400, f"name at least one category: {', '.join(CATEGORIES)}")
    if frozen_set() & set(cats):
        raise WithheldError(409, "a requested category is frozen; thaw first (withheld.sh on)")
    return _new_pairing("pass", cats, device, hours, issued_by)


def admin_human(account: str) -> bool:
    """An active human board signer with role admin -- the only holder a master token
    can have. Agent and service accounts never qualify."""
    try:
        r = db.board_signer_get(account)
    except Exception:  # noqa: BLE001
        return False
    return bool(r and r.get("active") and r.get("role") == "admin" and r.get("kind") == "human")


def request_master(device: str, hours: float, issued_by: str) -> dict:
    """Step 1 of a master mint. The caller (scripts/withheld.sh master) then GPG-signs
    the returned `spec` with the operator's primary key (attach_gpg) and SSH-signs the
    approval (approve.sh). Both are checked again at pair()."""
    hours = _check_issue(device, hours, issued_by)
    if not admin_human(issued_by):
        raise WithheldError(403, f"{issued_by} is not an active human admin signer; a master token is admin-only")
    if frozen():
        raise WithheldError(409, "withheld categories are frozen; thaw first, then mint again")
    return _new_pairing("master", ["*"], device, hours, issued_by)


def pinned_fingerprint() -> str:
    """The operator's PRIMARY signing key fingerprint, read literally (never sourced),
    root-owned /etc pin first, else the signed tree's security/signing.env. The agent
    delegate key (AGENT_SIGNING_KEY_FINGERPRINT) is deliberately not accepted here."""
    src = SIGNING_PIN if SIGNING_PIN.is_file() and SIGNING_PIN.stat().st_uid == 0 else SIGNING_ENV
    try:
        for line in src.read_text().splitlines():
            if line.startswith("SIGNING_KEY_FINGERPRINT="):
                v = re.sub(r"[^A-Fa-f0-9]", "", line.split("=", 1)[1]).upper()
                return v if len(v) == 40 and v != "0" * 40 else ""
    except OSError:
        pass
    return ""


def parse_validsig(status_text: str) -> tuple[str, str]:
    """(signing key fpr, primary key fpr) from gpg --status-fd output; ('', '') if none."""
    for line in status_text.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0] == "[GNUPG:]" and p[1] == "VALIDSIG":
            return p[2].upper(), p[-1].upper()
    return "", ""


def _gpg_verify(data: bytes, signature: str) -> str:
    """gpg --verify in an isolated, throwaway keyring holding only TRUSTED_KEY.
    Returns gpg's status output ('' on any failure)."""
    with tempfile.TemporaryDirectory(prefix="withheld-gpg-") as home:
        os.chmod(home, 0o700)
        env = {**os.environ, "GNUPGHOME": home}
        d, s = Path(home) / "spec", Path(home) / "spec.asc"
        d.write_bytes(data)
        s.write_text(signature)
        try:
            subprocess.run(["gpg", "--batch", "--quiet", "--import", str(TRUSTED_KEY)], env=env,
                           capture_output=True, timeout=30, check=True)
            r = subprocess.run(["gpg", "--batch", "--status-fd", "1", "--verify", str(s), str(d)], env=env,
                               capture_output=True, text=True, timeout=30)
            return r.stdout if r.returncode == 0 else ""
        except Exception as e:  # noqa: BLE001
            log.warning("withheld: gpg verify failed: %s", type(e).__name__)
            return ""
        finally:
            subprocess.run(["gpgconf", "--kill", "all"], env=env, capture_output=True, timeout=10)


def attach_gpg(approval_id: str, signature: str, verify: Callable[[bytes, str], str] | None = None) -> str:
    """Step 2 of a master mint: check the operator's detached GPG signature over the
    exact approval text and record the verified fingerprint. Raises on any mismatch."""
    from common import governance
    row = _q("SELECT * FROM withheld_pairings WHERE approval_id = ? AND kind = 'master'", (approval_id,), one=True)
    appr = governance.approval_view(approval_id) or {}
    if not row or appr.get("kind") != "withheld-master":
        raise WithheldError(404, "no master request with that approval id")
    pin = pinned_fingerprint()
    if not pin:
        raise WithheldError(409, "no operator signing key is pinned (security/signing.env)")
    status_text = (verify or _gpg_verify)((appr.get("command") or "").encode(), signature or "")
    sig_fpr, primary = parse_validsig(status_text)
    if pin not in (sig_fpr, primary):
        _gov_audit("withheld.master.gpg_refused", {"approval_id": approval_id, "signing_fpr": sig_fpr[-16:]})
        raise WithheldError(403, "GPG signature is not by the pinned operator key")
    _q("UPDATE withheld_pairings SET gpg_fpr = ?, gpg_verified_at = ? WHERE approval_id = ?",
       (primary or sig_fpr, time.time(), approval_id))
    _gov_audit("withheld.master.gpg_verified", {"approval_id": approval_id, "fpr": (primary or sig_fpr)[-16:]})
    return primary or sig_fpr


def pair(code: str) -> dict:
    """Exchange a one-time pairing code for the token. The pairing must be unused,
    inside its 10-minute window and SSH-approved; a master also needs its verified GPG
    signature and a still-active human admin holder."""
    from auth.auth import generate_token
    from common import governance
    row = _q("SELECT * FROM withheld_pairings WHERE code_hash = ?", (_h(code or ""),), one=True)
    now = time.time()
    if not row or row.get("used_at") or row["expires_at"] <= now:
        raise WithheldError(404, "pairing code not valid")
    master = row["kind"] == "master"
    appr = governance.approval_view(row["approval_id"]) or {}
    if appr.get("status") != "allowed" or appr.get("kind") != ("withheld-master" if master else "withheld-pass"):
        raise WithheldError(403, "pairing not approved")
    if _key() is None:
        raise WithheldError(409, "withheld audit key is not configured")
    if master:
        if not row.get("gpg_verified_at"):
            raise WithheldError(403, "master token needs the operator's GPG signature")
        if not admin_human(row["issued_by"]):
            raise WithheldError(403, "master token holder is no longer an active human admin")
        if frozen():
            raise WithheldError(409, "withheld categories are frozen")
    elif frozen_set() & set(row["categories"].split(",")):
        raise WithheldError(409, "a requested category is frozen")
    if _q("UPDATE withheld_pairings SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
          (now, row["code_hash"])) != 1:
        raise WithheldError(404, "pairing code not valid")
    expires = now + float(row["hours"]) * 3600
    token = generate_token(row["issued_by"], "cert", row["device_label"], expires_at=expires)
    th = _h(token)
    actions = MASTER_ACTION if master else ",".join(category(c).grant for c in row["categories"].split(","))
    _q("UPDATE auth_tokens SET allowed_actions = ? WHERE token_hash = ?", (actions, th))
    _q("UPDATE withheld_pairings SET token_hash = ? WHERE code_hash = ?", (th, row["code_hash"]))
    if master:
        _q("INSERT INTO withheld_masters (token_hash, user_label, device_label, approval_id, gpg_fpr, created_at, "
           "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
           (th, row["issued_by"], row["device_label"], row["approval_id"], row["gpg_fpr"], now, expires))
    _gov_audit("withheld.master.minted" if master else "withheld.pass.issued",
               {"approval_id": row["approval_id"], "user": row["issued_by"], "device": row["device_label"],
                "categories": row["categories"], "expires_at": expires})
    return {"token": token, "expires_at": expires, "device": row["device_label"], "master": master,
            "categories": sorted(CATEGORIES) if master else row["categories"].split(",")}


# -- per-request decision -------------------------------------------------------------------

def _actions(allowed_actions: Optional[str]) -> set[str]:
    return {p.strip() for p in (allowed_actions or "").split(",") if p.strip()}


def grants_of(allowed_actions: Optional[str]) -> frozenset:
    """Categories whose grant is named explicitly: an unrestricted (NULL) or wildcard
    token carries none."""
    acts = _actions(allowed_actions)
    return frozenset(c.name for c in CATEGORIES.values() if c.grant in acts)


def _key() -> Optional[bytes]:
    k = os.environ.get(KEY_ENV, "")
    return k.encode() if len(k) >= 32 else None


def _master_ok(token_hash: str, row: dict) -> bool:
    m = _q("SELECT * FROM withheld_masters WHERE token_hash = ?", (token_hash,), one=True)
    return bool(m and m["expires_at"] > time.time() and m["user_label"] == row.get("user_label")
                and admin_human(m["user_label"]))


def resolve(request, credentials=None) -> WithheldView:
    """FastAPI dependency body (wired through auth.resolve_withheld)."""
    path = getattr(getattr(request, "url", None), "path", "") or ""
    fz = frozen_set()
    if request.headers.get("X-CTDI-Public") == "1":
        return WithheldView(frozen=fz, path=path)
    raw = request.headers.get(PASS_HEADER) or (credentials.credentials if credentials else "")
    if not raw:
        return WithheldView(frozen=fz, path=path)
    th = _h(raw)
    try:
        row = db.lookup_token(th)
        if row is not None and "allowed_actions" not in row:
            row = {**row, **(_q("SELECT allowed_actions FROM auth_tokens WHERE token_hash = ?", (th,), one=True) or {})}
        if not row:
            return WithheldView(frozen=fz, path=path)
        acts = _actions(row.get("allowed_actions"))
        master = MASTER_ACTION in acts and _master_ok(th, row)
        grants = frozenset(CATEGORIES) if master else grants_of(row.get("allowed_actions"))
    except Exception as e:  # noqa: BLE001
        log.warning("withheld: token lookup failed (withheld): %s", type(e).__name__)
        return WithheldView(frozen=fz, path=path)
    if not grants or _key() is None:
        return WithheldView(frozen=fz, path=path)
    return WithheldView(grants=grants, frozen=fz, user=row.get("user_label"), device=row.get("device_label"),
                        master=master, path=path)


def _idents(cat: Category, item: dict) -> list[tuple[str, str]]:
    out = []
    for kind, keys, lower in cat.keys:
        for k in keys:
            v = item.get(k)
            if v not in (None, ""):
                v = str(v).strip()
                if v:
                    out.append((kind, v.lower() if lower else v.upper()))
    return out


def listed(cat_name: str, items: list[dict]) -> list[bool]:
    flags = _listed(cat_name, items)
    rule = category(cat_name).item_rule
    if rule:
        flags = [f or bool(rule(it)) for f, it in zip(flags, items)]
    return flags


def _listed(cat_name: str, items: list[dict]) -> list[bool]:
    """Per item: is it on the category's withheld list? Raises WithheldUnavailable."""
    cat = category(cat_name)
    by_kind: dict[str, set[str]] = {}
    per_item = [_idents(cat, it) for it in items]
    for ids in per_item:
        for kind, v in ids:
            by_kind.setdefault(kind, set()).add(v)
    if not by_kind:
        return [False] * len(items)
    try:
        hits = cat.lookup(by_kind)
    except Exception as e:  # noqa: BLE001
        raise WithheldUnavailable(type(e).__name__) from e
    return [any(i in hits for i in ids) for ids in per_item]


def screen(view, cat_name: str, items: list[dict]) -> tuple[list[dict], int]:
    """(items to show, number withheld) for one category. Holders see everything and
    each withheld item is audited; everyone else has them removed. Raises
    WithheldUnavailable when the list lookup fails (the caller answers 503/withholds)."""
    view = view_of(view)
    cat = category(cat_name)
    if not items:
        return [], 0
    flags = listed(cat.name, items)
    hit = [it for it, f in zip(items, flags) if f]
    if not hit:
        return list(items), 0
    if view.allowed(cat.name):
        try:
            audit_reveal(view, cat.name, hit)
        except Exception as e:  # noqa: BLE001 -- a reveal that cannot be audited does not happen
            log.warning("withheld: reveal audit failed, withheld: %s", type(e).__name__)
            return [it for it, f in zip(items, flags) if not f], len(hit)
        return list(items), 0
    if cat.name in view.frozen:
        audit_lockdown(view, cat.name, hit)
    return [it for it, f in zip(items, flags) if not f], len(hit)


def visible_one(view, cat_name: str, item: dict) -> bool:
    """Single-item form of screen(): False = answer 'No <noun> available'.
    A lookup failure is False (fail closed)."""
    try:
        kept, _ = screen(view, cat_name, [item])
    except WithheldUnavailable:
        return False
    return bool(kept)


# -- audit ---------------------------------------------------------------------------------

_seen_lock = threading.Lock()
_seen: dict = {"minute": None, "keys": set()}


def _minute(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts))


def digest(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode(), hashlib.sha512).hexdigest()


def reveal_text(minute: str, user: str, cat: str, kind: str, value: str) -> str:
    return f"{minute}|{user}|{cat}:{kind}:{value}"


def lockdown_text(cat: str, kind: str, value: str) -> str:
    return f"{cat}:{kind}:{value}"


def audit_reveal(view: WithheldView, cat_name: str, items: list[dict]) -> int:
    key = _key()
    if key is None:
        raise WithheldError(409, "audit key missing")
    cat = category(cat_name)
    now = time.time()
    minute = _minute(now)
    rows = []
    with _seen_lock:
        if _seen["minute"] != minute:
            _seen.update(minute=minute, keys=set())
        for it in items:
            for kind, v in _idents(cat, it):
                k = (view.user, view.device, cat.name, kind, v)
                if k in _seen["keys"]:
                    continue
                _seen["keys"].add(k)
                rows.append((now, minute, "reveal", cat.name, view.user, view.device, view.path[:120],
                             digest(key, reveal_text(minute, view.user or "", cat.name, kind, v))))
    _insert(rows)
    return len(rows)


def audit_lockdown(view: WithheldView, cat_name: str, items: list[dict]) -> int:
    """Every request during a freeze that touched a withheld item. Never raises."""
    key = _key()
    cat = category(cat_name)
    now = time.time()
    rows = [(now, _minute(now), "lockdown", cat.name, view.user, view.device, view.path[:120],
             digest(key, lockdown_text(cat.name, kind, v)) if key else "nokey")
            for it in items for kind, v in _idents(cat, it)]
    try:
        _insert(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("withheld: lockdown audit write failed: %s", type(e).__name__)
    return len(rows)


def _insert(rows: list[tuple]) -> None:
    if not rows:
        return
    with db.conn() as c:
        ensure(c)
        for r in rows:
            c.execute("INSERT INTO withheld_audit (ts, minute, kind, category, user_label, device_label, path, "
                      "digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", r)


def audit_check(candidates: Iterable[str], since: float = 0.0) -> list[dict]:
    """Rows whose digest matches a suspected identifier (each candidate is tried as every
    category and kind). Returns ts/kind/category/user/device/path only."""
    key = _key()
    if key is None:
        raise WithheldError(409, f"{KEY_ENV} is not set; the log cannot be checked")
    forms = set()
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        for cat in CATEGORIES.values():
            for kind, _, lower in cat.keys:
                forms.add((cat.name, kind, c.lower() if lower else c.upper()))
    lock = {digest(key, lockdown_text(*f)) for f in forms}
    out = []
    for r in _q("SELECT ts, minute, kind, category, user_label, device_label, path, digest FROM withheld_audit "
                "WHERE ts >= ? ORDER BY ts", (float(since),)):
        if r["kind"] == "lockdown":
            hit = r["digest"] in lock
        else:
            hit = any(hmac.compare_digest(r["digest"], digest(key, reveal_text(r["minute"], r["user_label"] or "",
                                                                              cat, kind, v)))
                      for cat, kind, v in forms if cat == r["category"])
        if hit:
            out.append({k: r[k] for k in ("ts", "kind", "category", "user_label", "device_label", "path")})
    return out


def _gov_audit(action: str, detail: dict) -> None:
    from common import governance
    governance.audit(action, detail)


def _notify(title: str, body: str) -> None:
    try:
        db.board_insert("withheld", "dispatch", "coord", title, body)
    except Exception:  # noqa: BLE001 -- notification only
        pass
