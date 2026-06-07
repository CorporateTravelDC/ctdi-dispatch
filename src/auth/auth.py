"""
Auth layer — Tier resolution for incoming requests.

Tier 0: Anonymous — no auth required.
Tier 1: CERT/Tailscale — Tailscale forwards Tailscale-User-Login header on tailnet.
Tier 2: SHARES — Bearer token with tier=shares in DB.
Admin: Bearer token with tier=admin in DB.

Token format: ctdc_<user>_<32-char-base32-or-hex>
Token stored as SHA-256 hash in DB. Plaintext never stored.
"""

import hashlib
import secrets
import string
from enum import Enum
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common import config, db

bearer_scheme = HTTPBearer(auto_error=False)


class Tier(str, Enum):
    T0 = "tier0"
    T1 = "tier1"      # CERT / Tailscale
    T2 = "tier2"      # SHARES
    ADMIN = "admin"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_tailscale_request(request: Request) -> bool:
    """
    Tailscale sets Tailscale-User-Login on authenticated tailnet requests.
    nginx must forward this header; strip it on public Cloudflare Tunnel traffic.
    """
    ts_header = request.headers.get("Tailscale-User-Login")
    ts_suffix = config.tailscale_domain_suffix()
    if ts_header and ts_suffix and ts_header.endswith(ts_suffix):
        return True
    # Also accept X-Forwarded-For from Tailscale CGNAT range <TAILSCALE_IP>/10.
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for.startswith("100."):
        return True
    return False


def resolve_tier(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Tier:
    """
    Resolve the tier for the current request. Used as a FastAPI dependency.
    Does not raise — always returns a Tier. Route handlers enforce minimum tier.
    """
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

    if _is_tailscale_request(request):
        return Tier.T1

    return Tier.T0


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


def require_admin(tier: Tier = Depends(resolve_tier)) -> Tier:
    if tier != Tier.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin tier required",
        )
    return tier


def _token_prefix(user: str) -> str:
    return f"ctdc_{user}_"


def generate_token(user: str, tier: str, device_label: str | None,
                   expires_at: float | None = None) -> str:
    """
    Generate a new token, store its hash in the DB, return the plaintext.
    Plaintext is shown once and never stored.
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
    )
    return token_plaintext


def revoke_by_prefix(token_prefix: str) -> int:
    """Revoke all tokens with this prefix. Returns count revoked."""
    return db.revoke_token(token_prefix)
