#!/usr/bin/env bash
# scripts/agent-pamphlet-weekly-refresh.sh
#
# Re-renders and re-publishes every cloud agent account's vault PAMPHLET.md
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
# On-box accounts (Claude Code, Codex, llama) are NOT covered here:
# `render-onboarding.sh <account> --install` needs root, and this unit runs
# as the operator user, not root. Those three still need a periodic,
# operator-run (or a separate root-run unit, not built here) re-install.
#
# Usage: scripts/agent-pamphlet-weekly-refresh.sh   # called by the timer
set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

CLOUD_ACCOUNTS=(ctdc-agent-anthropic-cowork ctdc-agent-openai-codex ctdc-agent-openai-chatgpt)
ON_BOX_ACCOUNTS=(ctdc-agent-anthropic-claude ctdc-agent-llama)

echo "[agent-pamphlet-weekly-refresh] $(date -u '+%Y-%m-%dT%H:%M:%SZ') start"

fail=0
for acct in "${CLOUD_ACCOUNTS[@]}"; do
    if scripts/with-dispatch-env.sh env PYTHONPATH="${REPO_DIR}/src" bash \
        scripts/agent-segmentation/publish-pamphlet-to-vault.sh "${acct}"; then
        :
    else
        echo "XX failed to publish pamphlet for ${acct}" >&2
        fail=1
    fi
done

echo "[agent-pamphlet-weekly-refresh] on-box accounts (${ON_BOX_ACCOUNTS[*]}) are not installed by this job -- it has no root. Run, as the operator, for each:"
for acct in "${ON_BOX_ACCOUNTS[@]}"; do
    echo "    sudo scripts/agent-segmentation/render-onboarding.sh ${acct} --install"
done

echo "[agent-pamphlet-weekly-refresh] $(date -u '+%Y-%m-%dT%H:%M:%SZ') done (exit ${fail})"
exit "${fail}"
