#!/usr/bin/env bash
# scripts/agent-pamphlet-weekly-refresh.sh
#
# Re-renders and re-publishes every activated agent/service account's vault PAMPHLET.md
# from the CURRENT, already-committed scripts/agent-segmentation/
# onboarding-template.md + docs/glossary.yaml. This job does not edit any
# tracked file -- a template or glossary content change is still a
# reviewed, signed commit, same as any other code change. What this job
# does is the last mile: whatever landed in those two files since the last
# run gets pushed to the vault, so it reaches every cloud agent's NEXT
# invocation (each reads its pamphlet fresh via research_read every
# session -- there is no cache to bust) without anyone having to remember
# to re-run the publish step by hand after a template/glossary commit.
#
# This leaf publishes vault editions. The deployment/weekly entrypoint is
# agent-pamphlet-refresh.sh, which also requests and verifies the installed
# root-managed local delivery service. It never gives the publisher root.
#
# Usage: scripts/agent-pamphlet-weekly-refresh.sh   # called by the timer
set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

# Read the registry each run: Codex is on-box; service identities do not
# necessarily run in Anthropic's cloud. No account list is hard-coded here.
REGISTRY="${CTDC_ACCOUNTS_FILE:-/etc/ctdc-accounts.conf}"
[[ -r "$REGISTRY" ]] || { echo "account registry unreadable; nothing published" >&2; exit 66; }
mapfile -t ACCOUNTS < <(awk '$1 !~ /^#/ && ($2=="agent" || $2=="service") && $4==0 {print $1}' "$REGISTRY")
(( ${#ACCOUNTS[@]} )) || { echo "no active registry accounts; nothing published" >&2; exit 65; }
ON_BOX_ACCOUNTS=()
while read -r acct kind login preload extra; do
    [[ "$acct" =~ ^ctdc-[a-z0-9-]+$ && "$kind" == agent && "$preload" == 0 ]] || continue
    case "$login" in claude|codex|ssh) ON_BOX_ACCOUNTS+=("$acct") ;; esac
done <"$REGISTRY"

echo "[agent-pamphlet-weekly-refresh] $(date -u '+%Y-%m-%dT%H:%M:%SZ') start"

fail=0
for acct in "${ACCOUNTS[@]}"; do
    if scripts/with-dispatch-env.sh env CTDC_ACCOUNTS_FILE="$REGISTRY" PYTHONPATH="${REPO_DIR}/src" bash \
        scripts/agent-segmentation/publish-pamphlet-to-vault.sh "${acct}"; then
        :
    else
        echo "XX failed to publish pamphlet for ${acct}" >&2
        fail=1
    fi
done

echo "[agent-pamphlet-weekly-refresh] local accounts (${ON_BOX_ACCOUNTS[*]}) are delivered by corporatetraveldc-agent-pamphlet-install.service; use agent-pamphlet-refresh.sh for both channels"

echo "[agent-pamphlet-weekly-refresh] $(date -u '+%Y-%m-%dT%H:%M:%SZ') done (exit ${fail})"
exit "${fail}"
