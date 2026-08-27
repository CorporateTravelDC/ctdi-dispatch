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

for f in "${MANIFEST}" "${SIGNATURE}" "${PUBKEY}" "${SIGNING_ENV}"; do
    if [[ ! -f "${f}" ]]; then
        echo "verify-manifest: missing ${f} -- cannot verify, refusing to trust anything" >&2
        exit 2
    fi
done

# shellcheck source=/dev/null
source "${SIGNING_ENV}"
: "${SIGNING_KEY_FINGERPRINT:?SIGNING_KEY_FINGERPRINT not set in ${SIGNING_ENV}}"
: "${AGENT_SIGNING_KEY_FINGERPRINT:?AGENT_SIGNING_KEY_FINGERPRINT not set in ${SIGNING_ENV}}"

GNUPGHOME_TMP="$(mktemp -d)"
SCOPED_TMP=""
# Single trap for both temp paths -- a second `trap ... EXIT` later would
# silently replace this one instead of adding to it (traps don't stack).
trap 'rm -rf "${GNUPGHOME_TMP}"; [[ -n "${SCOPED_TMP}" ]] && rm -f "${SCOPED_TMP}"' EXIT
chmod 700 "${GNUPGHOME_TMP}"
export GNUPGHOME="${GNUPGHOME_TMP}"

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

if [[ $# -eq 0 ]]; then
    # Collective mode: every entry in the manifest.
    if sha256sum -c "${MANIFEST}" --quiet; then
        echo "verify-manifest: OK -- signature valid, all $(wc -l < "${MANIFEST}") files match."
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
