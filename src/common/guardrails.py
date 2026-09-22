"""
common.guardrails -- SR1 (mutation gate) / SR2 (model-tier routing), native
to this platform, no MCP dependency.

2026-08-16: ported from agentic-management-tooling-mcp's agentic/guardrails.py
after that MCP server was demoted to public-facing-demo-only status (see
docs on the dispatch-mcp/agentic-tools MCP disconnect that same night) --
this platform's own code should never need a live MCP connection to get
this discipline, especially not for something as basic as "confirm before
mutating" or "pick the right model tier."

Built now, ahead of need, for when a frontier-model hybrid-offload path
becomes real: today every skill runs 100% local Ollama (phi3:mini, per the
2026-08-15 model-consolidation rebuild). If/when some future skill needs to
escalate a genuinely hard task to a frontier cloud model instead of local
Ollama, model_routing_check() is the decision function it calls -- the
routing logic exists now so that integration is a caller, not a redesign.
Same for mutation_gate(): if a frontier model's own output ever drives an
automated mutating call, this is the gate it goes through first.

Logs to the SAME audit_log table every other admin/mutation action in this
codebase already writes to (db.audit()) -- not a separate log file, so
`GET /api/v1/admin/audit-log` already surfaces SR1/SR2 events for free,
consistent with the board_refresh_token audit trail added the same night.
"""

from __future__ import annotations

import datetime

from common import db

# ---------------------------------------------------------------------------
# SR1 -- Mutation gate
# ---------------------------------------------------------------------------

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def mutation_gate(
    method: str,
    url: str,
    confirmed: bool = False,
    idempotent: bool = False,
) -> dict:
    """SR1: gate for any state-changing call (POST/PUT/PATCH/DELETE).

    GET/HEAD/OPTIONS are always allowed -- reads never need confirmation.
    Anything else requires confirmed=True, set explicitly by the caller to
    signal deliberate intent; there is no default-allow path for mutations.

    Returns {"allowed": bool, "method", "url", "idempotent", "challenge",
    "intercepted_at"} -- challenge is a human-readable string when
    allowed=False, explaining exactly what re-call is needed.
    """
    method = method.upper()
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if method in _SAFE_METHODS:
        return {
            "allowed": True, "method": method, "url": url,
            "idempotent": True, "challenge": None, "intercepted_at": ts,
        }

    if not confirmed:
        db.audit("SR1_INTERCEPT", "guardrail", None, None, {
            "method": method, "url": url, "idempotent": idempotent, "confirmed": False,
        })
        warn = "" if idempotent else " WARNING: not idempotent -- do not retry automatically."
        return {
            "allowed": False, "method": method, "url": url, "idempotent": idempotent,
            "challenge": f"SR1: {method} {url} requires explicit confirmation. "
                         f"Re-call with confirmed=True to proceed.{warn}",
            "intercepted_at": ts,
        }

    db.audit("SR1_ALLOWED", "guardrail", None, None, {
        "method": method, "url": url, "idempotent": idempotent, "confirmed": True,
    })
    return {
        "allowed": True, "method": method, "url": url,
        "idempotent": idempotent, "challenge": None, "intercepted_at": ts,
    }


# ---------------------------------------------------------------------------
# SR2 -- Model routing check
# ---------------------------------------------------------------------------

# Tier 1/2 map to local Ollama (this platform's only tier today). Tier 3/4
# are the hybrid-offload seam -- not wired to any actual frontier API call
# site yet, deliberately: this function only decides WHICH tier a task
# warrants, a future caller decides HOW to reach it.
_TASK_TIERS = {
    "classification": "tier_1",
    "extraction":      "tier_1",
    "summarization":   "tier_2",
    "rewriting":        "tier_2",
    "reasoning":         "tier_3",
    "generation":         "tier_3",
    "analysis":            "tier_3",
}

_TIER_LABELS = {
    "tier_1": "local Ollama, small model (phi3:mini-class) -- classification/extraction",
    "tier_2": "local Ollama, small model (phi3:mini-class) -- summarization/rewriting",
    "tier_3": "hybrid-offload candidate -- local Ollama today, frontier cloud model if/when wired",
    "tier_4": "frontier cloud model required -- token volume or task complexity exceeds local capability",
}


def model_routing_check(
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    task_type: str,
    budget_remaining: float,
    force_tier: str | None = None,
) -> dict:
    """SR2: recommend a model tier before a call, local-vs-frontier-aware.

    budget_remaining is USD, for the day a frontier API is actually metered
    and wired -- meaningless for local Ollama today (no per-call cost), but
    keeping the same shape as the ported original so a future frontier
    integration doesn't need this function's signature to change.

    Returns {"recommended_tier", "tier_description", "estimated_tokens",
    "block", "reasoning"}.
    """
    task_type = task_type.lower().strip()
    estimated_tokens = estimated_input_tokens + estimated_output_tokens

    if budget_remaining <= 0:
        db.audit("SR2_BLOCK", "guardrail", None, None, {
            "task_type": task_type, "budget_remaining": budget_remaining,
        })
        return {
            "recommended_tier": "block",
            "tier_description": "blocked -- budget exhausted",
            "estimated_tokens": estimated_tokens,
            "block": True,
            "reasoning": "budget_remaining is zero or negative. Reset or increase budget before proceeding.",
        }

    if force_tier and force_tier in _TIER_LABELS:
        return {
            "recommended_tier": force_tier,
            "tier_description": _TIER_LABELS[force_tier],
            "estimated_tokens": estimated_tokens,
            "block": False,
            "reasoning": f"Operator forced tier: {force_tier}.",
        }

    base_tier = _TASK_TIERS.get(task_type, "tier_3")

    if estimated_tokens > 50_000 and base_tier == "tier_3":
        recommended = "tier_4"
        reason = f"Task type '{task_type}' at {estimated_tokens:,} tokens warrants frontier model."
    elif estimated_tokens > 100_000:
        recommended = "tier_4"
        reason = f"Token count {estimated_tokens:,} exceeds local-model context comfort zone."
    else:
        recommended = base_tier
        reason = f"Task type '{task_type}' maps to {base_tier}."

    if budget_remaining < 0.05 and recommended in ("tier_3", "tier_4"):
        recommended = "tier_2"
        reason += f" Downgraded to tier_2: budget_remaining ${budget_remaining:.4f} is low -- stay local."

    db.audit("SR2_ROUTE", "guardrail", None, None, {
        "task_type": task_type, "recommended": recommended,
        "estimated_tokens": estimated_tokens, "budget_remaining": budget_remaining,
    })

    return {
        "recommended_tier": recommended,
        "tier_description": _TIER_LABELS.get(recommended, recommended),
        "estimated_tokens": estimated_tokens,
        "block": False,
        "reasoning": reason,
    }
