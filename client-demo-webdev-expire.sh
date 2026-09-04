#!/usr/bin/env bash
# client-demo-webdev-expire.sh -- generalized 2026-09-03 from an earlier
# one-off client-specific expiry script (that one stays put, untouched,
# still serving its own already-live instance).
#
# One-shot: removes the time-limited "webdev" Basic Auth credential from
# a client demo's .htpasswd (owner's credential is untouched, stays
# permanent). Fired by corporatetraveldc-client-demo-webdev-expiry@<slug>.timer,
# 7 days after that timer instance was started.
#
# Usage: client-demo-webdev-expire.sh <slug>
set -euo pipefail

SLUG="${1:?usage: client-demo-webdev-expire.sh <slug>}"
HTPASSWD="/home/corporatetraveldc/demos/${SLUG}/auth/.htpasswd"

if [[ ! -f "$HTPASSWD" ]]; then
    echo "no .htpasswd for slug '${SLUG}' at ${HTPASSWD} -- nothing to do." >&2
    exit 0
fi

if grep -q "^webdev:" "$HTPASSWD" 2>/dev/null; then
    sed -i '/^webdev:/d' "$HTPASSWD"
    systemctl --user restart "corporatetraveldc-client-demo@${SLUG}.service"
    echo "webdev credential removed for '${SLUG}' and container restarted."
else
    echo "webdev credential already absent for '${SLUG}' -- nothing to do."
fi
