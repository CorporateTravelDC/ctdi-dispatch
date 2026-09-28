#!/usr/bin/env bash
# scripts/sign-db-manifest.sh
# Generates (scripts/snapshot-db-manifest.py) and GPG-signs a point-in-time
# content-hash manifest of the dispatch database -- the DATA counterpart to
# scripts/sign-manifest.sh's repo-tree (CODE) manifest. Same key material,
# same agent-mode gating, same atomic-sign-into-temp-then-move safety --
# deliberately mirrors that script rather than inventing a second pattern.
# See docs/POSTGRES_MIGRATION.md and the second-brain note on the signed
# DB snapshot manifest design for the full rationale.
#
# Usage:
#   scripts/sign-db-manifest.sh          # operator key, your own passphrase
#   scripts/sign-db-manifest.sh --agent  # agent key, grant-or-approval gated
#
# 2026-09-19 addition: --manifest PATH picks which manifest file gets
# generated+signed (default manifests/DB_MANIFEST.json, unchanged); any
# args after -- are passed straight through to snapshot-db-manifest.py
# (e.g. --tables/--sqlite-db/--sqlite-only/--purpose) so this same signer
# covers the checkpoint-phase snapshots, not just the default dual-hash one.
#   scripts/sign-db-manifest.sh --agent --manifest manifests/DB_MANIFEST_phase2.json -- --tables cifp_fixes,...
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

AGENT_MODE=0
MANIFEST="manifests/DB_MANIFEST.json"
SNAPSHOT_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent) AGENT_MODE=1; shift ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --) shift; SNAPSHOT_ARGS=("$@"); break ;;
        *) echo "XX unrecognized arg: $1 (extra snapshot-db-manifest.py args go after --)" >&2; exit 2 ;;
    esac
done

ENV_FILE="security/signing.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "XX Missing ${ENV_FILE} -- see scripts/sign-manifest.sh's own header." >&2
    exit 2
fi
# shellcheck source=/dev/null
source "${ENV_FILE}"
: "${SIGNING_KEY_FINGERPRINT:?SIGNING_KEY_FINGERPRINT not set in ${ENV_FILE}}"
if [[ "${AGENT_MODE}" -eq 1 ]]; then
    : "${AGENT_SIGNING_KEY_FINGERPRINT:?AGENT_SIGNING_KEY_FINGERPRINT not set in ${ENV_FILE}}"
fi

SIGNATURE="${MANIFEST}.asc"

echo "[sign-db-manifest] snapshotting DB content (this reads every row of every migrated table, streaming -- takes a few minutes on the larger tables)..."
if ! PYTHONPATH=src python3 scripts/snapshot-db-manifest.py --out "${MANIFEST}" "${SNAPSHOT_ARGS[@]}"; then
    echo "[sign-db-manifest] FAILED -- snapshot-db-manifest.py reported a mismatch or error; NOT signing a manifest that documents a known-bad state." >&2
    echo "[sign-db-manifest] ${MANIFEST} was still written -- inspect it (mismatched_tables) before deciding how to proceed." >&2
    exit 1
fi

if [[ "${AGENT_MODE}" -eq 1 ]]; then
    ACTIVE_KEY="${AGENT_SIGNING_KEY_FINGERPRINT}"
    GRANT_PATTERN="sign-db-manifest:agent-key"
    GRANT_JSON="$(PYTHONPATH=src python3 -c "
import json
from common import db
g = db.get_active_session_grant('${GRANT_PATTERN}')
print(json.dumps(g) if g else '')
" 2>/dev/null || true)"
    if [[ -n "${GRANT_JSON}" ]]; then
        GRANT_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "${GRANT_JSON}")"
        echo "[sign-db-manifest] agent mode: active session_grant ${GRANT_ID} covers this pattern -- signing without an approval round-trip."
        PYTHONPATH=src python3 -c "
from common import db
db.audit('agent_sign_db_manifest', 'admin', None, None, {'grant_id': '${GRANT_ID}', 'via': 'session_grant'})
"
    else
        echo "[sign-db-manifest] agent mode: no active session_grant for '${GRANT_PATTERN}' -- requesting one-off approval."
        PYTHONPATH=src python3 -c "
from common import db
db.audit('agent_sign_db_manifest', 'admin', None, None, {'via': 'approval_gate_pending'})
"
        scripts/sudo-approval-gate.sh "${GRANT_PATTERN}" \
            "sign-db-manifest.sh --agent: signing the pre-cutover DB content manifest with the agent key" \
            -- /bin/true
        echo "[sign-db-manifest] approval granted -- proceeding to sign."
    fi
else
    ACTIVE_KEY="${SIGNING_KEY_FINGERPRINT}"
    echo "[sign-db-manifest] Signing with key ${ACTIVE_KEY} (you'll be prompted for your passphrase)..."
fi

TMP_SIGNATURE="$(mktemp "${SIGNATURE}.XXXXXX")"
if gpg --local-user "${ACTIVE_KEY}" --detach-sign --armor --yes \
       -o "${TMP_SIGNATURE}" "${MANIFEST}"; then
    mv "${TMP_SIGNATURE}" "${SIGNATURE}"
else
    rm -f "${TMP_SIGNATURE}"
    echo "[sign-db-manifest] FAILED -- gpg did not produce a signature (see error above)." >&2
    echo "[sign-db-manifest] ${MANIFEST} was written but is UNSIGNED -- do not treat it as a trusted baseline yet." >&2
    exit 1
fi

echo "[sign-db-manifest] OK -- ${MANIFEST} + ${SIGNATURE} written, signed by ${ACTIVE_KEY}."
echo "[sign-db-manifest] Remember to commit both files."
