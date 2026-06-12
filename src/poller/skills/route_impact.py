"""
route-impact — SR-1 + SR-2 compliant.
Fallback: structured raw TFR/NAS impact text written to hot_alerts if Ollama unavailable.
"""
import os
import argparse, logging, sys, time
import httpx
from common import db, ntfy_push as _ntfy
from common.push_dedup import PushDedup, content_hash
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)
SKILL_NAME = "route-impact"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_OSINT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "csexec-osint:latest")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"
# Route prompt is ~300-400 tokens; mistral-nemo Pi 5 CPU ~120s sufficient
OLLAMA_TIMEOUT    = 900  # stopgap

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
    """Call Ollama for VIP route impact narrative only. Focused prompt, Pi CPU feasible.
    Returns narrative text or None on any error (caller falls back to deterministic).
    """
    if not OLLAMA_BASE_URL:
        return None
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model":  OLLAMA_MODEL,
                "system": SYSTEM_PROMPT,
                "prompt": _vip_user_message(inputs),
                "stream": False,
                "options": {"num_predict": 200, "temperature": 0.2},
            },
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or None
    except Exception as exc:
        log.warning("%s: Ollama call failed (%s) — falling back to deterministic", SKILL_NAME, exc)
        return None


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
                narrative = _deterministic_summary(inputs)
                status = "fallback"
                log.warning("%s: Ollama unavailable for VIP route — using deterministic fallback", SKILL_NAME)
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
            if _route_dedup.should_push("route-impact", h, hot=True):
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
