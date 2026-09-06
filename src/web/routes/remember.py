"""
web.routes.remember -- REST wrapper around second_brain.remember_text(),
added 2026-07-23 so manual vault capture ("remember this") can be driven
from a Cowork skill instead of only from a shell on the Pi. Same scrub
gate, same write path, same index call as the CLI -- see
second_brain/remember.py's module docstring.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.auth import Tier, require_admin
from second_brain.remember import remember_text
from second_brain.scrub_gate import ScrubGateBlocked

router = APIRouter()


class RememberRequest(BaseModel):
    text: str
    tags: str = ""
    # 2026-08-24: defaults to "agent" -- this route's own docstring says
    # it exists so a Cowork skill (automation) can drive vault capture,
    # not primarily for a human typing at this specific endpoint. A real
    # human caller hitting this directly with their own admin token can
    # override to "human" explicitly; the point is the caller states it,
    # nothing here infers it from who holds the bearer token.
    author_kind: str = "agent"
    author_name: str | None = None


@router.post("/api/v1/remember", status_code=201)
async def remember(
    body: RememberRequest,
    tier: Tier = Depends(require_admin("vault.remember")),
) -> dict:
    """Capture a manual note into the vault (01-Sources/manual/). Admin
    required -- same tier as other write endpoints on this platform."""
    try:
        rel_path = remember_text(body.text, tags=body.tags,
                                 author_kind=body.author_kind,
                                 author_name=body.author_name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ScrubGateBlocked as e:
        raise HTTPException(422, f"blocked by CUI/PII scrub gate: {e}")
    return {"status": "ok", "path": rel_path}
