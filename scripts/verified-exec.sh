#!/usr/bin/env bash
# scripts/verified-exec.sh -- run a scoped signed-manifest integrity check
# (docs/COMPLIANCE_SECURITY.md "Signed Manifest Integrity"), then exec the
# real command only if it passes.
#
# Prepended to every skill container's Exec= line (instead of editing each
# of the ~25+ skill .py files individually to add its own self-check): one
# wrapper, one place to maintain.
#
# Deliberately scoped to src/ + the specific files this image's Containerfile
# actually bakes in (scripts/verify-manifest.sh, scripts/verified-exec.sh,
# security/trusted-signing-key.pub.asc) -- NOT the full unscoped collective
# check. First version used the unscoped check and it always failed inside
# a real container: these images intentionally bake in only a SUBSET of the
# repo (src/ + a handful of files), so an unscoped check immediately hit
# hundreds of "file not found" failures for docs/, nginx/, watchlists/, etc.
# that were never meant to be present. Caught by testing before this ever
# reached a live container.
#
# Usage (as a quadlet Exec= line):
#   Exec=scripts/verified-exec.sh python3 poller/skills/some_skill.py
#
# On failure: prints a clear message and exits 1 WITHOUT running the real
# command at all -- systemd marks the unit failed, same as any other
# startup failure, so it surfaces exactly like any other broken run (no
# silent skip).
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"

# Captured, not streamed -- the success case ("OK -- signature valid...")
# would otherwise repeat identically in every skill's log on every run.
# Only shown when it actually failed, when it's the useful part.
_check_output="$("${REPO_ROOT}/scripts/verify-manifest.sh" \
    "src/" "scripts/verify-manifest.sh" "scripts/verified-exec.sh" \
    "security/trusted-signing-key.pub.asc" 2>&1)"
if [[ $? -ne 0 ]]; then
    echo "verified-exec: INTEGRITY CHECK FAILED -- refusing to run: $*" >&2
    echo "${_check_output}" >&2
    exit 1
fi

exec "$@"
