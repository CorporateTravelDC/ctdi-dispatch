"""
route-impact — SR-1 + SR-2 compliant.
Fallback: structured raw TFR/NAS impact text written to hot_alerts if API unavailable.
"""
import argparse, logging, sys, time
import anthropic
import requests
from common import config, db
from common.push_dedup import PushDedup, content_hash
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)
SKILL_NAME = "route-impact"
MODEL = "claude-sonnet-4-6"

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


def _fallback_narrative(inputs: dict) -> str:
    ts = time.strftime("%H:%MZ", time.gmtime())
    vip = [t for t in inputs["tfrs"] if t["vip"]]
    lines = [f"[FALLBACK {ts}] Route impact — raw data:"]
    if vip:
        lines.append(f"VIP TFRs: {', '.join(t['id'] for t in vip)} — expect corridor activity.")
    elif inputs["tfrs"]:
        lines.append(f"Active TFRs: {len(inputs['tfrs'])} — no VIP flags.")
    else:
        lines.append("No active TFRs.")
    if inputs["nas"]:
        lines.append("NAS programs: " + ", ".join(f"{p['type']} {p['facility']}" for p in inputs["nas"]))
    return " ".join(lines)


def main(force: bool = False) -> None:
    inputs = build_inputs()
    gate_result = hash_gate(SKILL_NAME, inputs, force=force)
    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping", SKILL_NAME)
        sys.exit(0)

    input_tokens = output_tokens = cache_read_tokens = cache_write_tokens = 0
    status = "error"

    try:
        narrative = None
        try:
            client = anthropic.Anthropic(api_key=config.anthropic_api_key())
            response = client.messages.create(
                model=MODEL, max_tokens=500,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": build_user_message(inputs)}],
            )
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cache_read_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            cache_write_tokens = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            status = "ok"
            narrative = response.content[0].text
        except anthropic.APIError as e:
            log.warning("%s: API unavailable (%s) — using fallback", SKILL_NAME, e)
            status = "fallback"
            narrative = _fallback_narrative(inputs)

        vip_ids = [t["id"] for t in inputs["tfrs"] if t["vip"]]
        db.insert_route_narrative(narrative, [t["id"] for t in inputs["tfrs"]], vip_ids)
        log.info("%s: %s — %d+%d tokens", SKILL_NAME, status, input_tokens, output_tokens)

        if status in ("ok", "fallback") and narrative:
            priority = 5 if vip_ids else 4
            title = f"Route Impact {'— VIP ACTIVE' if vip_ids else 'Update'}"
            # VIP routes always hot-push (bypass 1hr dedup).
            # Non-VIP route updates: 1hr content dedup -- suppress if same
            # TFR/NAS set produced the same narrative within the last hour.
            h = content_hash(
                "|".join(t["id"] for t in sorted(inputs["tfrs"], key=lambda x: x["id"]))
                + "|" + str(bool(vip_ids))
            )
            hot = bool(vip_ids)
            if _route_dedup.should_push("route-impact", h, hot=hot):
                try:
                    base = config.ntfy_url()
                    token = config.ntfy_token()
                    hdrs = {"Authorization": f"Bearer {token}",
                            "Priority": str(priority), "Title": title}
                    requests.post(f"{base}/hot-alerts",
                                  data=narrative.encode(), headers=hdrs, timeout=10)
                    _route_dedup.record("route-impact", h)
                except Exception as e:
                    log.warning("%s: hot-alerts push failed: %s", SKILL_NAME, e)
            else:
                log.debug("%s: hot-alerts suppressed (dedup, same TFR set <1h)", SKILL_NAME)

    finally:
        log_usage(SKILL_NAME, MODEL, input_tokens, output_tokens, status, gate_result,
                  cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
