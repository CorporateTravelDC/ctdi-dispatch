#!/usr/bin/env bash
# scripts/sign-manifest.sh
# Generates and GPG-signs a whole-repo-tree integrity manifest -- the source
# of truth scripts/verify-manifest.sh checks any covered file against before
# letting it run. See docs/COMPLIANCE_SECURITY.md's "Signed Manifest
# Integrity" section for the full design and threat model.
#
# This is a DELIBERATE, human-run step -- same posture as this repo's signed
# commits (git config commit.gpgsign=true): nothing re-signs the manifest
# automatically. Run this after making changes you're ready to trust, as
# part of the same review/commit pass, using your own GPG passphrase.
#
# 2026-08-20: --agent mode. A second, no-passphrase GPG key exists
# specifically for agent-driven signing (see CLAUDE.md "Agent signing" and
# docs/COMPLIANCE_SECURITY.md) -- deliberately a SEPARATE key from the
# operator's own, so a signature's key ID is itself an honest audit trail
# of who/what produced it, instead of both being indistinguishable
# artifacts of one shared key + a shared gpg-agent cache (the gap that
# motivated this whole change -- see the second-brain note from this date).
# --agent mode never touches a passphrase (the agent key has none, by
# design -- a fake secret an agent could also read isn't real security) and
# is gated instead on an explicit human control: either an active
# session_grant (scripts/grant-agent-session.sh, human-run only) or, absent
# one, a live ntfy Allow/Deny round-trip via the existing
# scripts/sudo-approval-gate.sh. Every individual agent signature is
# db.audit()'d regardless of which path let it through.
#
# Usage:
#   scripts/sign-manifest.sh              # operator key, your own passphrase
#   scripts/sign-manifest.sh --agent       # agent key, grant-or-approval gated
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

AGENT_MODE=0
if [[ "${1:-}" == "--agent" ]]; then
    AGENT_MODE=1
fi

ENV_FILE="security/signing.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "XX Missing ${ENV_FILE} -- copy security/signing.env.example, fill in your" >&2
    echo "   own SIGNING_KEY_FINGERPRINT, and re-run." >&2
    exit 2
fi
# shellcheck source=/dev/null
source "${ENV_FILE}"
: "${SIGNING_KEY_FINGERPRINT:?SIGNING_KEY_FINGERPRINT not set in ${ENV_FILE}}"
if [[ "${SIGNING_KEY_FINGERPRINT}" == "0000000000000000000000000000000000000000" ]]; then
    echo "XX ${ENV_FILE} still has the placeholder fingerprint -- fill in your real one." >&2
    exit 2
fi

if [[ "${AGENT_MODE}" -eq 1 ]]; then
    : "${AGENT_SIGNING_KEY_FINGERPRINT:?AGENT_SIGNING_KEY_FINGERPRINT not set in ${ENV_FILE} -- add it (see CLAUDE.md 'Agent signing')}"
fi

# CLAUDE.md drift gate (2026-08-19) -- a change pass cannot complete with
# CLAUDE.md stale. --pre-sign skips the checker's manifest-vs-signature
# check (check 8): that check would otherwise flag the exact stale-signature
# state this script exists to fix, before it's had a chance to fix it. Runs
# before anything below is generated or signed, so a blocked run leaves no
# partial manifest.
DRIFT_CHECKER="scripts/check-claude-md-drift.sh"
if [[ -n "${SKIP_DRIFT_CHECK:-}" ]]; then
    echo "############################################################" >&2
    echo "## SKIP_DRIFT_CHECK=1 -- CLAUDE.md drift gate bypassed.    ##" >&2
    echo "## Whatever ${DRIFT_CHECKER} would have found was NOT      ##" >&2
    echo "## checked. This is not the default; unset it to restore  ##" >&2
    echo "## the gate. Signing will proceed against possibly-stale   ##" >&2
    echo "## documentation.                                          ##" >&2
    echo "############################################################" >&2
elif [[ ! -x "${DRIFT_CHECKER}" ]]; then
    echo "!! ${DRIFT_CHECKER} missing or not executable -- CLAUDE.md drift gate" >&2
    echo "   cannot run, continuing without it. Install it (see CLAUDE.md's" >&2
    echo "   header) so signing passes stop skipping doc-drift verification." >&2
else
    set +e
    DRIFT_OUTPUT="$("${DRIFT_CHECKER}" --pre-sign 2>&1)"
    DRIFT_RC=$?
    set -e
    if [[ ${DRIFT_RC} -ne 0 ]]; then
        echo "${DRIFT_OUTPUT}" >&2
        echo "XX CLAUDE.md drift found (above) -- signing pass BLOCKED." >&2
        echo "   Reconcile CLAUDE.md against live state, or re-run with" >&2
        echo "   SKIP_DRIFT_CHECK=1 to sign anyway (not recommended)." >&2
        exit 3
    fi
    echo "[sign-manifest] [OK] ${DRIFT_CHECKER} --pre-sign: no drift."
fi

MANIFEST="MANIFEST.sha256"
SIGNATURE="MANIFEST.sha256.asc"

echo "[sign-manifest] Hashing every tracked AND untracked-but-not-ignored file (true whole-repo-tree coverage)..."
# --cached --others --exclude-standard: tracked files PLUS untracked ones
# that aren't .gitignore'd -- NOT just `git ls-files` alone (tracked only).
# Bug found 2026-08-09: every file added this session before being `git add`ed
# -- including sign-manifest.sh/verify-manifest.sh/verified-exec.sh
# themselves -- was silently absent from every manifest generated so far,
# and worse, the COLLECTIVE check (sha256sum -c) can only notice a
# *modified* file it already knows about; it can't notice an entirely new
# file existing at all, since it only iterates the manifest's own known
# list. `git add`-only coverage means "new file added by an attacker" was
# invisible to the one check meant to catch exactly that. .git internals,
# build artifacts, and anything actually .gitignore'd are still excluded
# (via --exclude-standard), same as before.
#
# 2026-08-18: docs/LIVE_STATE_CHECK_*.md excluded the same way MANIFEST
# itself is. These are written autonomously by
# scripts/post-commit-doc-verify.sh's background Fable pass after every
# major commit (its own prompt hard-rules it to NEVER stage/commit what it
# writes), so today's file is routinely sitting modified/untracked at the
# exact moment someone runs this script -- every prior night this showed
# up as manifest "drift" that had to be manually explained away as
# harmless each time. They're real historical docs (every one gets
# committed eventually) and genuinely worth keeping, but they're
# informational notes, not security-relevant code/config, so they don't
# belong in the integrity baseline at all -- same reasoning as excluding
# MANIFEST.sha256/.asc from their own hash list.
#
# 2026-08-19: docs/CLAUDE_MD_DRIFT_REPORT.md excluded for the identical
# reason -- corporatetraveldc-claude-md-drift-daily overwrites it every
# 05:15 ET run, so it would otherwise show as manifest drift after every
# single day's backstop, same class of noise as LIVE_STATE_CHECK above.
TMP_MANIFEST="$(mktemp "${MANIFEST}.XXXXXX")"
# 2026-08-20: MANIFEST.sha256 itself now goes through the same temp-file/
# rename-on-full-success pattern the signature got on 2026-08-19 --
# generating straight into MANIFEST.sha256 (as before) left it regenerated
# and paired with the OLD signature the instant hashing finished, even if
# everything after that (the drift gate, a headless gpg prompt, an
# AGENT_MODE grant lookup) then failed. `set -e` would abort the script
# but MANIFEST.sha256 stayed changed, so the very next scheduled-
# integrity-sweep.sh run (or any verified-exec.sh-gated skill) hit a
# genuine crypto mismatch -- "BAD signature", not just "stale" -- against
# a signature made for content that no longer existed on disk. Traced
# live to exactly this: MANIFEST.sha256's mtime was ~6.5h newer than
# MANIFEST.sha256.asc's, both from this same session's sign-manifest.sh
# testing, which cascaded into 13 unrelated-looking skill-timer failures
# simultaneously (see CLAUDE.md Known bad). Both files now only ever
# change together, atomically, on a fully successful run -- clean up the
# trap below fires on any early exit and leaves the real MANIFEST/
# SIGNATURE pair exactly as they were.
trap 'rm -f "${TMP_MANIFEST}" "${TMP_SIGNATURE:-}"' EXIT
# 2026-08-20: exclude the whole MANIFEST.sha256* family, not just the two
# exact final names. The old exact-match-only exclusion let a STRAY mktemp
# temp file (MANIFEST.sha256.XXXXXX / MANIFEST.sha256.asc.XXXXXX, left
# behind by any concurrent or previously-killed run whose trap didn't get
# a chance to fire) get picked up by `git ls-files --others` as an
# untracked file and baked into the manifest as a real entry -- then, once
# that stray file was inevitably cleaned up by its own owning process, the
# next `verify-manifest.sh` failed with "No such file or directory" for a
# file that was never a real part of the repo. Caught live the first time
# --agent produced a real signed manifest this session.
git ls-files --cached --others --exclude-standard -z \
    | grep -zvE "^(MANIFEST\.sha256(\..*)?|docs/LIVE_STATE_CHECK_[0-9-]+\.md|docs/CLAUDE_MD_DRIFT_REPORT\.md)\$" \
    | xargs -0 sha256sum \
    | sort -k2 \
    > "${TMP_MANIFEST}"

echo "[sign-manifest] $(wc -l < "${TMP_MANIFEST}") files covered."

if [[ "${AGENT_MODE}" -eq 1 ]]; then
    ACTIVE_KEY="${AGENT_SIGNING_KEY_FINGERPRINT}"
    GRANT_PATTERN="sign-manifest:agent-key"
    GRANT_JSON="$(PYTHONPATH=src python3 -c "
import json
from common import db
g = db.get_active_session_grant('${GRANT_PATTERN}')
print(json.dumps(g) if g else '')
" 2>/dev/null || true)"
    if [[ -n "${GRANT_JSON}" ]]; then
        GRANT_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "${GRANT_JSON}")"
        echo "[sign-manifest] agent mode: active session_grant ${GRANT_ID} covers this pattern -- signing without an approval round-trip."
        PYTHONPATH=src python3 -c "
from common import db
db.audit('agent_sign_manifest', 'admin', None, None, {'grant_id': '${GRANT_ID}', 'via': 'session_grant'})
"
    else
        echo "[sign-manifest] agent mode: no active session_grant for '${GRANT_PATTERN}' -- requesting one-off approval."
        PYTHONPATH=src python3 -c "
from common import db
db.audit('agent_sign_manifest', 'admin', None, None, {'via': 'approval_gate_pending'})
"
        scripts/sudo-approval-gate.sh "${GRANT_PATTERN}" \
            "sign-manifest.sh --agent: signing the repo-tree integrity manifest with the agent key" \
            -- /bin/true
        # sudo-approval-gate.sh only reports allow/deny for the placeholder
        # `/bin/true` above -- the actual signing happens below, in this
        # process, so the same $TMP_SIGNATURE/mv-on-success safety applies
        # to the agent path too. If the gate denied, it already exited
        # non-zero and `set -e` stopped this script before reaching here.
        echo "[sign-manifest] approval granted -- proceeding to sign."
    fi
else
    ACTIVE_KEY="${SIGNING_KEY_FINGERPRINT}"
    echo "[sign-manifest] Signing with key ${ACTIVE_KEY} (you'll be prompted for your passphrase)..."
fi

# 2026-08-19: sign into a temp file and only replace SIGNATURE on success --
# the old `rm -f "${SIGNATURE}"` followed by an unconditional gpg call
# deleted the previous, still-valid signature BEFORE attempting the new
# one. In a headless/non-interactive shell (no /dev/tty for the passphrase
# prompt) gpg exits non-zero every time, `set -e` aborts the script right
# there, and the tree is left with no valid signature at all -- discovered
# live when repeated headless sign attempts corrupted MANIFEST.sha256.asc
# to 0 bytes twice in the same session, silently turning "stale, needs
# re-signing" (normal, expected) into "SIGNATURE INVALID" (looks like
# tampering). Recovered via `git checkout -- MANIFEST.sha256.asc`, which
# only works because nothing had been committed yet -- this fix makes that
# recovery step unnecessary going forward.
TMP_SIGNATURE="$(mktemp "${SIGNATURE}.XXXXXX")"
# --yes: mktemp above already created TMP_SIGNATURE (empty) to reserve the
# name atomically -- without --yes, gpg's -o refuses to silently overwrite
# an existing file and prompts "File '...' exists. Overwrite? (y/N)" on
# every single run. Safe to force here: TMP_SIGNATURE is a fresh mktemp
# path this script owns exclusively, never a file a user could care about.
if gpg --local-user "${ACTIVE_KEY}" --detach-sign --armor --yes \
       -o "${TMP_SIGNATURE}" "${TMP_MANIFEST}"; then
    mv "${TMP_MANIFEST}" "${MANIFEST}"
    mv "${TMP_SIGNATURE}" "${SIGNATURE}"
else
    rm -f "${TMP_SIGNATURE}" "${TMP_MANIFEST}"
    echo "[sign-manifest] FAILED -- gpg did not produce a signature (see error above)." >&2
    echo "[sign-manifest] ${MANIFEST} + ${SIGNATURE} left untouched -- no valid pair was destroyed." >&2
    exit 1
fi

echo "[sign-manifest] OK -- ${MANIFEST} + ${SIGNATURE} written, signed by ${ACTIVE_KEY}."
echo "[sign-manifest] Verify with: scripts/verify-manifest.sh"
echo "[sign-manifest] Remember to commit both files alongside the changes they cover."
