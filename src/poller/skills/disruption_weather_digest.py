"""
disruption_weather_digest -- second-brain ingestion for the flight/train/
maritime disruption + weather-vs-facility correlation work done in the
2026-08-09 catch-up session. Runs analyze_disruption_weather_split()
(flights, via nas_programs' real FAA/SWIM reason field),
analyze_train_disruption_summary() (trains, via train_events.delay_minutes
plus a regional NEC weather-activity proxy), and
analyze_vessel_disruption_summary() (maritime, currently an honest
insufficient_data stub) -- then writes a synthesized note into
corporatetraveldc/01-Sources/transport-patterns/disruption-weather/ in the
second-brain vault, and a short truncated capsule to state_dir() for
ops_brief.py to splice into the daily brief (same "Latest X excerpt"
pattern as second_brain_daily.py's ops-brief/export-analysis excerpts).

Deliberately distinct from transport_pattern_digest.py (12h timer,
route-lock/codeshare-drift mining): that skill asks "does this flight/
train number/vessel have a locked-in pattern?" This skill asks "how much
disruption is happening right now, and how much of it is weather vs.
facility/volume-driven?" -- a different question, answered from a
different data source (nas_programs' reason field + train_events.delay_
minutes, not route/schedule consistency).

Honest scope note, carried in every digest: the flight-side weather/
facility split is real (FAA-sourced nas_programs.reason). The train side
has NO delay-cause field anywhere in this platform's Amtrak ingest --
its "weather correlation" is a regional proxy (aviation WX ground-
programs near the NEC on the same days), not a per-train attribution.
Maritime is currently empty (AIS_AISHUB_ID not registered). See each
analyze_*() function's own docstring in common/db.py for the full
reasoning -- this digest must not blur these distinctions into a false
claim of parity across the three verticals.

Schedule: daily (corporatetraveldc-disruption-weather-digest.timer).

Model: same tiered pattern as transport_pattern_digest.py -- Ollama
first (corporatetraveldc-pi5-brief, dedicated
Modelfile), deterministic fallback if unavailable. SR-1 compliant
(log_usage).
"""
import logging
import sqlite3
from datetime import datetime, timezone

from common import config, db
from common.llm import generate as llm_generate
from common.ntfy_push import send_run_status
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "disruption-weather-digest"
OLLAMA_MODEL = "corporatetraveldc-pi5-disruption-weather-digest:latest"  # dedicated Phase-4 model, persona + skill layer in its Modelfile SYSTEM
LOOKBACK_DAYS = 30
CAPSULE_MAX_CHARS = 500


def build_digest_content() -> tuple[str, dict]:
    flights = db.analyze_disruption_weather_split(days=LOOKBACK_DAYS)
    trains = db.analyze_train_disruption_summary(days=LOOKBACK_DAYS)
    vessels = db.analyze_vessel_disruption_summary(days=LOOKBACK_DAYS)

    stats = {
        "flight_facilities": len(flights["facility_breakdown"]),
        "train_routes": len(trains["delay_summary"]),
        "vessel_status": vessels["status"],
    }

    sections = [
        f"## Flights ({LOOKBACK_DAYS}-day, {flights['sample_window']})",
    ]
    top_flights = flights["facility_breakdown"][:12]
    for x in top_flights:
        sections.append(
            f"- {x['facility']}: {x['total_programs']} programs, "
            f"{x['pct_weather']}% weather-driven, "
            f"{100-x['pct_weather']:.1f}% facility/volume-or-other"
        )
    if not top_flights:
        sections.append("- No qualifying facility data this window.")

    sections.append("")
    sections.append(f"## Trains ({LOOKBACK_DAYS}-day, {trains['sample_window']})")
    sections.append(
        "Regional weather-activity proxy (aviation WX ground-programs near the NEC, "
        "NOT a per-train delay-cause attribution): "
        f"{trains['regional_weather_context']['wx_flagged_days']} of "
        f"{trains['regional_weather_context']['window_days']} days flagged."
    )
    top_trains = trains["delay_summary"][:10]
    for x in top_trains:
        sections.append(
            f"- Train {x['train_number']} ({x['route_name']}): "
            f"{x['pct_over_threshold']}% of {x['samples']} observations delayed, "
            f"avg {x['avg_delay_minutes']} min"
        )
    if not top_trains:
        sections.append("- No qualifying train delay data this window.")

    sections.append("")
    sections.append("## Maritime")
    if vessels["status"] == "insufficient_data":
        sections.append(f"- {vessels['reason']}")
    elif vessels["status"] == "not_yet_implemented":
        sections.append(f"- {vessels['reason']}")
    else:
        sections.append(f"- status: {vessels['status']}")

    return "\n".join(sections), stats


def _write_capsule(raw_content: str, stats: dict) -> None:
    """Short truncated capsule for ops_brief.py's daily-brief splice, read
    back via common.disruption_weather_watch.get_disruption_weather_capsule()
    -- same freshness-gated-cache shape as common/aam_watch.py. Filename
    here MUST match that module's _CACHE_FILENAME constant. Deliberately
    best-effort: a capsule write failure must not fail the main digest run."""
    try:
        import pathlib
        from common.disruption_weather_watch import _CACHE_FILENAME
        capsule_path = pathlib.Path(config.state_dir()) / _CACHE_FILENAME
        capsule = raw_content[:CAPSULE_MAX_CHARS]
        if len(raw_content) > CAPSULE_MAX_CHARS:
            capsule += "..."
        capsule_path.write_text(capsule)
    except Exception as e:
        log.warning("%s: capsule write failed (non-fatal): %s", SKILL_NAME, e)


def main() -> None:
    status = "error"
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H%M")
    rel_path = None

    try:
        raw_content, stats = build_digest_content()

        _write_capsule(raw_content, stats)

        # CUI/PII scrub gate -- non-negotiable, see second_brain.scrub_gate
        raw_content = gate(raw_content, source=SKILL_NAME)

        ollama_result = llm_generate(
            system=None, prompt=raw_content,  # dedicated Modelfile carries this now
            ollama_model=OLLAMA_MODEL, max_tokens=350, temperature=0.3,
            # Measured 2026-08-15 under forced TIER2+ contention (Phase-3
            # methodology: guard timer paused, synthetic burn, la 43 at
            # sample): 1066-tok prompt / 121.4s eval + gen at 0.80 tok/s
            # -> 439.3s at the 350-tok cap; delta over the 47.1s
            # spiked persona-only ref = 513.7s; x1.13 top-up to the 53s locked bound applied;
            # (53 + 578.5) x 1.25 = 789s -> 810.
            timeout=810,
            # Same allow_anthropic=False/max_retries=0 reasoning as
            # transport_pattern_digest.py -- see that skill's comment for
            # the exact 2026-08-06 timeout-stacking incident this avoids.
            allow_anthropic=False, max_retries=0,
        )
        if ollama_result:
            ollama_result = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            narrative = ollama_result + "\n\n---\n\n**Raw disruption data:**\n\n" + raw_content
            status = "ok"
            log.info("%s: narrative generated via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
        else:
            try:
                narrative = raw_content
                status = "fallback"
                log.info("%s: Ollama unavailable -- using raw disruption data", SKILL_NAME)
            except Exception as fallback_err:
                log.error("%s: fallback also failed — %s", SKILL_NAME, fallback_err)
                narrative = (
                    f"[{SKILL_NAME.upper()}] Generation failed -- both Ollama and the "
                    f"deterministic fallback errored. See logs."
                )
                status = "fallback_error"

        frontmatter = (
            "---\n"
            f"generated_at: {now.isoformat()}\n"
            "ingest_method: disruption-weather-digest-auto\n"
            f"stats: {stats}\n"
            "---\n\n"
        )
        note = frontmatter + f"# Disruption / Weather-vs-Facility Digest — {stamp}\n\n" + narrative + "\n"

        rel_path = f"{webdav_client.BUSINESS_ROOT}/01-Sources/transport-patterns/disruption-weather/{stamp}.md"
        webdav_client.put(rel_path, note)
        log.info("%s: wrote %s (%d bytes, status=%s)", SKILL_NAME, rel_path, len(note), status)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"Disruption/Weather Digest — {stamp}", content=note,
            tags="transport-patterns,disruption,weather-facility,auto",
            ingest_method="disruption-weather-digest-auto",
        )
        conn.close()

    except ScrubGateBlocked as e:
        status = "blocked"
        log.error("%s: BLOCKED by scrub gate: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, OLLAMA_MODEL if status == "ok" else "deterministic",
                   0, 0, status, "n/a")
        send_run_status(SKILL_NAME, status, detail=rel_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
