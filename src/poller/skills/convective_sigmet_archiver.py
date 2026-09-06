"""
convective_sigmet_archiver -- durable, append-only archive of AWC KKCI
convective SIGMET polygons (the weather half Detector D was blocked on).

The 2026-08-30 late-night pass built Detector D's route half
(fdps_route_versions + the genuine-reroute-vs-noise classifier with
eta_delta_min cost) and honestly declared the weather half BLOCKED:
"weather attribution requires an ARCHIVED, timestamped convective-SIGMET
polygon history ('was there weather when THIS reroute happened') and none
exists" -- web/main.py's /api/v1/airmets overlay is a 5-minute in-memory
live snapshot that stores nothing (correct for a UI, useless for
history), NWWS is WFO-filtered so AWC KKCI convective SIGMETs never
arrive on it, and ITWS is terminal-scale. This skill is the prescribed
legwork: a small poller fetcher on AWC's /api/data/airsigmet plus an
append-only polygon archive (db_swim.convective_sigmet_archive, v47).

Scope discipline (2026-08-31): this skill is the FETCHER + ARCHIVE ONLY.
The actual attribution matching (joining a reroute event's timing/route
against the polygons active at that moment) is deliberately NOT built
here -- it can only be tested against real accumulated history, which
starts existing the day this timer goes live. Same
don't-build-against-zero-data discipline as every 2026-08-30 pass.

Mechanics:
  - Independent fetch of the SAME AWC Data API endpoint the web overlay
    uses (shared normalizer: common/airsigmet.py; the web app's
    in-memory cache is process-local and not shareable anyway). Full
    raw product text is kept (raw_text_limit=None), not the overlay's
    600-char display truncation.
  - Filter: hazard == CONVECTIVE only. Domestic AIRMETs and
    non-convective SIGMET hazards (TURB/ICE/IFR/...) are not the
    reroute-attribution signal per the external Detector D document;
    hazard is still stored per-row for future flexibility.
  - Insert-once per (sigmet_id, valid_from) -- see
    db_swim.archive_convective_sigmets() for why not id-alone and why
    not an upsert. Re-fetching a still-active SIGMET is a no-op.
  - Cadence: every 10 min (quadlet timer). Convective SIGMETs are
    issued hourly at H+55 with specials at any time and ~2 h validity;
    10 min bounds worst-case archival latency well inside any
    attribution window while staying lighter than the UI overlay's own
    5-min cache TTL. Missing one cycle costs nothing fatal -- the next
    cycle re-sees anything still valid; only a SIGMET issued AND
    cancelled inside ~10 min could slip through, an acceptable trade.
  - NO PRUNE: this table must accumulate (retention_prune.py's opt-in
    job list deliberately excludes it -- see SCHEMA_SWIM_V47 comment).

SR-1: log_usage() in the finally block, model "deterministic".
SR-2: exempt -- deterministic, no LLM call, and a gate would be wrong
anyway (an unchanged AWC payload still needs no re-archive thanks to
INSERT OR IGNORE, so skipping adds nothing).
"""
import logging

from common import airsigmet, db_swim
from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "convective-sigmet-archiver"

_FETCH_TIMEOUT_S = 15.0  # matches web/main.py's overlay fetch timeout


def fetch_convective() -> tuple[int, list[dict]]:
    """Fetch + normalize the full active AIRMET/SIGMET set, return
    (total_active_records, convective_only). Raises on fetch failure --
    main() owns the non-fatal handling."""
    records = airsigmet.fetch_airsigmets(timeout=_FETCH_TIMEOUT_S,
                                         raw_text_limit=None)
    convective = [r for r in records if r["hazard"] == airsigmet.CONVECTIVE]
    return len(records), convective


def main() -> None:
    status = "error"
    try:
        # Fresh-DB safe: this skill owns the table (poller-side), so it
        # creates it itself rather than depending on ingest/web having
        # run first -- see init_db_swim_v47()'s caller-contract note.
        db_swim.init_db_swim_v47()
        total, convective = fetch_convective()
        inserted, skipped = db_swim.archive_convective_sigmets(convective)
        # "0 convective active" is the common case outside convective
        # season/hours and is a SUCCESSFUL run, not an error.
        log.info("%s: %d active airsigmet record(s), %d convective, "
                 "%d newly archived, %d skipped",
                 SKILL_NAME, total, len(convective), inserted, skipped)
        status = "ok"
    except Exception as e:
        # Non-fatal by design: a transient AWC/network failure just
        # means this cycle archives nothing; the timer retries in 10
        # min and anything still valid is re-seen. Never crash a
        # scheduled production skill over it.
        log.error("%s: archive cycle failed: %s", SKILL_NAME, e)
    finally:
        log_usage(SKILL_NAME, "deterministic", 0, 0, status, "new")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
