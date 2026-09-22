#!/usr/bin/env bash
# build-models.sh — Verify corporatetraveldc.<skill> Modelfiles stay in sync
# with common/personas.py after the Ollama -> llama.cpp cutover.
#
# REWORKED 2026-08-30 (operator directive: "figure out what is causing
# Llama server to not be called properly... rework all the model files
# such that they call the proper Llama server, not Ollama server").
#
# Root cause: this script's actual "build" logic (build_one/
# smoke_test_brief_model below, in the pre-rework version) was a
# half-finished mechanical find/replace of "ollama" -> "llama" -- it shelled
# out to `llama create`/`llama cp`/`llama rm`/`llama list`, none of which
# are real commands (llama.cpp has no such CLI; llama-server is a
# systemd-managed persistent process, not a model-registry tool), against
# LLAMA_URL=http://100.x.x.x:11434 -- Ollama's own dead port, nothing
# has listened there since the cutover (see common/llama_pool.py). It also
# referenced $OLLAMA_URL, never defined anywhere in the script, which
# would have tripped `set -u` before any of that even ran. It never
# actually worked post-cutover; it also could never have worked in
# principle, because the thing it existed to do -- build a dedicated
# Ollama model per skill -- is a step that no longer exists in this
# architecture at all.
#
# Why there's no "build" step anymore: llama-server (common/llama_pool.py)
# loads ONE shared phi3-mini-q4_0.gguf per tier (hot/chat), resident for
# the life of the systemd unit. There is no per-skill model artifact to
# create, promote, or roll back -- common/personas.py supplies each
# skill's system prompt and sampling params dynamically, per request, via
# build_system_prompt()/sampling_params(). Editing a persona's behavior
# now means editing personas.py directly; it takes effect on the very next
# request, no rebuild, no restart, no smoke test.
#
# What this script does now instead: the corporatetraveldc.<skill>
# Modelfiles are being kept in place, unmodified, as human-readable
# canonical source text (per personas.py's own module docstring: "do not
# hand-edit the SYSTEM/task text without updating both here and (until
# Ollama is retired) the source Modelfile, since they are compared during
# cutover verification") -- but nothing actually did that comparison
# before this rework. This script now does exactly that: for every
# Modelfile, extract its SYSTEM block and diff it (whitespace-normalized)
# against personas.py's build_system_prompt() for the matching persona.
# Reports DRIFT per skill; does not touch anything. GUARD 0 (signed-
# manifest integrity per Modelfile) is unchanged from the old build path
# -- still relevant, a Modelfile is still a tracked file that should
# match its signed hash before being trusted as a comparison source.
#
# The MODELS map below is UNCHANGED from the old build script on purpose:
# scripts/check-claude-md-drift.sh parses this exact
# `declare -A MODELS=(...)` block (by literal grep, not by running this
# script) to cross-check the platform's own documented "N dedicated
# models" count -- renaming or restructuring it would silently break that
# drift check.
#
# Usage:
#   build-models.sh                 # verify all 21 personas
#   build-models.sh <name> [name..] # verify only the named persona(s)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Signed-manifest integrity self-check (docs/COMPLIANCE_SECURITY.md "Signed
# Manifest Integrity") -- collective, whole-tree: this script does its own
# per-Modelfile check on (GUARD 0 below), but that doesn't cover this
# script's own integrity, or anything else it might read. Run once, up
# front, before touching anything.
if ! "${REPO_DIR}/scripts/verify-manifest.sh"; then
    echo "XX INTEGRITY CHECK FAILED -- refusing to run build-models.sh" >&2
    exit 5
fi

# name -> Modelfile suffix (== common/personas.py PERSONAS dict key).
# UNCHANGED structure -- see the check-claude-md-drift.sh note above.
declare -A MODELS=(
  [corporatetraveldc-pi5-aam-daily-watch]="aam-daily-watch"
  [corporatetraveldc-pi5-aam-weekly-watch]="aam-weekly-watch"
  [corporatetraveldc-pi5-aviation-daily-watch]="aviation-daily-watch"
  [corporatetraveldc-pi5-concierge-travel-daily-watch]="concierge-travel-daily-watch"
  [corporatetraveldc-pi5-dispatch-desk-memo]="dispatch-desk-memo"
  [corporatetraveldc-pi5-disruption-weather-digest]="disruption-weather-digest"
  [corporatetraveldc-pi5-ep-advance]="ep-advance"
  [corporatetraveldc-pi5-ep-advance-trend]="ep-advance-trend"
  [corporatetraveldc-pi5-executive-protection-daily-watch]="executive-protection-daily-watch"
  [corporatetraveldc-pi5-gig-economy-daily-watch]="gig-economy-daily-watch"
  [corporatetraveldc-pi5-ops-brief]="ops-brief"
  [corporatetraveldc-pi5-ops-brief-trend]="ops-brief-trend"
  [corporatetraveldc-pi5-osint-monitor]="osint-monitor"
  [corporatetraveldc-pi5-route-impact]="route-impact"
  [corporatetraveldc-pi5-secondbrain-daily]="secondbrain-daily"
  [corporatetraveldc-pi5-secondbrain-weekly]="secondbrain-weekly"
  [corporatetraveldc-pi5-tfr-enrichment]="tfr-enrichment"
  [corporatetraveldc-pi5-trains-yachts-daily-watch]="trains-yachts-daily-watch"
  [corporatetraveldc-pi5-transport-digest]="transport-digest"
  [corporatetraveldc-pi5-weekly-summary]="weekly-summary"
  [corporatetraveldc-pi5-chat]="chat"
)

assert_modelfile_integrity() {  # GUARD 0 -- unchanged from the old build path
  local suffix="$1" name="$2"
  local relpath="corporatetraveldc.${suffix}"
  if ! "${REPO_DIR}/scripts/verify-manifest.sh" "${relpath}"; then
    echo "  XX BLOCKED: ${name}'s Modelfile (${relpath}) failed the signed-manifest"
    echo "     integrity check -- refusing to compare a Modelfile that doesn't"
    echo "     match its signed hash. Run scripts/sign-manifest.sh after"
    echo "     reviewing/approving the change, or investigate why it doesn't match."
    exit 5
  fi
}

# Extracts the Modelfile's SYSTEM """...""" block and diffs it
# (whitespace-normalized on both sides) against
# common.personas.build_system_prompt(suffix). Prints MATCH/DRIFT/ERROR;
# returns non-zero on DRIFT or ERROR so the caller can track a failure
# count without parsing printed text.
verify_one() {
  local name="$1" suffix="$2"
  local modelfile="${REPO_DIR}/corporatetraveldc.${suffix}"
  [ -f "$modelfile" ] || { echo "XX missing Modelfile: ${modelfile}"; return 2; }
  assert_modelfile_integrity "$suffix" "$name"

  PYTHONPATH="${REPO_DIR}/src" python3 - "$modelfile" "$suffix" "$name" <<'PYEOF'
import re
import sys

modelfile_path, suffix, name = sys.argv[1], sys.argv[2], sys.argv[3]

from common.personas import PERSONAS, build_system_prompt

if suffix not in PERSONAS:
    print(f"  XX ERROR: {name} -- no PERSONAS[{suffix!r}] entry in common/personas.py")
    sys.exit(2)

text = open(modelfile_path).read()
m = re.search(r'SYSTEM\s*"""(.*)"""', text, re.DOTALL)
if not m:
    print(f"  XX ERROR: {name} -- could not find a SYSTEM \"\"\"...\"\"\" block in {modelfile_path}")
    sys.exit(2)

modelfile_system = m.group(1).strip()
persona_system = build_system_prompt(suffix).strip()

# Whitespace-normalized comparison (collapse runs of whitespace) -- this
# catches real content drift (added/removed/reworded sentences) without
# false-positiving on incidental reformatting (trailing spaces, a
# rewrapped line) that changes no actual meaning.
norm = lambda s: re.sub(r"\s+", " ", s)
if norm(modelfile_system) == norm(persona_system):
    print(f"  OK MATCH: {name}")
    sys.exit(0)
else:
    print(f"  XX DRIFT: {name} -- Modelfile SYSTEM block and personas.py's "
          f"build_system_prompt({suffix!r}) no longer match. Reconcile by hand -- "
          f"see personas.py's own module docstring for why there's no automated sync.")
    sys.exit(1)
PYEOF
}

declare -a TO_CHECK=()
if [ "$#" -gt 0 ]; then
  for arg in "$@"; do
    if [ -n "${MODELS[$arg]:-}" ]; then TO_CHECK+=("$arg")
    else echo "XX unknown model: ${arg} (valid: ${!MODELS[*]})"; exit 2; fi
  done
else
  TO_CHECK=("${!MODELS[@]}")
fi

FAILED=0
for name in "${TO_CHECK[@]}"; do
  verify_one "$name" "${MODELS[$name]}" || FAILED=1
done

echo ""
if [ "$FAILED" = "1" ]; then
  echo "⛔ One or more Modelfile/personas.py pairs have drifted -- see DRIFT lines above."
  exit 4
fi

echo "All checked Modelfile/personas.py pairs match."

# 2026-08-11: deployment trigger for the doc-drift check -- same script the
# post-commit hook uses, fired here too since a persona/prompt change is
# exactly the kind of deploy docs might describe without necessarily being
# its own commit.
"${REPO_DIR}/scripts/post-commit-doc-verify.sh" deploy 2>/dev/null || true
