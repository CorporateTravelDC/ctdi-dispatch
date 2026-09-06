#!/usr/bin/env bash
# scripts/cowork-coord-24h-check.sh
# Belt-and-suspenders backup for the Cowork daily board-write token refresh
# (db.board_refresh_token, ~24h TTL -- see db.py's _BOARD_TOKEN_TTL_S and
# scripts/board-presence-attest.sh's docstring for the full chain).
#
# 2026-09-02 (operator directive): Cowork's own automation is expected to
# reach out directly (a cross-session coordination request ~6h before its
# token expires) for a human approval-gate on the refresh -- this script is
# the INDEPENDENT backup in case that direct ping doesn't land. It does two
# things, neither of which replaces the direct-ping path:
#   1. Posts a "coord" thread message on the board itself (db.board_insert)
#      so Cowork sees a checkpoint from this side the next time it wakes
#      and polls GET /api/v1/board?thread=coord, regardless of whether its
#      own outbound ping worked.
#   2. Sends the operator an ntfy+email heads-up, so a missed direct ping
#      is still visible even if this session isn't actively watched.
#
# Does NOT attempt to refresh the token itself -- that stays gated on a
# real approval decision, same as the direct-ping path.
#
# Run via corporatetraveldc-cowork-coord-24h.timer (systemd --user), with
# RandomizedDelaySec giving a grace window for days the operator isn't
# actively on the Cowork side.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

# This runs on the bare host (systemd --user), not inside a container --
# config.ntfy_url()'s default resolves to host.containers.internal, which
# is only reachable from inside a container (same gotcha documented in
# container-mem-watch.sh/swim-session-health-watch.sh). Override for this
# process only.
export NTFY_URL="http://127.0.0.1:2586"

PYTHONPATH=src python3 - <<'PY'
import time
from common import db, config, ntfy_push

now = time.time()
body = (
    "24h daily board-write token refresh checkpoint (belt-and-suspenders "
    "backup post -- Cowork's own direct coordination ping is the primary "
    "path). If a refresh approval request hasn't already been handled via "
    "direct coordination, check in here."
)
rec = db.board_insert(
    from_side="dispatch", to_side="cowork", thread="coord",
    subject="24h refresh checkpoint", body=body,
)
print(f"posted board coord message id={rec['id']} seq={rec['seq']}")

ntfy_push.send(
    "ops-health",
    f"{body}\n\nBoard message: {rec['id']}",
    title="Cowork: 24h refresh checkpoint",
    priority=2,
    tags="handshake",
    email=True,
)
PY
