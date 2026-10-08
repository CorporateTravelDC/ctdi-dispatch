#!/usr/bin/env bash
# scripts/research/airline-time-fidelity-collect.sh -- host wrapper for the
# read-only airline time-fidelity research collector (2026-10-08,
# docs/research/AIRLINE_TIME_FIDELITY_SPEC.md). Runs the collector inside the
# web container (database access, nothing written there) and appends its JSONL
# to a dated file. No alerts: this is a research monitor.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${ATF_OUT_DIR:-/var/lib/corporatetraveldc/research/airline-time-fidelity}"
mkdir -p "$OUT_DIR"
day=$(date -u +%Y-%m-%d)
tmp=$(mktemp "$OUT_DIR/.run.XXXXXX")
trap 'rm -f "$tmp"' EXIT
# Bellwether flights are private (watched flights = client travel patterns): one
# callsign per line in docs/research/private/bellwethers.txt, dropped from the public mirror.
bw=""
if [[ -f "$REPO/docs/research/private/bellwethers.txt" ]]; then
  bw=$(grep -Eo '^[A-Z]{3}[0-9]{1,4}[A-Z]?' "$REPO/docs/research/private/bellwethers.txt" | paste -sd, -)
fi
podman exec -i -e "ATF_BELLWETHERS=${bw}" systemd-corporatetraveldc-web python3 - \
  < "$REPO/scripts/research/airline_time_fidelity_collect.py" > "$tmp"
cat "$tmp" >> "$OUT_DIR/$day.jsonl"
# rolling 30/60/90-day summaries (private ledger + public anchors), rebuilt every run
python3 "$REPO/scripts/research/airline_time_fidelity_rollup.py" "$OUT_DIR"
echo "airline-time-fidelity: $(grep -c '"kind": "flight"' "$tmp") flight rows -> $OUT_DIR/$day.jsonl"
