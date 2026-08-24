"""
semantic_compile_daily -- daily re-run of second_brain.semantic.compile_layer()
so the second-brain semantic layer stays live instead of going stale.

Real gap this closes, found 2026-08-23: the semantic layer (src/second_brain/
semantic/) was compiled exactly once, by hand, the day it was built
(2026-08-18) and never again -- confirmed via semantic_meta.compiled_at and
the compiled artifacts' mtimes all frozen at 2026-08-18, and
semantic_note_concepts/semantic_note_derivations having zero coverage for
anything written since. Nothing called compile_layer() automatically; it was
a standalone CLI step (`python3 -m second_brain.semantic --compile`) that
only ever ran the one time. This skill is that missing schedule -- same
"a mechanism existed but nothing scheduled it" shape as
entity_tracking_digest's founding gap, different subsystem.

Deliberately NOT an Ollama call, same reasoning as entity_tracking_digest:
compile_layer() is a deterministic, rule-based recomputation over the vault
index (no LLM, no embeddings -- see compile.py's own module docstring), and
every existing digest already competes for the single Ollama slot.
compile_layer() itself documents running "well under a second on the real
5,700-document index".

Also runs assign_derivations() (added 2026-08-23 alongside the `derivation`
ontology facet) as part of the same compile -- see ontology.json's changelog
and CLAUDE.md's second-brain section for what that captures (note-to-note
leans_on/derives_from/reutilizes edges, parsed from a note's own
`## Provenance` section) and why it's a separate table from
semantic_note_concepts.

Schedule: daily (corporatetraveldc-semantic-compile-daily.timer), fixed
calendar grid at 03:47 ET -- off every other timer's :00/:10/:12/:15/:30/:45
marks, same OnCalendar-not-OnUnitActiveSec bunching lesson recorded on
trains-yachts-daily-watch.timer and entity_tracking_digest.timer.

No cursor, no "since last run" windowing -- compile_layer() is a pure
function of (ontology.json, lexicon.py, current index contents) and always
does a full DROP/CREATE of every semantic_* table (see compile.py's own
docstring for why that's deliberate, not a missed optimization). There is
nothing to skip; every run either succeeds or reports why it didn't.

SR-1: log_usage() in a finally block, model="none" (no LLM call made).
SR-2: not applicable -- no content-bearing input to hash; behavior is
"recompute the whole layer from current state", same as entity_tracking_digest.
"""
import logging

from common.sr1_log import log_usage

log = logging.getLogger(__name__)

SKILL_NAME = "semantic-compile-daily"


def run() -> dict:
    status = "ok"
    result: dict = {}
    try:
        from second_brain.semantic.compile import compile_layer

        result = compile_layer()
        log.info(
            "semantic_compile_daily: v%s, %d concept assignments, "
            "%d derivation edges, %d unmapped tags",
            result.get("version"), result.get("assignments", 0),
            result.get("derivations", 0), result.get("unmapped_tags", 0),
        )
        return {"status": "ok", **result}
    except Exception as e:
        status = "error"
        log.error("semantic_compile_daily: failed: %s", e)
        return {"status": "error", "reason": str(e)}
    finally:
        log_usage(SKILL_NAME, "none", 0, 0, status=status, gate_result="new")


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run(), indent=2))
