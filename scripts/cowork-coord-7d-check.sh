#!/usr/bin/env bash
# scripts/cowork-coord-7d-check.sh
# Belt-and-suspenders backup for the weekly human presence re-attestation
# that gates Cowork's daily board-write token refresh chain (see
# scripts/board-presence-attest.sh -- "run it yourself on a ~7-day cadence,
# whenever a reminder fires (or proactively)" -- this IS that reminder).
#
# 2026-09-02 (operator directive): does NOT and must NEVER attempt to run
# board-presence-attest.sh itself -- that's a deliberate, human-run step
# requiring the operator's own GPG passphrase, same posture as
# sign-manifest.sh. This script only:
#   1. Checks real remaining validity via db.board_presence_status().
#   2. Posts a "coord" thread board message (db.board_insert) so Cowork
#      sees the current presence state on its next wake/poll, independent
#      of whether the operator has acted yet.
#   3. Sends the operator an ntfy+email reminder to run
#      scripts/board-presence-attest.sh themselves.
#
# Run via corporatetraveldc-cowork-coord-7d.timer (systemd --user), with
# RandomizedDelaySec giving a grace window for the operator's actual
# schedule.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

# Same host-vs-container ntfy_url() gotcha as cowork-coord-24h-check.sh.
export NTFY_URL="http://127.0.0.1:2586"

PYTHONPATH=src python3 - <<'PY'
import time
from datetime import datetime, timezone
from common import db, config, ntfy_push

status = db.board_presence_status()
now = time.time()

if status["valid"]:
    remaining_h = round((status["valid_until"] - now) / 3600, 1)
    state_line = f"Current attestation still valid, ~{remaining_h}h remaining."
    priority = 2
else:
    state_line = "No valid attestation on record -- refresh chain is (or will soon be) failing closed."
    priority = 4

body = (
    f"Weekly presence re-attestation checkpoint. {state_line} "
    "Run scripts/board-presence-attest.sh yourself (real GPG passphrase, "
    "not automatable) to re-anchor the week's autonomous daily-refresh chain."
)
rec = db.board_insert(
    from_side="dispatch", to_side="cowork", thread="coord",
    subject="7d presence-attestation checkpoint", body=body,
)
print(f"posted board coord message id={rec['id']} seq={rec['seq']}")

ntfy_push.send(
    "ops-health",
    f"{body}\n\nBoard message: {rec['id']}",
    title="Cowork: weekly presence attestation due",
    priority=priority,
    tags="handshake,key",
    email=True,
)
PY
