"""
tfr-enrichment — SR-1 + SR-2 compliant.
Fallback: structured raw TFR/METAR summary pushed to ntfy if Ollama unavailable.
"""
import os
import argparse, logging, sys, time
import httpx
from common import db
from common.llm import generate as llm_generate
from common.push_dedup import PushDedup, content_hash as _ch
from common.sr1_log import log_usage
from common.sr2_gate import check_gate, commit_gate

log = logging.getLogger(__name__)
SKILL_NAME = "tfr-enrichment"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_TFR_ENRICHMENT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "corporatetraveldc-pi5-tfr-enrichment:latest")
MODEL             = OLLAMA_MODEL if OLLAMA_BASE_URL else "deterministic"

SYSTEM_PROMPT = """You are a dispatch assistant for an executive chauffeur operation in the Washington DC
metropolitan area. You have operational knowledge of DC-area airspace, VIP movement patterns,
and how TFRs affect ground transportation.

Given a list of active TFRs and current METAR conditions, produce a concise operational narrative:
1. Identify any VIP/POTUS/Marine One TFRs and their significance.
2. Note the active airspace restrictions and which corridors are affected.
3. State the current weather conditions and how they interact with operations.
4. Provide a one-line operational recommendation for ground transportation.

Be direct and specific. Maximum 300 words. No preamble."""


import time

from common import ntfy_push as _ntfy

_tfr_skill_dedup = PushDedup("tfr")


def _push_ntfy(text: str, title: str, priority: int = 3, stable_key: str = "") -> None:
    """Fire to tfr-alert + hot-alerts with 1-hour dedup via shared PushDedup.
    stable_key (TFR IDs + VIP flags) prevents Claude narrative variation
    from bypassing the hour gate. VIP pushes are always hot (no suppression).
    """
    key = f"enrichment_{SKILL_NAME}"
    h = _ch(stable_key) if stable_key else _ch(text)
    hot = "vip=True" in stable_key or "vip=1" in stable_key
    # 2026-09-03 (forward-only push_dedup redesign): PERIODIC api,
    # deliberately -- same still-active-TFR hourly re-push contract as
    # pusher/main.py's push_vip_tfrs and route_impact's hot-alerts gate
    # (all three share the "tfr"/"route" dedup design from 2026-08-16).
    if not _tfr_skill_dedup.should_push_periodic(key, h, hot=hot):
        log.debug("%s: tfr-alert suppressed (dedup, same content <1h)", SKILL_NAME)
        return
    _ntfy.send("tfr-alert",  text, title=title, priority=priority, tags="rotating_light")
    _ntfy.send("hot-alerts", text, title=title, priority=priority, tags="rotating_light")
    _tfr_skill_dedup.record(key, h)


def build_inputs() -> dict:
    tfrs = db.get_active_tfrs()
    metars = db.get_metar_snapshot()
    nas = db.get_active_nas_programs()
    return {
        "tfr_ids": sorted([
            {"id": t["tfr_id"], "vip": t["is_vip"],
             "start": t["effective_start"], "end": t["effective_end"]}
            for t in tfrs
        ], key=lambda x: x["id"]),
        "metar_content": sorted([
            {"station": m["station"], "ceiling": m["ceiling_ft"],
             "vis": m["visibility_sm"], "wind": m["wind_kt"], "precip": m["precip_code"]}
            for m in metars
        ], key=lambda x: x["station"]),
        "nas_programs": sorted([
            {"id": p["program_id"], "type": p["type"], "facility": p["facility"]}
            for p in nas
        ], key=lambda x: x["id"]),
    }


def build_user_message(inputs: dict) -> str:
    tfr_lines = [f"  TFR {t['id']}{' [VIP/POTUS]' if t['vip'] else ''}" for t in inputs["tfr_ids"]]
    metar_lines = [
        f"  {m['station']}: ceiling={m['ceiling']}ft vis={m['vis']}SM wind={m['wind']}kt precip={m['precip'] or 'nil'}"
        for m in inputs["metar_content"]
    ]
    nas_lines = [f"  {p['type']} at {p['facility']} ({p['id']})" for p in inputs["nas_programs"]] or ["  None active"]
    return (
        "Active TFRs:\n" + ("\n".join(tfr_lines) or "  None active") + "\n\n"
        "Current METAR conditions:\n" + ("\n".join(metar_lines) or "  No data") + "\n\n"
        "NAS delay programs:\n" + "\n".join(nas_lines)
    )


def _vip_user_message(inputs: dict) -> str:
    """Focused prompt for VIP TFRs only — keeps token count low for Pi CPU inference."""
    vip = [t for t in inputs["tfr_ids"] if t["vip"]]
    metar_lines = [
        f"  {m['station']}: ceiling={m['ceiling']}ft vis={m['vis']}SM wind={m['wind']}kt precip={m['precip'] or 'nil'}"
        for m in inputs["metar_content"]
        if m["station"] in ("KDCA", "KIAD", "KBWI", "KADW")
    ]
    nas_lines = [f"  {p['type']} at {p['facility']} ({p['id']})" for p in inputs["nas_programs"]] or ["  None active"]
    return (
        f"VIP/POTUS TFRs active ({len(vip)}):\n" +
        "\n".join(f"  TFR {t['id']} [VIP/POTUS]" for t in vip) + "\n\n"
        "Current METAR (DC airports):\n" + ("\n".join(metar_lines) or "  No data") + "\n\n"
        "NAS delay programs:\n" + "\n".join(nas_lines)
    )


def _call_ollama_vip(inputs: dict) -> str | None:
    """Call LLM (Ollama-first, Anthropic fallback) for VIP TFR narrative.
    Returns narrative text or None (caller falls back to deterministic).
    """
    # priority="hot" (2026-07-26): this is the VIP/POTUS TFR narrative --
    # must never wait behind a report job (ep-brief/ops-brief/weekly-summary/
    # osint-monitor) for the shared Ollama slot. See common/ollama_lock.py.
    return llm_generate(
        system=None,  # dedicated Modelfile carries this now
        prompt=_vip_user_message(inputs),
        ollama_model=OLLAMA_MODEL,
        max_tokens=220,
        temperature=0.2,
        priority="hot",
        # Measured 2026-08-15 under forced TIER2+ contention (Phase-3
        # methodology: guard timer paused, synthetic burn, la 26 at
        # sample): 1009-tok prompt / 103.5s eval + gen at 0.79 tok/s
        # -> 266.2s at the 220-tok cap; delta over the 48.4s
        # spiked persona-only ref = 321.4s; x1.10 top-up to the 53s locked bound applied;
        # +16s cold-load allowance (hot path skips _preload_model);
        # (53 + 368.0) x 1.25 = 526s -> 540.
        timeout=540,
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
    vip = [t for t in inputs["tfr_ids"] if t["vip"]]
    tfr_count = len(inputs["tfr_ids"])
    vip_ids = ", ".join(t["id"] for t in vip) if vip else "none"
    primary = [m for m in inputs["metar_content"] if m["station"] in ("KDCA", "KIAD", "KBWI")]
    wx = "; ".join(
        f"{m['station']} {m['ceiling']}ft/{m['vis']}SM/{m['wind']}kt"
        for m in primary
    ) or "no METAR"
    return (
        f"[{ts}] TFRs: {tfr_count} active, VIP: {vip_ids}. "
        f"Weather: {wx}. "
        f"NAS programs: {len(inputs['nas_programs'])} active."
    )


# Keep _fallback_narrative as alias for SR-1 log compatibility
_fallback_narrative = _deterministic_summary


def main(force: bool = False) -> None:
    inputs = build_inputs()
    gate_result, _gate_hash = check_gate(SKILL_NAME, inputs, force=force)
    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping", SKILL_NAME)
        sys.exit(0)

    status = "error"

    try:
        vip_ids = [t["id"] for t in inputs["tfr_ids"] if t["vip"]]
        all_ids  = [t["id"] for t in inputs["tfr_ids"]]

        if vip_ids:
            # VIP path: focused prompt (~30-60 tokens) — Ollama call is feasible on Pi CPU
            ollama_result = _call_ollama_vip(inputs)
            if ollama_result:
                narrative = ollama_result
                status = "ok"
                log.info("%s: VIP narrative via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
            else:
                # 2026-08-06: narrow safety net around the fallback ITSELF --
                # same pattern applied identically across every skill with
                # an Ollama fallback. See route_impact.py for the full note.
                try:
                    narrative = _deterministic_summary(inputs)
                    status = "fallback"
                    log.warning("%s: Ollama unavailable for VIP TFR — using deterministic fallback", SKILL_NAME)
                except Exception as fallback_err:
                    log.error("%s: deterministic fallback also failed — %s", SKILL_NAME, fallback_err)
                    narrative = (
                        f"[{SKILL_NAME.upper()}] Generation failed -- both Ollama and the "
                        f"deterministic fallback errored. See logs."
                    )
                    status = "fallback_error"
        else:
            # No VIP TFRs: deterministic is the right call, not a degraded path.
            # No [FALLBACK] label — this is clean structured output.
            narrative = _deterministic_summary(inputs)
            status = "ok"
            log.info("%s: no VIP TFRs — deterministic summary (%d active)", SKILL_NAME, len(all_ids))

        for tid in vip_ids:
            db.set_tfr_enrichment(tid, narrative)
        db.insert_route_narrative(narrative, all_ids, vip_ids)
        log.info("%s: %s — %d VIP TFRs", SKILL_NAME, status, len(vip_ids))

        if vip_ids:
            # VIP path only — non-VIP TFR churn generates a new hash every cycle
            # (routine TFR expiry/addition changes all 365 IDs) and floods hot-alerts.
            # Routine non-VIP updates are written to DB only; API consumers poll /api/v1/tfr.
            title = "VIP TFR Alert"
            if status == "fallback":
                title += " [FALLBACK]"
            # Stable key: VIP TFR IDs only — immune to routine non-VIP TFR cycling
            stable = "|".join(
                f"{t['id']}:vip={t['vip']}" for t in
                sorted(inputs["tfr_ids"], key=lambda x: x["id"])
                if t["vip"]
            )
            _push_ntfy(narrative, title, priority=5, stable_key=stable)
        else:
            log.info("%s: no VIP TFRs — DB write only, no ntfy push (%d active)", SKILL_NAME, len(all_ids))

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
