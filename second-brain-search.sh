#!/usr/bin/env bash
# scripts/second-brain-search.sh -- query the local second-brain FTS5 index
# the same way the platform's own skills do, without hand-rolling
# NEXTCLOUD_ADMIN_USER/PYTHONPATH/quoting every time.
#
# Usage:
#   scripts/second-brain-search.sh QUERY               # phrase-safe (default)
#   scripts/second-brain-search.sh --raw QUERY          # raw FTS5 syntax (AND/OR/col:)
#   scripts/second-brain-search.sh --summary
#   scripts/second-brain-search.sh --backlinks TARGET
#
# Semantic modes (2026-08-18) -- route to second_brain.semantic instead of the
# literal FTS5 index. A literal search only finds the exact spelling the query
# used, and this vault writes the same concept several ways
# (advanced_air_mobility / aam / evtol / vertiport), so a literal search
# silently under-returns. --semantic expands the query to every known surface
# form first and prints both counts so the difference is visible:
#   scripts/second-brain-search.sh --semantic QUERY     # concept-expanded search
#   scripts/second-brain-search.sh --resolve TERM       # what concept is this?
#   scripts/second-brain-search.sh --expand TERM        # all equivalent spellings
#   scripts/second-brain-search.sh --concepts           # vocabulary size/shape
#   scripts/second-brain-search.sh --drift              # governance backlog
#
# These modes need no Nextcloud credentials (the semantic layer reads the local
# index and the ontology file only), so the NEXTCLOUD_ADMIN_USER requirement
# below is enforced only for the paths that actually reach index_db.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

export PYTHONPATH="${REPO_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

# ── Semantic modes: no credentials needed, handled before the env gate ────────
case "${1:-}" in
    --semantic)
        shift; exec python3 -m second_brain.semantic --search "$*" ;;
    --resolve)
        shift; exec python3 -m second_brain.semantic --resolve "$*" ;;
    --expand)
        shift; exec python3 -m second_brain.semantic --expand "$*" ;;
    --concepts)
        exec python3 -m second_brain.semantic --stats ;;
    --drift)
        exec python3 -m second_brain.semantic --drift ;;
esac

ENV_FILE="/etc/corporatetraveldc/dispatch.env"
if [[ -z "${NEXTCLOUD_ADMIN_USER:-}" && -r "${ENV_FILE}" ]]; then
    NEXTCLOUD_ADMIN_USER="$(sed -nE 's/^NEXTCLOUD_ADMIN_USER=(.*)$/\1/p' "${ENV_FILE}" | tail -1)"
fi
: "${NEXTCLOUD_ADMIN_USER:?NEXTCLOUD_ADMIN_USER not set and not found in ${ENV_FILE}}"

export NEXTCLOUD_ADMIN_USER

if [[ "${1:-}" == "--summary" || "${1:-}" == "--scan" ]]; then
    exec python3 -m second_brain.index_db "$@"
fi
if [[ "${1:-}" == "--backlinks" ]]; then
    exec python3 -m second_brain.index_db "$@"
fi
if [[ "${1:-}" == "--raw" ]]; then
    shift
    exec python3 -m second_brain.index_db --raw --search "$*"
fi

exec python3 -m second_brain.index_db --search "$*"
