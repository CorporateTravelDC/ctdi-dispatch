"""
tfr-enrichment — SR-1 + SR-2 compliant.
Fallback: structured raw TFR/METAR summary pushed to ntfy if API unavailable.
"""
import argparse, logging, sys, time
import anthropic, requests
from common import config, db
from common.sr1_log import log_usage
from common.sr2_gate import hash_gate

log = logging.getLogger(__name__)
SKILL_NAME = "tfr-enrichment"
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a dispatch assistant for an executive chauffeur operation in the Washington DC
metropolitan area. You have operational knowledge of DC-area airspace, VIP movement patterns,
and how TFRs affect ground transportation.

Given a list of active TFRs and current METAR conditions, produce a concise operational narrative:
1. Identify any VIP/POTUS/Marine One TFRs and their significance.
2. Note the active airspace restrictions and which corridors are affected.
3. State the current weather conditions and how they interact with operations.
4. Provide a one-line operational recommendation for ground transportation.

Be direct and specific. Maximum 300 words. No preamble."""


import hashlib
import json
import time

_TFR_DEDUP_SECS = 3600


def _tfr_dedup_path():
    import pathlib
    return pathlib.Path(config.state_dir()) / "pusher-tfr-dedup.json"


def _load_tfr_dedup():
    p = _tfr_dedup_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_tfr_dedup(state):
    p = _tfr_dedup_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))


def _content_hash(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]


def _push_ntfy(text: str, title: str, priority: int = 3, stable_key: str = "") -> None:
    """Fire to tfr-alert with 1-hour dedup (shared state with pusher).
    Fires immediately if content changed; otherwise suppressed for 1 hour.
    """
    dedup = _load_tfr_dedup()
    now = time.time()
    key = f"enrichment_{SKILL_NAME}"
    # Use stable_key (TFR IDs + VIP flags) if provided; fall back to text hash.
    # This prevents Claude narrative variation from bypassing the hour gate.
    content_h = _content_hash(stable_key) if stable_key else _content_hash(text)
    last = dedup.get(key, {})
    content_changed = last.get("hash") != content_h
    hour_elapsed = (now - last.get("ts", 0)) >= _TFR_DEDUP_SECS
    if not content_changed and not hour_elapsed and last.get("ts"):
        log.debug("%s: tfr-alert suppressed (dedup, same content <1h)", SKILL_NAME)
        return
    try:
        base = config.ntfy_url()
        token = config.ntfy_token().split(":")[0]
        headers = {"Authorization": f"Bearer {token}", "Priority": str(priority), "Title": title}
        requests.post(f"{base}/tfr-alert", data=text.encode(), headers=headers, timeout=10)
        requests.post(f"{base}/hot-alerts", data=text.encode(), headers=headers, timeout=10)
        dedup[key] = {"ts": now, "hash": content_h}
        _save_tfr_dedup(dedup)
    except Exception as e:
        log.warning("%s: ntfy push failed: %s", SKILL_NAME, e)


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

    input_tokens = output_tokens = cache_read_tokens = cache_write_tokens = 0
    status = "error"

    try:
        narrative = None
        try:
            client = anthropic.Anthropic(api_key=config.anthropic_api_key())
            response = client.messages.create(
                model=MODEL, max_tokens=600,
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
            log.warning("%s: API unavailable (%s) — using fallback narrative", SKILL_NAME, e)
            status = "fallback"
            narrative = _fallback_narrative(inputs)

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
        log_usage(SKILL_NAME, MODEL, input_tokens, output_tokens, status, gate_result,
                  cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=f"{SKILL_NAME} skill")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(force=args.force)
