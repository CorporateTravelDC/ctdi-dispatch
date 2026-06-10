"""
tfr-enrichment — SR-1 + SR-2 compliant.
Fallback: structured raw TFR/METAR summary pushed to ntfy if API unavailable.
"""
import os
import argparse, logging, sys, time
from common import db
from common.push_dedup import PushDedup, content_hash as _ch
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)
SKILL_NAME = "tfr-enrichment"
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "")
OLLAMA_MODEL      = (os.getenv("OLLAMA_OSINT_MODEL")
                     or os.getenv("OLLAMA_MODEL")
                     or "mistral")
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
    if not _tfr_skill_dedup.should_push(key, h, hot=hot):
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


def _fallback_narrative(inputs: dict) -> str:
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
        f"[FALLBACK {ts}] TFRs: {tfr_count} active, VIP: {vip_ids}. "
        f"Weather: {wx}. "
        f"NAS programs: {len(inputs['nas_programs'])} active."
    )


def main(force: bool = False) -> None:
    inputs = build_inputs()
    gate_result = hash_gate(SKILL_NAME, inputs, force=force)
    if gate_result == "skipped":
        log.debug("%s: inputs unchanged — skipping", SKILL_NAME)
        sys.exit(0)

    status = "error"

    try:
        # Deterministic fallback is now the primary path — no API dependency.
        narrative = _fallback_narrative(inputs)
        status = "ok"

        vip_ids = [t["id"] for t in inputs["tfr_ids"] if t["vip"]]
        all_ids = [t["id"] for t in inputs["tfr_ids"]]
        for tid in vip_ids:
            db.set_tfr_enrichment(tid, narrative)
        db.insert_route_narrative(narrative, all_ids, vip_ids)
        log.info("%s: %s — %d VIP TFRs", SKILL_NAME, status, len(vip_ids))

        if vip_ids or inputs["tfr_ids"]:
            priority = 5 if vip_ids else 3
            title = "VIP TFR Alert" if vip_ids else "TFR Update"
            if status == "fallback": title += " [FALLBACK]"
            # Stable key: sorted TFR IDs + VIP flags — immune to narrative wording variation
            stable = "|".join(
                f"{t['id']}:vip={t['vip']}" for t in
                sorted(inputs["tfr_ids"], key=lambda x: x["id"])
            )
            _push_ntfy(narrative, title, priority=priority, stable_key=stable)

    finally:
        log_usage(SKILL_NAME, MODEL, 0, 0, status, gate_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
