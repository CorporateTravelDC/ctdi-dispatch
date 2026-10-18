"""
demo.profiles — password-gated access profiles for the public demo.

Deliberately decoupled from the main app's src/auth/auth.py: that module
is tightly bound to common.db (the live operational database), and
demo_api.py's whole design point is to never touch live state (see its
module docstring). This still gets its own table and its own admin gate,
proportionate to what it actually protects -- a handful of shared
passwords keeping casual visitors out of a public playback loop, not a
multi-tenant credential system.

Storage (2026-10-16, operator rule: the public demo is logically AND
physically separated from production):

  DISPATCH_DB_BACKEND=sqlite (what both public-demo containers pin via
  demo-secrets.env) -- its own SQLite file in the demo-state volume,
  DEMO_ACCESS_DB (default /var/lib/corporatetraveldc-demo-state/
  demo_access.db, table `profiles` -- the exact file and schema this
  module used before 2026-09-20, so the rows already in that volume keep
  working). Nothing in this path can reach production: no Postgres
  socket is mounted, and common.db_backend.pg_conn() refuses under a
  non-postgres backend anyway.

  DISPATCH_ROLE=demo AND DISPATCH_DB_BACKEND=postgres (the relay-2 shape
  of the 2026-10-16 demo/production split, set by config/demo.env) --
  demo_profiles on the DEMO Postgres instance through
  common.db_backend.demo_pg_conn() (src/common/pg_schema_demo/0001, same
  columns as production's pg_schema/0057 table). This module never names
  pg_conn() at all -- tests/demo/test_demo_accessor_boundary.py holds that
  -- so there is no code path from the public demo to production,
  whatever the environment says. ~~DISPATCH_DB_BACKEND=postgres alone used
  to select pg_conn() (the 2026-09-20 shape)~~ -- removed 2026-10-16: a
  postgres backend without the demo role falls back to the sqlite file.

History: 2026-09-20 moved this store from demo_access.db onto Postgres
("everything live on Postgres"); that put the public demo's only
database client on the production database (one instance, one db, one
superuser role) -- demo-api, pinned to sqlite, was still targeting
production and only failed for lack of a password (verified live
2026-10-16 12:13Z). The sqlite path is restored so the demo login works
with no path to production at all.

Same non-negotiable as the main auth module: plaintext passwords are
never stored. A profile's plaintext is returned to the caller exactly
once, at creation, and never again.

Password hashing: PBKDF2-HMAC-SHA256, per-profile random salt, 200k
iterations. No new dependency (bcrypt/passlib) -- stdlib hashlib is
plenty for this threat model (keep casual visitors out, not defend
against a targeted attacker), and it keeps this service's dependency
footprint as small as demo_api.py's own.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from common import db_backend

# Signs session tokens issued after a successful password check. Falls back
# to a per-process random secret if not configured -- fine for a single
# long-running service, just means restarting the service invalidates any
# outstanding session tokens (acceptable; the demo login is cheap to redo).
SESSION_SECRET = os.environ.get("DEMO_SESSION_SECRET") or secrets.token_hex(32)
SESSION_TTL_SECONDS = int(os.environ.get("DEMO_SESSION_TTL_SECONDS", str(8 * 3600)))

PBKDF2_ITERATIONS = 200_000

# The demo-state volume (rw, demo-owned, never /var/lib/corporatetraveldc
# -- see corporatetraveldc-demo-api.container's F6 mount narrowing).
DEFAULT_ACCESS_DB = "/var/lib/corporatetraveldc-demo-state/demo_access.db"

# Legacy (pre-2026-09-20) shape, kept byte-for-byte so the file already in
# the volume is read as-is. Booleans are INTEGER 0/1 here, BOOLEAN on
# Postgres; every caller treats them as truthy, so one SQL text serves
# both (sqlite >= 3.23 accepts TRUE/FALSE, >= 3.35 RETURNING).
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    window_days   INTEGER NOT NULL,
    speed         REAL NOT NULL DEFAULT 1.0,
    auto_scale    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);
"""


def access_db_path() -> str:
    """Resolved at call time (not import) so a test or a quadlet can point
    it elsewhere without reloading the module."""
    return os.environ.get("DEMO_ACCESS_DB", DEFAULT_ACCESS_DB)


def store_kind() -> str:
    """'postgres' (the demo instance, DISPATCH_ROLE=demo + a postgres
    backend) or 'sqlite' (everything else). ~~follows DISPATCH_DB_BACKEND
    exactly~~ -- since 2026-10-16 the backend alone never selects Postgres
    here: the only Postgres this module may touch is the demo instance,
    and that is selected by the role."""
    if db_backend.backend() == "postgres" and db_backend.is_demo_role():
        return "postgres"
    return "sqlite"


def _table() -> str:
    return "demo_profiles" if store_kind() == "postgres" else "profiles"


@contextmanager
def _store():
    """One connection per public function, commit on success / rollback on
    exception on both backends. Yields a connection whose execute() takes
    `?` placeholders and whose rows support row["col"] and dict(row)."""
    if store_kind() == "postgres":
        with db_backend.demo_pg_conn() as c:
            yield c
        return
    path = Path(access_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=10)
    try:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(_SQLITE_SCHEMA)
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
    finally:
        c.close()


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS).hex()


def _generate_password(length: int = 16) -> str:
    # Avoids visually ambiguous characters (0/O, 1/l/I) since these are
    # meant to be read aloud or typed by someone on a phone.
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_profile(label: str, window_days: int, speed: float = 1.0,
                    password: str | None = None, auto_scale: bool = False) -> dict:
    """Create a profile. Returns the plaintext password -- the only time
    it will ever be available. Caller (the admin route) is responsible
    for handing it back to the operator and never logging it.

    auto_scale=True means window_days below is just the initial/floor
    value shown back to the caller for reference -- at each login, the
    actual window used gets re-resolved to whatever retention tier the
    archive has currently reached (see demo_api.py's login handler and
    _current_tier()), so the demo keeps growing on its own as the archive
    does, without anyone needing to come back and bump these profiles."""
    plaintext = password or _generate_password()
    salt = secrets.token_bytes(16)
    password_hash = _hash(plaintext, salt)
    created_at = datetime.now(timezone.utc).isoformat()

    with _store() as c:
        row = c.execute(
            f"INSERT INTO {_table()} "
            "(label, password_hash, salt, window_days, speed, auto_scale, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (label, password_hash, salt.hex(), window_days, speed, auto_scale, created_at),
        ).fetchone()
    return {
        "id": row["id"],
        "label": label,
        "password": plaintext,
        "window_days": window_days,
        "speed": speed,
        "auto_scale": auto_scale,
        "created_at": created_at,
    }


def list_profiles(include_inactive: bool = False) -> list[dict]:
    where = "" if include_inactive else "WHERE active"
    with _store() as c:
        rows = c.execute(
            f"SELECT id, label, window_days, speed, auto_scale, created_at, active "
            f"FROM {_table()} {where} ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_profile(profile_id: int) -> bool:
    """True only when an ACTIVE profile was revoked. 2026-10-16: the
    statement used to match on id alone, so revoking an already-revoked
    (or never-active) id reported True on both backends while the admin
    route answers 404 "No such active profile" on False -- `AND active`
    makes the result mean what the route says."""
    with _store() as c:
        cur = c.execute(f"UPDATE {_table()} SET active = FALSE WHERE id = ? AND active", (profile_id,))
        return cur.rowcount > 0


def authenticate(password: str) -> dict | None:
    """Check password against every active profile. Small N (a handful
    of live demo links at most), so a linear scan is fine and keeps this
    simple -- no need for a lookup index on something that isn't a key."""
    with _store() as c:
        rows = c.execute(
            "SELECT id, label, password_hash, salt, window_days, speed, auto_scale "
            f"FROM {_table()} WHERE active"
        ).fetchall()

    for row in rows:
        salt = bytes.fromhex(row["salt"])
        candidate = _hash(password, salt)
        if hmac.compare_digest(candidate, row["password_hash"]):
            return {
                "id": row["id"], "label": row["label"], "window_days": row["window_days"],
                "speed": row["speed"], "auto_scale": bool(row["auto_scale"]),
            }
    return None


def issue_session_token(profile: dict) -> str:
    """HMAC-signed, base64url-encoded JSON blob -- deliberately not a JWT
    library dependency for something this small. Format: <payload>.<sig>,
    both base64url, no padding.

    Caller (demo_api.py's login handler) is responsible for having already
    resolved profile["window_days"] to the live current tier if
    profile["auto_scale"] is set -- this function just signs whatever
    window_days/speed it's handed, it doesn't know about auto-scaling
    itself."""
    import base64

    payload = {
        "id": profile["id"],
        "label": profile["label"],
        "window_days": profile["window_days"],
        "speed": profile["speed"],
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    payload_b = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = hmac.new(SESSION_SECRET.encode() if isinstance(SESSION_SECRET, str) else SESSION_SECRET,
                    payload_b, hashlib.sha256).digest()
    sig_b = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return (payload_b + b"." + sig_b).decode()


def verify_session_token(token: str) -> dict | None:
    """Returns the decoded payload if the token is valid and unexpired,
    else None. Never raises -- callers treat None as 'fall back to the
    open, unauthenticated default window/speed.'"""
    import base64

    try:
        payload_b, sig_b = token.encode().split(b".", 1)
        expected_sig = hmac.new(
            SESSION_SECRET.encode() if isinstance(SESSION_SECRET, str) else SESSION_SECRET,
            payload_b, hashlib.sha256
        ).digest()
        expected_sig_b = base64.urlsafe_b64encode(expected_sig).rstrip(b"=")
        if not hmac.compare_digest(sig_b, expected_sig_b):
            return None
        pad = b"=" * (-len(payload_b) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b + pad))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
