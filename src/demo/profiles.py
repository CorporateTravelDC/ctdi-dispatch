"""
demo.profiles — password-gated access profiles for the public demo.

Deliberately decoupled from the main app's src/auth/auth.py: that module
is tightly bound to common.db (the live operational database), and
demo_api.py's whole design point is to never touch live state (see its
module docstring). This gets its own tiny SQLite file and its own admin
gate, proportionate to what it actually protects -- a handful of shared
passwords keeping casual visitors out of a public playback loop, not a
multi-tenant credential system.

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
from datetime import datetime, timezone

DB = os.environ.get("DEMO_ACCESS_DB", "/var/lib/corporatetraveldc/demo_access.db")

# Signs session tokens issued after a successful password check. Falls back
# to a per-process random secret if not configured -- fine for a single
# long-running service, just means restarting the service invalidates any
# outstanding session tokens (acceptable; the demo login is cheap to redo).
SESSION_SECRET = os.environ.get("DEMO_SESSION_SECRET") or secrets.token_hex(32)
SESSION_TTL_SECONDS = int(os.environ.get("DEMO_SESSION_TTL_SECONDS", str(8 * 3600)))

PBKDF2_ITERATIONS = 200_000


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.execute("""
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
        )
    """)
    # Additive migration for DBs created before auto_scale existed.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()]
    if "auto_scale" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN auto_scale INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    return conn


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

    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO profiles (label, password_hash, salt, window_days, speed, auto_scale, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (label, password_hash, salt.hex(), window_days, speed, int(auto_scale), created_at),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "label": label,
            "password": plaintext,
            "window_days": window_days,
            "speed": speed,
            "auto_scale": auto_scale,
            "created_at": created_at,
        }
    finally:
        conn.close()


def list_profiles(include_inactive: bool = False) -> list[dict]:
    conn = _conn()
    try:
        where = "" if include_inactive else "WHERE active = 1"
        rows = conn.execute(
            f"SELECT id, label, window_days, speed, auto_scale, created_at, active FROM profiles {where} "
            "ORDER BY created_at DESC"
        ).fetchall()
        cols = ["id", "label", "window_days", "speed", "auto_scale", "created_at", "active"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def revoke_profile(profile_id: int) -> bool:
    conn = _conn()
    try:
        cur = conn.execute("UPDATE profiles SET active = 0 WHERE id = ?", (profile_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def authenticate(password: str) -> dict | None:
    """Check password against every active profile. Small N (a handful
    of live demo links at most), so a linear scan is fine and keeps this
    simple -- no need for a lookup index on something that isn't a key."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, label, password_hash, salt, window_days, speed, auto_scale "
            "FROM profiles WHERE active = 1"
        ).fetchall()
    finally:
        conn.close()

    for pid, label, password_hash, salt_hex, window_days, speed, auto_scale in rows:
        salt = bytes.fromhex(salt_hex)
        candidate = _hash(password, salt)
        if hmac.compare_digest(candidate, password_hash):
            return {
                "id": pid, "label": label, "window_days": window_days,
                "speed": speed, "auto_scale": bool(auto_scale),
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
