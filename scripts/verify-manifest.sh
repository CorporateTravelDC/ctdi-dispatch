#!/usr/bin/env bash
# scripts/verify-manifest.sh
# Verifies the signed whole-repo-tree integrity manifest (MANIFEST.sha256 +
# MANIFEST.sha256.asc, see scripts/sign-manifest.sh and
# docs/COMPLIANCE_SECURITY.md's "Signed Manifest Integrity" section).
#
# Two modes, same underlying check:
#   scripts/verify-manifest.sh                     # collective: every
#                                                   # covered file, like an
#                                                   # ISO's sha256sum -c --
#                                                   # for validating a fresh
#                                                   # clone/install is a
#                                                   # genuine, untampered
#                                                   # copy of the WHOLE repo.
#   scripts/verify-manifest.sh <target> [<target>..] # one or more targets,
#                                                   # each an exact file
#                                                   # ("scripts/foo.sh") or a
#                                                   # directory prefix
#                                                   # ("src/", matching every
#                                                   # manifest entry under
#                                                   # it). A single exact
#                                                   # file is the common
#                                                   # "check just myself"
#                                                   # runtime-guard case;
#                                                   # multiple/mixed targets
#                                                   # cover containers that
#                                                   # intentionally bake in
#                                                   # only a SUBSET of the
#                                                   # repo (e.g. src/ + a few
#                                                   # security files): a full
#                                                   # collective check inside
#                                                   # one would fail on every
#                                                   # manifest entry that was
#                                                   # never meant to be
#                                                   # present there (docs/,
#                                                  # nginx/, watchlists/,
#                                                  # etc.) -- this checks
#                                                  # only what's actually
#                                                  # supposed to be there.
#
# Both first verify MANIFEST.sha256.asc's signature against
# security/trusted-signing-key.pub.asc using an ISOLATED keyring -- never the
# caller's ambient GPG keyring, so verification never depends on (or can be
# confused by) whatever else happens to already be trusted/imported there.
# The signing key's fingerprint is then asserted against
# security/signing.env's SIGNING_KEY_FINGERPRINT/AGENT_SIGNING_KEY_FINGERPRINT
# (2026-08-25 fix, Opus blind review C-4) -- verifying against "whatever
# key is in the tracked pubkey file" alone trusts exactly the adversary
# this manifest is meant to defend against: someone who can write tracked
# files can replace trusted-signing-key.pub.asc with their own key and
# re-sign MANIFEST.sha256, and the old check would have passed it clean.
#
# Exit 0 = verified clean. Any non-zero = do not trust the file(s); the
# caller must refuse to proceed. Threat model honesty: this catches
# on-disk tampering (a compromised account, a bad deploy, disk corruption)
# between signing and execution -- it does not defend against someone who
# already has this signing key's private half and passphrase (the same
# trust boundary as this repo's signed git commits).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

MANIFEST="MANIFEST.sha256"
SIGNATURE="MANIFEST.sha256.asc"
PUBKEY="security/trusted-signing-key.pub.asc"
SIGNING_ENV="security/signing.env"
# 2026-10-04 (duel C2): a ROOT-OWNED pin outside the operator-writable tree
# takes precedence over security/signing.env. Create it once (runbook):
#   sudo install -m 0644 -o root -g root /dev/null /etc/corporatetraveldc/signing-pin
#   grep -E '^(SIGNING_KEY_FINGERPRINT|AGENT_SIGNING_KEY_FINGERPRINT)=' security/signing.env | sudo tee /etc/corporatetraveldc/signing-pin
# VERIFY_MANIFEST_PIN overrides the path (tests only).
SIGNING_PIN="${VERIFY_MANIFEST_PIN:-/etc/corporatetraveldc/signing-pin}"

# 2026-10-03: --unsigned-dry-run. ONLY for scripts/push-public.sh --dry-run,
# which builds the scrubbed public tree and its manifest but deliberately
# stops before the operator's GPG pass -- so there is no signature yet to
# verify, and this flag checks hashes + coverage only. It prints a loud
# banner, is never used by any production gate (ExecStartPre, sweeps,
# verified-exec.sh all call this script with no flag), and must stay that
# way: an unsigned manifest proves nothing about who produced it.
UNSIGNED_DRY_RUN=0
if [[ "${1:-}" == "--unsigned-dry-run" ]]; then
    UNSIGNED_DRY_RUN=1
    shift
    echo "verify-manifest: ##### UNSIGNED DRY RUN -- signature and key pin NOT checked; hashes + coverage only (push-public --dry-run use only) #####" >&2
fi

for f in "${MANIFEST}" "${SIGNATURE}" "${PUBKEY}" "${SIGNING_ENV}"; do
    if [[ "${UNSIGNED_DRY_RUN}" -eq 1 && "${f}" != "${MANIFEST}" ]]; then
        continue
    fi
    if [[ ! -f "${f}" ]]; then
        echo "verify-manifest: missing ${f} -- cannot verify, refusing to trust anything" >&2
        exit 2
    fi
done

GNUPGHOME_TMP="$(mktemp -d)"
SCOPED_TMP=""
# Single trap for both temp paths -- a second `trap ... EXIT` later would
# silently replace this one instead of adding to it (traps don't stack).
# 2026-10-05: stop the gpg-agent/scdaemon this keyring spawned before removing
# it -- they outlived every run ("left-over process ... in control group").
trap 'GNUPGHOME="${GNUPGHOME_TMP}" gpgconf --kill all >/dev/null 2>&1; rm -rf "${GNUPGHOME_TMP}"; [[ -n "${SCOPED_TMP}" ]] && rm -f "${SCOPED_TMP}"' EXIT
chmod 700 "${GNUPGHOME_TMP}"
export GNUPGHOME="${GNUPGHOME_TMP}"

if [[ "${UNSIGNED_DRY_RUN}" -eq 0 ]]; then
# 2026-10-04 (duel C2): never `source` the pin file -- it used to be code
# executed by whoever verifies (operator sweep, container entrypoints, root).
# Read the two keys literally; a root-owned /etc pin wins over the tree copy.
pin_src="${SIGNING_ENV}"
if [[ -f "${SIGNING_PIN}" ]]; then
    pin_owner="$(stat -c %u "${SIGNING_PIN}" 2>/dev/null || echo x)"
    if [[ "${pin_owner}" == 0 || -n "${VERIFY_MANIFEST_PIN:-}" ]]; then pin_src="${SIGNING_PIN}"
    else echo "verify-manifest: ${SIGNING_PIN} is not root-owned -- ignoring it" >&2; fi
fi
pin_get() {   # literal KEY=value read; strips one pair of surrounding quotes, nothing else
    local v; v="$(grep -m1 -E "^$1=" "${pin_src}" 2>/dev/null | cut -d= -f2-)"
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    printf '%s' "${v}" | tr -cd 'A-Fa-f0-9'
}
SIGNING_KEY_FINGERPRINT="$(pin_get SIGNING_KEY_FINGERPRINT)"
AGENT_SIGNING_KEY_FINGERPRINT="$(pin_get AGENT_SIGNING_KEY_FINGERPRINT)"
: "${SIGNING_KEY_FINGERPRINT:?SIGNING_KEY_FINGERPRINT not set in ${pin_src}}"
: "${AGENT_SIGNING_KEY_FINGERPRINT:?AGENT_SIGNING_KEY_FINGERPRINT not set in ${pin_src}}"

gpg --quiet --import "${PUBKEY}" >/dev/null 2>&1

gpg_err="$(mktemp)"
gpg_status="$(mktemp)"
if ! gpg --quiet --status-fd 3 --verify "${SIGNATURE}" "${MANIFEST}" 3>"${gpg_status}" 2>"${gpg_err}"; then
    echo "verify-manifest: SIGNATURE INVALID -- ${SIGNATURE} does not verify against ${PUBKEY}" >&2
    cat "${gpg_err}" >&2
    rm -f "${gpg_err}" "${gpg_status}"
    exit 1
fi
rm -f "${gpg_err}"

# VALIDSIG line: "[GNUPG:] VALIDSIG <sig-fpr> <date> ... <primary-key-fpr>"
# Field 3 is the fingerprint of whatever key/subkey actually produced the
# signature; the LAST field is the fingerprint of that key's PRIMARY key
# (identical to field 3 when the signer has no subkeys, as with the
# single-key AGENT_SIGNING_KEY_FINGERPRINT setup).
#
# CORRECTED 2026-08-27: this used to check field 3 only. A modern GPG key
# with the default subkey layout (a primary cert-only key plus dedicated
# signing/auth/encryption subkeys -- exactly what the operator's own key
# has, see security/trusted-signing-key.pub.asc) always delegates actual
# signing to its `s`-flagged SUBKEY, whose fingerprint is never equal to
# the primary key's. SIGNING_KEY_FINGERPRINT below is pinned to the
# operator's PRIMARY key fingerprint (same value used for
# `git config user.signingkey` and everywhere else this key is
# referenced) -- so any manual, passphrase-signed run (as opposed to
# --agent, whose single-key setup has no subkeys and happened to match
# field 3 directly every time) failed this pin, live, the first time it
# was ever exercised: "SIGNING KEY NOT PINNED" against the operator's own
# just-created signature. A key's subkey signing on the key's own behalf
# is exactly as trusted as the primary key -- the fingerprint pin exists
# to reject an ATTACKER-SUBSTITUTED key in ${PUBKEY}, not to reject the
# pinned identity's own normal subkey delegation. Now matches if either
# field identifies a pinned fingerprint.
signing_fpr="$(awk '/^\[GNUPG:\] VALIDSIG/ {print $3; exit}' "${gpg_status}")"
primary_fpr="$(awk '/^\[GNUPG:\] VALIDSIG/ {print $NF; exit}' "${gpg_status}")"
rm -f "${gpg_status}"

if [[ -z "${signing_fpr}" ]]; then
    echo "verify-manifest: could not determine the signing key's fingerprint from gpg's status output -- refusing to trust" >&2
    exit 1
fi

if [[ "${signing_fpr}" != "${SIGNING_KEY_FINGERPRINT}" && "${signing_fpr}" != "${AGENT_SIGNING_KEY_FINGERPRINT}" \
   && "${primary_fpr}" != "${SIGNING_KEY_FINGERPRINT}" && "${primary_fpr}" != "${AGENT_SIGNING_KEY_FINGERPRINT}" ]]; then
    echo "verify-manifest: SIGNING KEY NOT PINNED -- ${SIGNATURE} verifies against a key in ${PUBKEY} (signing fingerprint ${signing_fpr}, primary key fingerprint ${primary_fpr}), but neither matches SIGNING_KEY_FINGERPRINT nor AGENT_SIGNING_KEY_FINGERPRINT in ${SIGNING_ENV}. Refusing to trust a key that isn't the operator's own pinned fingerprint -- an attacker who can write tracked files could otherwise replace ${PUBKEY} with their own key and re-sign cleanly." >&2
    exit 1
fi
fi  # UNSIGNED_DRY_RUN guard (signature + key pin block above)

if [[ $# -eq 0 ]]; then
    # Collective mode: every entry in the manifest -- AND, 2026-10-03,
    # COVERAGE. `sha256sum -c` can only notice a *modified* file it already
    # knows about; a file present in the tree and absent from the manifest
    # (committed after the last signing pass, or dropped in by an attacker)
    # passed clean. Demonstrated live 2026-09-23 (an unsigned
    # docs/legacy/legacy-sqlite-schema.sql reached the public mirror through
    # push-public.sh, which trusts this exit code) and again three times on
    # 2026-10-03. The enumeration and exclusions below MIRROR
    # scripts/sign-manifest.sh exactly -- change both together. Scoped mode
    # (targets given) is untouched: container ExecStartPre gates verify only
    # what they bake in and must not fail on an unrelated new file.
    # Only possible inside a git work tree; a bare tarball install has no
    # enumeration to compare against and keeps the old hash-only check.
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        uncovered="$(LC_ALL=C comm -23 \
            <(git ls-files --cached --others --exclude-standard -z \
                | grep -zvE "^(MANIFEST\.sha256(\..*)?|docs/LIVE_STATE_CHECK_[0-9-]+\.md|docs/CLAUDE_MD_DRIFT_REPORT\.md)\$" \
                | tr '\0' '\n' | LC_ALL=C sort -u) \
            <(sed -E 's/^[0-9a-f]{64}  //' "${MANIFEST}" | LC_ALL=C sort -u))"
        if [[ -n "${uncovered}" ]]; then
            echo "verify-manifest: INTEGRITY FAILURE -- $(printf '%s\n' "${uncovered}" | wc -l) file(s) present in the tree but NOT covered by the signed manifest (re-run scripts/sign-manifest.sh, or remove them):" >&2
            printf '%s\n' "${uncovered}" | sed 's/^/  /' >&2
            exit 1
        fi
    fi
    if sha256sum -c "${MANIFEST}" --quiet; then
        if [[ "${UNSIGNED_DRY_RUN}" -eq 1 ]]; then
            echo "verify-manifest: OK (UNSIGNED DRY RUN -- hashes + coverage only, signature NOT checked) -- all $(wc -l < "${MANIFEST}") files match."
        else
            echo "verify-manifest: OK -- signature valid, all $(wc -l < "${MANIFEST}") files match."
        fi
        exit 0
    else
        echo "verify-manifest: INTEGRITY FAILURE -- one or more files do not match the signed manifest (see above)." >&2
        exit 1
    fi
fi

# One or more targets, each either an exact file (e.g. "scripts/foo.sh") or
# a directory prefix ending in "/" (e.g. "src/", matching every manifest
# entry under it). A single exact-file target is the common "check just
# myself" case; multiple/mixed targets are the "check everything actually
# baked into this container" case (e.g. "src/" + a few standalone files
# that live outside it) -- both go through the same matching logic here.
SCOPED_TMP="$(mktemp)"
: > "${SCOPED_TMP}"
for target in "$@"; do
    if [[ "${target}" == */ ]]; then
        awk -v p="${target}" 'index($2, p) == 1 {print; found=1} END{exit !found}' "${MANIFEST}" >> "${SCOPED_TMP}"
        matched=$?
    else
        # Exact-field match, not substring -- "scripts/foo.sh" can never
        # match "scripts/foo.sh.bak".
        awk -v t="${target}" '$2==t {print; found=1} END{exit !found}' "${MANIFEST}" >> "${SCOPED_TMP}"
        matched=$?
    fi
    if [[ ${matched} -ne 0 ]]; then
        echo "verify-manifest: '${target}' matched nothing in the signed manifest -- refusing to trust it" >&2
        exit 1
    fi
done
if sha256sum -c "${SCOPED_TMP}" --quiet; then
    echo "verify-manifest: OK -- signature valid, all $(wc -l < "${SCOPED_TMP}") file(s) under/matching (${*}) match."
    exit 0
else
    echo "verify-manifest: INTEGRITY FAILURE -- one or more files under/matching (${*}) do not match the signed manifest (see above)." >&2
    exit 1
fi
