"""
demo.profiles — password-gated access profiles for the public demo.

Deliberately decoupled from the main app's src/auth/auth.py: that module
is tightly bound to common.db (the live operational database), and
demo_api.py's whole design point is to never touch live state (see its
module docstring). This still gets its own table (demo_profiles,
pg_schema/0057_demo.sql) and its own admin gate, proportionate to what it
actually protects -- a handful of shared passwords keeping casual
visitors out of a public playback loop, not a multi-tenant credential
system.

2026-09-20 Postgres cutover (operator directive -- see src/demo/db.py's
module docstring for the full rationale): this used to be its own
standalone SQLite file (/var/lib/corporatetraveldc-demo-state/
demo_access.db, opened directly via sqlite3.connect() on every call).
Now goes through common.db_backend.pg_conn() directly (this module is
small enough -- 7 live rows, no polling loop -- that a dedicated
src/demo/db.py-style wrapper would be pure ceremony; see that module's
own docstring for why demo_snapshots got one and this didn't: this
module's connection use is already exactly one call per public function,
no shared/looping state to centralize).

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
import string
import time
from datetime import datetime, timezone

from common import db_backend

# Signs session tokens issued after a successful password check. Falls back
# to a per-process random secret if not configured -- fine for a single
# long-running service, just means restarting the service invalidates any
# outstanding session tokens (acceptable; the demo login is cheap to redo).
SESSION_SECRET = os.environ.get("DEMO_SESSION_SECRET") or secrets.token_hex(32)
SESSION_TTL_SECONDS = int(os.environ.get("DEMO_SESSION_TTL_SECONDS", str(8 * 3600)))

PBKDF2_ITERATIONS = 200_000


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

    with db_backend.pg_conn() as c:
        row = c.execute(
            "INSERT INTO demo_profiles "
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
    with db_backend.pg_conn() as c:
        rows = c.execute(
            f"SELECT id, label, window_days, speed, auto_scale, created_at, active "
            f"FROM demo_profiles {where} ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_profile(profile_id: int) -> bool:
    with db_backend.pg_conn() as c:
        cur = c.execute("UPDATE demo_profiles SET active = FALSE WHERE id = ?", (profile_id,))
        return cur.rowcount > 0


def authenticate(password: str) -> dict | None:
    """Check password against every active profile. Small N (a handful
    of live demo links at most), so a linear scan is fine and keeps this
    simple -- no need for a lookup index on something that isn't a key."""
    with db_backend.pg_conn() as c:
        rows = c.execute(
            "SELECT id, label, password_hash, salt, window_days, speed, auto_scale "
            "FROM demo_profiles WHERE active"
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
