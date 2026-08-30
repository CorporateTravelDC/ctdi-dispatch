"""
transport_pattern_digest -- second-brain ingestion for the flight/train/
vessel route-lock and codeshare-map system (2026-07-28, Phase 2.8/3 wiring).

Runs codeshare_map's Phase 3 decay sweep, then all three
analyze_*_patterns() mining functions (flights, trains, vessels), and
writes a synthesized note into corporatetraveldc/01-Sources/transport-
patterns/ in the second-brain vault. This is the scheduled job that Phase
2's flight mining and Phase 3's decay sweep were explicitly designed to
need -- see common/db.py's analyze_flight_number_patterns docstring
("computed on demand... no cron needed yet" turned out wrong once
flight_events volume was actually measured; this timer is that cron) and
decay_stale_codeshare_mappings's docstring ("not wired to a timer yet").

Honest scope note written into every digest: train/vessel schedule-drift
and route-lock detection is only as deep as this platform's own retained
history (train_events backfilled from amtrak_status on 2026-07-28;
vessel_events accumulating from today forward, and currently empty because
AIS_AISHUB_ID isn't configured yet). Multi-year drift detection (the
example that motivated this whole feature -- a shuttle's scheduled
departure creeping over years) needs years of data this system has not
been running long enough to have; the mechanism is real and compounds a
genuine baseline starting now.

2026-08-09: cruise ships specifically (as opposed to DC-area water-taxi/
harbor traffic) are stubbed via analyze_vessel_patterns()'s new
vessel_class tag (AIS type 60-69 = "passenger", the closest available
signal -- see _vessel_class_for_ship_type in common/db.py) rather than a
separate mining function. No new mechanism needed once real cruise-relevant
AIS coverage exists; today's blocker is twofold: (1) AIS_AISHUB_ID isn't
registered yet, and (2) even once it is, AISHub's cooperative-receiver
network is coastal/harbor-range, so open-ocean cruise itineraries would
need a satellite-AIS source, not built.

Schedule: every 12h (corporatetraveldc-transport-pattern-digest.timer).

Model: same tiered pattern as second_brain_daily.py -- Ollama first (cheap,
local), deterministic fallback if unavailable. SR-1 compliant (log_usage).
"""
import logging
import sqlite3
from datetime import datetime, timezone

from common import db
from common.llm import generate as llm_generate
from common.ntfy_push import send_run_status
from common.sr1_log import log_usage
from second_brain import webdav_client
from second_brain.index_db import INDEX_DB, index_note
from second_brain.index_db import init_db as init_vault_db
from second_brain.scrub_gate import ScrubGateBlocked, gate

log = logging.getLogger(__name__)

SKILL_NAME = "transport-pattern-digest"
OLLAMA_MODEL = "corporatetraveldc-pi5-transport-digest:latest"  # dedicated Phase-4 model, persona + skill layer in its Modelfile SYSTEM

SYSTEM_PROMPT = """You are writing a technical digest entry for a
second-brain knowledge vault used by a DC-area executive chauffeur/
dispatch operation. You're given the output of route-lock and schedule-
drift mining across three transport verticals (commercial flights, Amtrak
trains, DC-area AIS vessel traffic) plus a codeshare-mapping cache health
check. Summarize under 300 words, plain markdown, no headers deeper than
###. Call out anything that looks like a genuine pattern (a route-locked
flight/train number, a real schedule-time drift, a vessel cluster
suggesting a regular water-taxi/cruise run) versus data that's simply too
thin yet to conclude anything from -- do not oversell empty or sparse
results as findings. Be factual, not promotional."""


def build_digest_content() -> tuple[str, dict]:
    decay = db.decay_stale_codeshare_mappings()
    flights = db.analyze_flight_number_patterns(min_samples=5, dominance_threshold=0.85)
    trains = db.analyze_train_patterns(min_samples=10, drift_minutes_flag=10)
    vessels = db.analyze_vessel_patterns(min_samples=5)

    stats = {
        "codeshare_checked": decay["checked"],
        "codeshare_zeroed_out": len(decay["zeroed_out"]),
        "flight_route_locks": len(flights["route_locks"]),
        "train_route_locks": len(trains["route_locks"]),
        "train_drift_flags": len(trains["schedule_drift_flags"]),
        "vessel_route_locks": len(vessels["route_locks"]),
    }

    sections = [
        f"Codeshare map decay sweep: {decay['checked']} entries checked, "
        f"{len(decay['zeroed_out'])} zeroed out (unconfirmed 90+ days).",
        "",
        f"## Flights ({len(flights['route_locks'])} route-locked flight numbers, "
        f"{flights['sample_window']})",
    ]
    top_flights = sorted(flights["route_locks"], key=lambda x: -x["samples"])[:15]
    for x in top_flights:
        sections.append(
            f"- {x['airline']}{x['flight_num']}: {x['origin']}->{x['destination']} "
            f"({x['dominance']*100:.0f}% of {x['samples']} observations)"
        )
    if not top_flights:
        sections.append("- No flight numbers yet meet the sample-size/dominance threshold.")

    # 2026-08-20: real on-time departure/arrival history, from actual TFMS
    # airline-reported OOOI times vs. TFMS's own originalDeparture/
    # originalArrival -- see db.get_flight_ontime_history()'s docstring.
    # flight_ooooi_times only started being populated 2026-08-20 and is
    # only ever populated for a flight number that has itself been
    # watchlisted (TFMS OOOI capture is watchlist-gated, same as the rest
    # of tfms_parser.py) -- so this section will be sparse or empty for a
    # while, honestly, not a bug.
    ontime_numbers = db.list_flight_numbers_with_ooooi_data(days=14)
    ontime_rows = []
    for _airline, _num in ontime_numbers:
        hist = db.get_flight_ontime_history(_airline, _num, days=14)
        if hist["insufficient_data"]:
            continue
        ontime_rows.append((_airline, _num, hist))
    stats["ontime_flight_numbers_with_data"] = len(ontime_rows)
    stats["ontime_drift_flags"] = sum(
        1 for _a, _n, h in ontime_rows
        for leg in (h["departure"], h["arrival"])
        if leg and leg.get("drift") and leg["drift"]["flagged"]
    )

    sections.append("")
    sections.append(
        f"## On-time performance ({len(ontime_rows)} flight numbers with "
        f"real OOOI-vs-scheduled data, 14-day window)"
    )
    def _leg_str(leg: dict | None, label: str) -> str:
        if not leg:
            return f"{label}: no matched pairs"
        s = f"{label} on-time {leg['on_time_rate']*100:.0f}% (avg {leg['avg_delay_minutes']:+.1f} min, n={leg['count']})"
        d = leg.get("drift")
        if d and d["flagged"]:
            s += f" [DRIFT: {d['shift_minutes']:+.1f} min, {d['old_avg_delay_minutes']:+.1f}->{d['new_avg_delay_minutes']:+.1f} min]"
        return s

    for _airline, _num, hist in sorted(ontime_rows, key=lambda r: -(r[2]["departure"] or r[2]["arrival"] or {}).get("count", 0))[:15]:
        dep_str = _leg_str(hist["departure"], "dep")
        arr_str = _leg_str(hist["arrival"], "arr")
        sections.append(f"- {_airline}{_num} ({hist['sample_days']}d sample): {dep_str}; {arr_str}")
    if not ontime_rows:
        sections.append(
            "- No flight number yet has a matched actual-vs-scheduled OOOI "
            "pair. Capture started 2026-08-20 and only covers watchlisted "
            "flight numbers -- expected to be sparse for the first "
            "1-2 weeks, not a data gap."
        )

    sections.append("")
    sections.append(
        f"## Trains ({len(trains['route_locks'])} route-locked train numbers, "
        f"{len(trains['schedule_drift_flags'])} schedule-drift flags, {trains['sample_window']})"
    )
    top_trains = sorted(trains["route_locks"], key=lambda x: -x["samples"])[:10]
    for x in top_trains:
        sections.append(
            f"- Train {x['train_number']} ({x['route_name']}): "
            f"{x['origin']}->{x['destination']} ({x['dominance']*100:.0f}% of {x['samples']} observations)"
        )
    for x in trains["schedule_drift_flags"][:10]:
        sections.append(
            f"- DRIFT: train {x['train_number']} at {x['station_code']} shifted "
            f"{x['shift_minutes']:+.1f} min (time-of-day {x['old_avg_time_of_day_min']:.0f}->"
            f"{x['new_avg_time_of_day_min']:.0f} min, {x['samples']} samples)"
        )
    if not top_trains:
        sections.append("- No train numbers yet meet the sample-size/dominance threshold.")

    sections.append("")
    sections.append(
        f"## Vessels ({len(vessels['route_locks'])} MMSI position clusters, {vessels['sample_window']})"
    )
    top_vessels = sorted(vessels["route_locks"], key=lambda x: -x["samples"])[:10]
    for x in top_vessels:
        prefix_tag = f", {x['name_prefix']}" if x["name_prefix"] else ""
        weight_tag = f", {x['weight_class']}" if x.get("weight_class") else ""
        sections.append(
            f"- MMSI {x['mmsi']} ({x['name'] or 'unnamed'}, {x['vessel_class']}{prefix_tag}{weight_tag}): "
            f"dominant cluster {x['dominant_cluster_lat']},{x['dominant_cluster_lon']} "
            f"({x['dominance']*100:.0f}% of {x['samples']} observations, "
            f"{x['distinct_clusters']} distinct clusters)"
        )
    if not top_vessels:
        sections.append(
            "- No vessel data yet -- AIS_AISHUB_ID is not configured, so vessel_events "
            "is not currently accumulating. Not an error; awaiting that credential."
        )
    else:
        sections.append(
            f"- {vessels['passenger_cruise_class_count']} of the above are AIS-type "
            f"60-69 (\"passenger\", the closest available signal for cruise/charter "
            f"traffic -- AIS has no dedicated cruise code)."
        )
        prefix_counts = vessels.get("name_prefix_counts", {})
        prefix_summary = ", ".join(
            f"{p}={n}" for p, n in prefix_counts.items() if n
        )
        sections.append(
            "- By vessel-name prefix (independently queryable, MV/MY/SV/SY/PV/PY): "
            + (prefix_summary if prefix_summary else "none of the tracked MMSIs carry a recognized prefix yet.")
        )
    sections.append(
        "- Cruise-ship-specific tracking (multi-day port-call/itinerary drift, distinct "
        "from the DC-area water-taxi clusters above) is stubbed via this same vessel_class "
        "tag rather than a separate mechanism: same analyze_vessel_patterns() mining, "
        "filtered to passenger/cruise-class MMSIs, once real ocean-going AIS coverage "
        "exists (AISHub's free feed is coastal/cooperative-receiver-dependent, so DC-area "
        "AIS receivers won't see open-ocean cruise itineraries -- a dedicated satellite-AIS "
        "source would be the eventual upgrade path, not built yet)."
    )
    sections.append(
        "- Cargo/tanker weight class (Panamax/Neopanamax/Suezmax/Aframax/VLCC/ULCC for "
        "tankers; Handysize/Handymax/Panamax/Capesize/VLOC for bulk carriers) is stubbed "
        "via vessel_events.loa_m/beam_m (SCHEMA_V31) but reads 'insufficient_data' on "
        "every row today -- nothing ingests those dimension fields yet, pending live "
        "AISHub field verification once AIS_AISHUB_ID is registered. Container ship vs. "
        "bulk/general cargo isn't derivable from AIS ship_type at all (both report as "
        "'cargo', 70-79) -- that split needs an IMO-number cross-reference against a "
        "vessel-type registry, a separate future data source, not an AIS/AISHub gap."
    )

    return "\n".join(sections), stats


def main() -> None:
    status = "error"
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H%M")
    rel_path = None

    try:
        raw_content, stats = build_digest_content()

        # CUI/PII scrub gate -- non-negotiable, see second_brain.scrub_gate
        raw_content = gate(raw_content, source=SKILL_NAME)

        ollama_result = llm_generate(
            system=None, prompt=raw_content,  # dedicated Modelfile carries this now
            ollama_model=OLLAMA_MODEL, max_tokens=350, temperature=0.3,
            # Measured 2026-08-15 under forced TIER2+ contention (Phase-3
            # methodology: guard timer paused, synthetic burn, la 49 at
            # sample): 888-tok prompt / 90.7s eval + gen at 0.94 tok/s
            # -> 370.7s at the 350-tok cap; delta over the 53.0s
            # spiked persona-only ref = 408.4s; spike met/exceeded the locked 53s bound, no scaling;
            # (53 + 408.4) x 1.25 = 577s -> 600.
            timeout=600,
            # 2026-08-06: found live during a full-container-sweep -- this
            # skill crashed at 12:31-12:36 ET today from the exact same bug
            # already fixed elsewhere (aam_weekly_watch.py etc): a mid-flight
            # Ollama pause triggered a retry with a fresh 300s timeout, and
            # 300+300=600s hits this container's own TimeoutStartSec=600
            # with zero headroom. allow_anthropic=False keeps this Ollama-
            # only; max_retries=0 sends one failed attempt straight to the
            # deterministic fallback below instead of risking the kill.
            allow_anthropic=False, max_retries=0,
        )
        if ollama_result:
            ollama_result = gate(ollama_result, source=f"{SKILL_NAME}-llm")
            narrative = ollama_result + "\n\n---\n\n**Raw mining output:**\n\n" + raw_content
            status = "ok"
            log.info("%s: narrative generated via Ollama/%s", SKILL_NAME, OLLAMA_MODEL)
        else:
            # 2026-08-06: narrow safety net around the fallback ITSELF --
            # same pattern applied identically across every skill with an
            # Ollama fallback. See route_impact.py for the full note.
            try:
                narrative = raw_content
                status = "fallback"
                log.info("%s: Ollama unavailable -- using raw mining output", SKILL_NAME)
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
            "ingest_method: transport-pattern-digest-auto\n"
            f"stats: {stats}\n"
            "---\n\n"
        )
        note = frontmatter + f"# Transport Pattern Digest — {stamp}\n\n" + narrative + "\n"

        rel_path = f"{webdav_client.BUSINESS_ROOT}/01-Sources/transport-patterns/{stamp}.md"
        webdav_client.put(rel_path, note)
        log.info("%s: wrote %s (%d bytes, status=%s)", SKILL_NAME, rel_path, len(note), status)

        conn = sqlite3.connect(INDEX_DB)
        init_vault_db(conn)
        index_note(
            conn, rel_path, title=f"Transport Pattern Digest — {stamp}", content=note,
            tags="transport-patterns,codeshare,auto", ingest_method="transport-pattern-digest-auto",
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
