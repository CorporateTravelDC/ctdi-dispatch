"""
Auth layer — Tier resolution for incoming requests.

Tier 0: Anonymous — no auth required.
Tier 1: CERT — bearer token with tier=cert in DB.
Tier 2: SHARES — Bearer token with tier=shares in DB.
Admin: Bearer token with tier=admin in DB.

2026-08-05: elevation above Tier 0 requires a valid bearer token AND a
non-public-origin request. nginx sets X-CTDI-Public: 1 on the Cloudflare-
tunnel-fronted public vhost (dispatch.example.com); that
marker forces Tier 0 before any token lookup runs, so a tunnel-borne
token -- even a valid admin token -- can never elevate. Tailnet and
on-box loopback requests never carry this marker (nginx only sets it on
the public vhost's location blocks) and resolve the bearer token
normally. Tailnet identity/network origin alone no longer grants any
tier by itself -- a real bearer token is required either way; the
network path only affects whether that token is even considered.

Replaces the previous Tailscale-User-Login-header / X-Forwarded-For-
prefix trust model (_is_tailscale_request, removed), which was
exploitable: nginx's public vhost forwarded X-Forwarded-For via
$proxy_add_x_forwarded_for (append, not replace), so a client-supplied
"X-Forwarded-For: 100.x.x.x" survived as the first value in the merged
header and satisfied the old startswith("100.") check from the open
internet, no token required. Confirmed exploitable against the live
container before this fix; confirmed closed after. See
docs/COMPLIANCE_SECURITY.md §6 for the full network-vs-app-layer model.

Token format: ctdc_<user>_<32-char-base32-or-hex>
Token stored as SHA-256 hash in DB. Plaintext never stored.
"""

import hashlib
import json
import secrets
import string
from enum import Enum
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common import db

bearer_scheme = HTTPBearer(auto_error=False)


class Tier(str, Enum):
    T0 = "tier0"
    T1 = "tier1"      # CERT / Tailscale
    T2 = "tier2"      # SHARES
    ADMIN = "admin"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def resolve_tier(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Tier:
    """
    Resolve the tier for the current request. Used as a FastAPI dependency.
    Does not raise — always returns a Tier. Route handlers enforce minimum tier.
    """
    if request.headers.get("X-CTDI-Public") == "1":
        return Tier.T0

    if credentials and credentials.credentials:
        token_hash = _hash_token(credentials.credentials)
        record = db.lookup_token(token_hash)
        if record:
            tier_str = record["tier"]
            if tier_str == "admin":
                return Tier.ADMIN
            if tier_str == "shares":
                return Tier.T2
            if tier_str == "cert":
                return Tier.T1

    return Tier.T0


def resolve_identity(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict:
    """
    Added 2026-08-02 for the department/multi-operator feed visibility
    model (Add Category, personal vs department vs company-scoped RSS
    feeds -- see shared/rss_catalog.py). Unlike resolve_tier(), which only
    answers "how privileged is this request," this answers "who is this,"
    so the runner service (which never touches the shared DB directly --
    see README's "runner is the only container that does not touch the
    shared DB" note) can ask web/main.py's /api/v1/whoami-token endpoint
    and get back a stable identity to scope personal/department feeds by,
    without runner needing its own DB connection or duplicating the token
    hashing/lookup logic here.

    Returns {"tier": str, "user_label": str|None, "department": str|None,
    "token_prefix": str|None} -- anonymous/invalid tokens resolve to
    tier="tier0" with the rest None, never raises.

    2026-08-05: same X-CTDI-Public guard as resolve_tier() -- a tunnel-
    borne request never resolves to an identity above anonymous, token
    or not. See resolve_tier()'s docstring for the full rationale.
    """
    if request.headers.get("X-CTDI-Public") == "1":
        return {"tier": "tier0", "user_label": None, "department": None, "token_prefix": None}
    if credentials and credentials.credentials:
        token_hash = _hash_token(credentials.credentials)
        record = db.lookup_token(token_hash)
        if record:
            return {
                "tier": record["tier"],
                "user_label": record["user_label"],
                "department": record.get("department"),
                "token_prefix": record["token_prefix"],
            }
    return {"tier": "tier0", "user_label": None, "department": None, "token_prefix": None}


def require_tier(minimum: Tier):
    """Dependency factory: raises 403 if resolved tier is below minimum."""
    order = [Tier.T0, Tier.T1, Tier.T2, Tier.ADMIN]

    def _dep(tier: Tier = Depends(resolve_tier)) -> Tier:
        if order.index(tier) < order.index(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This endpoint requires tier {minimum.value}",
            )
        return tier

    return _dep


_AUDIT_REDACT_KEY_TERMS = (
    "password", "passwd", "secret", "api_key", "apikey", "token",
    "credential", "authorization", "note", "command", "cmd",
)


def _redact_audit_url(value: str) -> str:
    """Strip userinfo and redact sensitive query params from a URL before
    it goes into audit_log -- feed_urls commonly carry an embedded API
    key (`https://host/feed?api_key=...` or `https://user:pass@host/`)."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    query = [
        (k, "<redacted>" if any(t in k.lower() for t in _AUDIT_REDACT_KEY_TERMS) else v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query), ""))


def _redact_audit_value(key: str, value):
    if isinstance(value, dict):
        return {k: _redact_audit_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_audit_value(key, v) for v in value]
    if isinstance(value, str):
        if any(t in key.lower() for t in _AUDIT_REDACT_KEY_TERMS):
            return f"<redacted:{len(value)} chars>"
        if value.startswith("http://") or value.startswith("https://"):
            return _redact_audit_url(value)
    return value


def _redact_audit_detail(detail):
    """2026-08-25 fix (Opus blind review C-9, redaction half): require_admin
    used to store the raw request body verbatim in audit_log.detail for 90
    days -- vault note text, sudo command strings, and feed URLs (which
    commonly embed an API key) all went in unredacted. This walks the
    detail dict and blanks values under a sensitive-looking key name, and
    strips userinfo/sensitive query params from any URL-shaped value, so
    the audit row still proves an action happened without persisting the
    sensitive content itself."""
    if not isinstance(detail, dict):
        return detail
    return {k: _redact_audit_value(k, v) for k, v in detail.items()}


def require_admin(action: str):
    """Dependency factory: requires Admin tier AND writes an audit row.

    2026-08-19: previously a plain dependency (`Depends(require_admin)`)
    that checked tier and nothing else -- none of the 23 admin/osint-write
    endpoints wrote to audit_log, so /admin/audit showed zero admin
    history no matter what was actually done through the API (see
    docs/COMPLIANCE_SECURITY.md's "What is not audited" section for the
    finding that motivated this).

    Now a factory: every call site passes its own hand-picked action name
    (`Depends(require_admin("admin.vip.add"))`) so authorization and audit
    logging can never drift apart -- there is no route that checks tier
    without also being logged, because the same dependency does both.
    Hand-named rather than auto-derived from method+path so
    /admin/audit reads like a real action log, not a raw route dump.

    Captures the actor (token_prefix -- never the raw token) and, for
    POST/PUT/PATCH/DELETE, the request body as `detail` (falls back to
    query params for bodyless requests). `request.body()` is read here
    via the dependency; Starlette caches the bytes on `request._body`,
    so the route handler's own body-parsing (Pydantic models, etc.) reads
    the cached copy rather than a second, empty stream -- this is safe by
    construction, not by convention.

    Retention: audit_log itself had no prune job (unbounded growth) --
    poller/skills/audit_log_prune.py (added the same day) now deletes rows
    older than 90 days daily.

    2026-08-25 fix (Opus blind review C-9): a failed authorization used to
    raise its 403 before ever reaching the db.audit() call below, so admin-
    surface probing left zero trace (confirmed live: 0 denied rows in
    audit_log despite real probing). Denied attempts are now audited too,
    before the exception is raised. The stored `detail` also now goes
    through `_redact_audit_detail()` -- see that function's docstring for
    why (raw vault notes/sudo commands/feed-embedded API keys were being
    persisted unredacted for 90 days).
    """
    async def _dep(
        request: Request,
        tier: Tier = Depends(resolve_tier),
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    ) -> Tier:
        token_prefix = None
        if credentials and credentials.credentials:
            record = db.lookup_token(_hash_token(credentials.credentials))
            if record:
                token_prefix = record["token_prefix"]

        if tier != Tier.ADMIN:
            db.audit(
                action, tier.value, token_prefix,
                request.client.host if request.client else None,
                {"result": "denied"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin tier required",
            )

        detail = None
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                body = await request.body()
                if body:
                    detail = json.loads(body)
            except Exception:
                detail = None
        if detail is None and request.query_params:
            detail = dict(request.query_params)
        detail = _redact_audit_detail(detail)

        db.audit(
            action, tier.value, token_prefix,
            request.client.host if request.client else None, detail,
        )
        return tier

    return _dep


def _token_prefix(user: str) -> str:
    return f"ctdc_{user}_"


def generate_token(user: str, tier: str, device_label: str | None,
                   expires_at: float | None = None, department: str | None = None) -> str:
    """
    Generate a new token, store its hash in the DB, return the plaintext.
    Plaintext is shown once and never stored.

    department -- added 2026-08-02, optional. Tokens with no department set
    are treated as personal-only for department-scoped feed visibility
    (see shared/rss_catalog.py); set one to make this operator part of a
    department for department-scoped categories/feeds.
    """
    valid_tiers = {"cert", "shares", "admin"}
    if tier not in valid_tiers:
        raise ValueError(f"Invalid tier {tier!r}; must be one of {valid_tiers}")

    # 32 chars of URL-safe random base32.
    alphabet = string.ascii_uppercase + string.digits
    raw_suffix = "".join(secrets.choice(alphabet) for _ in range(32))
    token_plaintext = f"ctdc_{user}_{raw_suffix}"
    token_hash = _hash_token(token_plaintext)
    token_prefix = _token_prefix(user)

    db.insert_token(
        token_hash=token_hash,
        token_prefix=token_prefix,
        user_label=user,
        tier=tier,
        device_label=device_label,
        expires_at=expires_at,
        department=department,
    )
    return token_plaintext


def revoke_by_prefix(token_prefix: str) -> int:
    """Revoke all tokens with this prefix. Returns count revoked."""
    return db.revoke_token(token_prefix)
