"""
route-impact — SR-1 + SR-2 compliant.
Fallback: structured raw TFR/NAS impact text written to hot_alerts if Ollama unavailable.
"""
import json
import os
import argparse, logging, sys, time
import httpx
from common import db, ntfy_push as _ntfy
from common.input_gate import InputGate, dc_nas_key, is_dc_vip_tfr, make_key
from common.llm import generate as llm_generate
from common.push_dedup import PushDedup, content_hash
from common.sr1_log import log_usage
from common.sr2_gate import check_gate, commit_gate

log = logging.getLogger(__name__)
SKILL_NAME = "route-impact"
LLAMA_BASE_URL   = os.getenv("LLAMA_BASE_URL") or os.getenv("OLLAMA_BASE_URL", "")  # 2026-10-05 rename; OLLAMA_ = deprecated alias
OLLAMA_MODEL      = (os.getenv("OLLAMA_ROUTE_IMPACT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "corporatetraveldc-pi5-route-impact:latest")
MODEL             = OLLAMA_MODEL if LLAMA_BASE_URL else "deterministic"

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



def _tfr_text(t: dict) -> dict:
    try:
        d = json.loads(t.get("raw_json") or "{}") or {}
    except (TypeError, ValueError):
        d = {}
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    def fmt(e):
        if not e:
            return "?"
        u = datetime.fromtimestamp(float(e), timezone.utc)
        return f"{u:%Y-%m-%d %H:%M}Z / {u.astimezone(ZoneInfo('America/New_York')):%H:%M} ET"
    window = f"{fmt(t.get('effective_start'))} to {fmt(t.get('effective_end'))}"
    areas = d.get("area_windows_utc") or []      # 2026-10-14: per-area windows from the FAA detail record
    if len(areas) > 1:
        window += "; area windows " + ", ".join(f"{fmt(a)} to {fmt(b)}" for a, b in areas)
    return {"text": " ".join(str(d.get("description") or "").split()), "window": window}


def _dc_vip(t: dict) -> bool:
    """2026-10-14 (operator): the route narrative is for DC-area VIP TFRs only.
    2026-10-20: the predicate lives in common.input_gate (shared with ep-advance's key)."""
    return is_dc_vip_tfr(t)


def model_key(inputs: dict) -> str:
    """2026-10-20: what the VIP route NARRATIVE depends on -- the DC VIP TFRs'
    id, FAA text and window, and the set of DC-area NAS program types. The SR-2
    gate below still keys every TFR/NAS row (the deterministic DB summary and
    its counts refresh every cycle); this key only decides whether the MODEL
    runs. Before, national churn (~33 of 127 NAS programs per 5 min) made every
    run a model call and kept llama's second slot permanently busy."""
    return make_key({
        "vip": [[t["id"], t.get("text", ""), t.get("window", "")] for t in inputs["tfrs"] if t["vip"]],
        "nas": dc_nas_key(inputs["nas"]),
    })

def build_inputs() -> dict:
    tfrs = db.get_active_tfrs()
    nas = db.get_active_nas_programs()
    return {
        # 2026-10-14 (operator): a DC-area VIP TFR carries its own FAA text and window, so the
        # route narrative is about where it actually is -- the prompt used to get IDs only.
        "tfrs": sorted([{"id": t["tfr_id"], "vip": _dc_vip(t), **(_tfr_text(t) if _dc_vip(t) else {})}
                        for t in tfrs], key=lambda x: x["id"]),
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
    """Focused prompt with VIP TFRs only — keeps token count low for Pi CPU inference.
    2026-10-20: NAS lines are the DC-area program types only (was every national
    program -- ~127 lines of prompt the DC narrative never used)."""
    vip = [t for t in inputs["tfrs"] if t["vip"]]
    nas_lines = [f"  {t} at {f}" for t, f in dc_nas_key(inputs["nas"])] or ["  None active at DCA/IAD/BWI/ZDC/PCT"]
    return (
        f"VIP/POTUS TFRs active ({len(vip)}):\n" +
        "\n".join([f"  VIP TFR {t['id']}: {t.get('text') or 'no FAA text'} ({t.get('window') or 'window unknown'})"
                   for t in vip] or ["  None"]) + "\n\n"
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
        # 2026-10-20 (abandoned-work fix): kept at 480 rather than raised.
        # One in-flight call per skill is already enforced (poller SkillLoop's
        # in-flight guard never re-triggers a running skill), so the cancels
        # llama logged were a 928+-token prompt (every national NAS program)
        # outrunning 480 s while the second slot was shared. The prompt now
        # carries DC-area NAS lines only and model_key() skips unchanged
        # cycles, so real duration falls back inside the measured bound; a
        # longer timeout would only delay the VIP push behind a slow call.
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
    gate_result, _gate_hash = check_gate(SKILL_NAME, inputs, force=force)
    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping", SKILL_NAME)
        sys.exit(0)

    status = "error"

    try:
        vip_ids = [t["id"] for t in inputs["tfrs"] if t["vip"]]

        if vip_ids:
            # VIP path: focused prompt — Ollama call feasible on Pi CPU.
            # 2026-10-20: skip the model when the DC inputs are unchanged since
            # the last SUCCESSFUL narrative (InputGate, 6 h reuse cap; --force bypasses).
            mkey = model_key(inputs)
            mgate = InputGate(SKILL_NAME)
            reused = None if force else mgate.reusable(mkey)
            ollama_result = None if reused else _call_ollama_vip(inputs)
            if reused:
                narrative = reused["narrative"]
                status = "ok"
                log.info("%s: VIP route narrative reused (DC inputs unchanged -- model call skipped)",
                         SKILL_NAME)
            elif ollama_result:
                narrative = ollama_result
                status = "ok"
                mgate.record(mkey, narrative, "")
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
            # The route hot-alert (restored 2026-10-14, operator: "I want the hot alert when
            # there's a TFR that affects the route"). DC-area VIP TFRs only.
            title = f"Route Impact — VIP ACTIVE"
            if status == "fallback":
                title += " [FALLBACK]"
            # Stable key: the DC VIP TFRs' own ids + FAA text -- never the narrative or a stamp
            h = content_hash("|".join(f"{t['id']}:{t.get('text', '')}"
                                      for t in sorted(inputs["tfrs"], key=lambda x: x["id"]) if t["vip"]))
            # 2026-10-14 (operator): one alert per VIP set, with the times -- no hourly re-page.
            windows = "; ".join(f"{t['id']} {t.get('window', '?')}" for t in inputs["tfrs"] if t["vip"])
            if _route_dedup.should_push("route-impact", h):
                _ntfy.send("hot-alerts", f"{windows}\n\n{narrative}", title=title, priority=5,
                           tags="car,rotating_light")
                _route_dedup.record("route-impact", h)
            else:
                log.debug("%s: hot-alerts suppressed (dedup, same VIP TFR set <1h)", SKILL_NAME)
        elif narrative:
            log.info("%s: no VIP TFRs — DB write only, no ntfy push", SKILL_NAME)

    finally:
        # 2026-08-25 fix (Opus blind review C-7): only commit the gate
        # hash once we know this run didn't crash -- see
        # sr2_gate.commit_gate()'s docstring for why the write is
        # deferred until after the guarded work actually completes.
        if status != "error":
            commit_gate(SKILL_NAME, _gate_hash)
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
