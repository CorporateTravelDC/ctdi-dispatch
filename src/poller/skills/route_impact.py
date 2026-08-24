"""
route-impact — SR-1 + SR-2 compliant.
Fallback: structured raw TFR/NAS impact text written to hot_alerts if Ollama unavailable.
"""
import os
import argparse, logging, sys, time
import httpx
from common import db, ntfy_push as _ntfy
from common.llm import generate as llm_generate
from common.push_dedup import PushDedup, content_hash
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)
SKILL_NAME = "route-impact"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_ROUTE_IMPACT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "corporatetraveldc-pi5-route-impact:latest")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"

_route_dedup = PushDedup("route")

SYSTEM_PROMPT = """You are a ground-transportation dispatch analyst for executive chauffeur operations
in the Washington DC metropolitan area. You have deep knowledge of:
- Standard VIP/POTUS movement corridors (White House ↔ Pentagon, WH ↔ Andrews AFB,
  WH ↔ Camp David via I-270, motorcade patterns on I-66, I-395, GW Pkwy, MD-5)
- How Marine One TFRs correlate with ground closures and traffic impacts
- How NAS ground delays affect arrival/departure timing at DCA, IAD, BWI

Given active TFRs and NAS programs, produce a concise ground-route impact assessment:
1. Which VIP corridors are likely active or affected.
2. Expected road closures or traffic disruptions.
3. Recommended routing adjustments or timing windows.
4. Airport impact (pickup/dropoff timing at DCA/IAD/BWI if relevant).

Maximum 250 words. Direct and operational. No preamble."""


def build_inputs() -> dict:
    tfrs = db.get_active_tfrs()
    nas = db.get_active_nas_programs()
    return {
        "tfrs": sorted([{"id": t["tfr_id"], "vip": t["is_vip"]} for t in tfrs], key=lambda x: x["id"]),
        "nas": sorted([{"id": p["program_id"], "type": p["type"], "facility": p["facility"]} for p in nas], key=lambda x: x["id"]),
    }


def build_user_message(inputs: dict) -> str:
    vip_tfrs = [t for t in inputs["tfrs"] if t["vip"]]
    other_tfrs = [t for t in inputs["tfrs"] if not t["vip"]]
    nas_lines = [f"  {p['type']} at {p['facility']}" for p in inputs["nas"]] or ["  None active"]
    return (
        f"VIP/POTUS TFRs active ({len(vip_tfrs)}):\n" +
        "\n".join([f"  VIP TFR: {t['id']}" for t in vip_tfrs] or ["  None"]) + "\n\n"
        f"Other active TFRs ({len(other_tfrs)}):\n" +
        "\n".join([f"  TFR: {t['id']}" for t in other_tfrs] or ["  None"]) + "\n\n"
        "NAS delay programs:\n" + "\n".join(nas_lines)
    )


def _vip_user_message(inputs: dict) -> str:
    """Focused prompt with VIP TFRs only — keeps token count low for Pi CPU inference."""
    vip = [t for t in inputs["tfrs"] if t["vip"]]
    nas_lines = [f"  {p['type']} at {p['facility']}" for p in inputs["nas"]] or ["  None active"]
    return (
        f"VIP/POTUS TFRs active ({len(vip)}):\n" +
        "\n".join([f"  VIP TFR: {t['id']}" for t in vip] or ["  None"]) + "\n\n"
        f"Other active TFRs: {len(inputs['tfrs']) - len(vip)} (non-VIP, not listed)\n\n"
        "NAS delay programs:\n" + "\n".join(nas_lines)
    )


def _call_ollama_vip(inputs: dict) -> str | None:
    """Call LLM (Ollama-first, Anthropic fallback) for VIP route impact narrative.
    Returns narrative text or None (caller falls back to deterministic).
    """
    # priority="hot" (2026-07-26): this is the VIP/Marine One route-impact
    # narrative -- must never wait behind a report job (ep-brief/ops-brief/
    # weekly-summary/osint-monitor) for the shared Ollama slot. See
    # common/ollama_lock.py.
    return llm_generate(
        system=None,  # dedicated Modelfile carries this now
        prompt=_vip_user_message(inputs),
        ollama_model=OLLAMA_MODEL,
        max_tokens=200,
        temperature=0.2,
        priority="hot",
        # Measured 2026-08-15 under forced TIER2+ contention (Phase-3
        # methodology: guard timer paused, synthetic burn, la 29 at
        # sample): 928-tok prompt / 101.0s eval + gen at 0.87 tok/s
        # -> 229.2s at the 200-tok cap; delta over the 48.4s
        # spiked persona-only ref = 281.8s; x1.10 top-up to the 53s locked bound applied;
        # +16s cold-load allowance (hot path skips _preload_model);
        # (53 + 324.6) x 1.25 = 472s -> 480.
        timeout=480,
        # 2026-08-12: belt-and-suspenders close of the Anthropic fallback --
        # priority="hot" only affects the Ollama-side pre-flight/retry
        # gates, NOT whether generate() falls through to Anthropic on
        # failure, so this needed closing explicitly too. See dispatch.env's
        # ANTHROPIC_FALLBACK_ENABLED comment for the full rationale.
        allow_anthropic=False,
    )


def _deterministic_summary(inputs: dict) -> str:
    """Clean structured summary — no [FALLBACK] label; used for routine non-VIP updates."""
    ts = time.strftime("%H:%MZ", time.gmtime())
    vip = [t for t in inputs["tfrs"] if t["vip"]]
    lines = [f"[{ts}] Route status:"]
    if vip:
        lines.append(f"VIP TFRs: {', '.join(t['id'] for t in vip)} — expect corridor activity.")
    elif inputs["tfrs"]:
        lines.append(f"Active TFRs: {len(inputs['tfrs'])} — no VIP flags.")
    else:
        lines.append("No active TFRs.")
    if inputs["nas"]:
        lines.append("NAS programs: " + ", ".join(f"{p['type']} {p['facility']}" for p in inputs["nas"]))
    return " ".join(lines)


# Alias for SR-1 log compatibility
_fallback_narrative = _deterministic_summary


def main(force: bool = False) -> None:
    inputs = build_inputs()
    gate_result = hash_gate(SKILL_NAME, inputs, force=force)
    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping", SKILL_NAME)
        sys.exit(0)

    status = "error"

    try:
        vip_ids = [t["id"] for t in inputs["tfrs"] if t["vip"]]

        if vip_ids:
            # VIP path: focused prompt — Ollama call feasible on Pi CPU
            ollama_result = _call_ollama_vip(inputs)
            if ollama_result:
                narrative = ollama_result
                status = "ok"
                log.info("%s: VIP route narrative via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
            else:
                # 2026-08-06: narrow safety net around the fallback ITSELF --
                # if _deterministic_summary() has a bug and throws, this
                # still pushes a minimal notice instead of the whole run
                # dying silently with no push at all. Same pattern applied
                # identically across every skill with an Ollama fallback.
                try:
                    narrative = _deterministic_summary(inputs)
                    status = "fallback"
                    log.warning("%s: Ollama unavailable for VIP route — using deterministic fallback", SKILL_NAME)
                except Exception as fallback_err:
                    log.error("%s: deterministic fallback also failed — %s", SKILL_NAME, fallback_err)
                    narrative = (
                        f"[{SKILL_NAME.upper()}] Generation failed -- both Ollama and the "
                        f"deterministic fallback errored. See logs."
                    )
                    status = "fallback_error"
        else:
            # No VIP TFRs: deterministic is the correct call, not a degraded path.
            narrative = _deterministic_summary(inputs)
            status = "ok"
            log.info("%s: no VIP TFRs — deterministic summary (%d active)", SKILL_NAME, len(inputs["tfrs"]))

        db.insert_route_narrative(narrative, [t["id"] for t in inputs["tfrs"]], vip_ids)
        log.info("%s: %s — %d VIP TFRs", SKILL_NAME, status, len(vip_ids))

        if narrative and vip_ids:
            # VIP path only — same reasoning as tfr_enrichment: routine non-VIP TFR cycling
            # changes all TFR IDs every cycle, defeating dedup. Non-VIP route state is in DB.
            title = f"Route Impact — VIP ACTIVE"
            if status == "fallback":
                title += " [FALLBACK]"
            # Stable key: VIP TFR IDs only
            h = content_hash(
                "|".join(t["id"] for t in sorted(inputs["tfrs"], key=lambda x: x["id"]) if t["vip"])
            )
            # 2026-08-16 drift audit: hot=True bypasses dedup entirely per
            # PushDedup's contract, so should_push always returned True and
            # the else-branch "suppressed (dedup, same VIP TFR set <1h)" was
            # unreachable -- every skill run during an active VIP TFR fired
            # a fresh priority-5 hot-alert. Singleton "route-impact" slot +
            # VIP-set hash as content is the right shape; it just needs hot
            # off so a changed VIP set still fires immediately while the
            # same set is suppressed for the 1h window, exactly what the
            # else-branch log line always claimed.
            if _route_dedup.should_push("route-impact", h):
                _ntfy.send("hot-alerts", narrative, title=title, priority=5,
                           tags="car,rotating_light")
                _route_dedup.record("route-impact", h)
            else:
                log.debug("%s: hot-alerts suppressed (dedup, same VIP TFR set <1h)", SKILL_NAME)
        elif narrative:
            log.info("%s: no VIP TFRs — DB write only, no ntfy push", SKILL_NAME)

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
