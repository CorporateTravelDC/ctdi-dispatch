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

for f in "${MANIFEST}" "${SIGNATURE}" "${PUBKEY}"; do
    if [[ ! -f "${f}" ]]; then
        echo "verify-manifest: missing ${f} -- cannot verify, refusing to trust anything" >&2
        exit 2
    fi
done

GNUPGHOME_TMP="$(mktemp -d)"
SCOPED_TMP=""
# Single trap for both temp paths -- a second `trap ... EXIT` later would
# silently replace this one instead of adding to it (traps don't stack).
trap 'rm -rf "${GNUPGHOME_TMP}"; [[ -n "${SCOPED_TMP}" ]] && rm -f "${SCOPED_TMP}"' EXIT
chmod 700 "${GNUPGHOME_TMP}"
export GNUPGHOME="${GNUPGHOME_TMP}"

gpg --quiet --import "${PUBKEY}" >/dev/null 2>&1

gpg_err="$(mktemp)"
if ! gpg --quiet --verify "${SIGNATURE}" "${MANIFEST}" 2>"${gpg_err}"; then
    echo "verify-manifest: SIGNATURE INVALID -- ${SIGNATURE} does not verify against ${PUBKEY}" >&2
    cat "${gpg_err}" >&2
    rm -f "${gpg_err}"
    exit 1
fi
rm -f "${gpg_err}"

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
