#!/usr/bin/env bash
# scripts/lib/public-manifest.sh -- self-verifying signed manifest for a
# scrubbed PUBLIC tree. Sourced by scripts/push-public.sh (and, by design,
# by the sibling site repos' push-public equivalents); every function takes
# explicit arguments and touches nothing outside the paths it is given.
#
# Why (2026-10-03): push-public.sh used to ship the public mirror WITHOUT
# any manifest (the private MANIFEST.sha256(.asc) is dropped by the
# scrubber because it hashes private bytes the scrub rewrites), so the
# public repo could not run scripts/verify-manifest.sh at all -- the mirror
# that exists to showcase the integrity model did not actually carry it.
#
# What a public tree now carries, all produced here:
#   MANIFEST.sha256 / MANIFEST.sha256.asc
#       hashes of exactly the public files (same format, ordering and
#       exclusions as scripts/sign-manifest.sh), detached-signed by the
#       operator key -- so `scripts/verify-manifest.sh` passes from a
#       public clone, including its full-tree coverage assertion.
#   PRIVATE-TREE-MANIFEST.sha256 / PRIVATE-TREE-MANIFEST.sha256.asc
#       the PRIVATE tree's manifest with every PATH replaced by sha256(path)
#       ("path-blinded"), freshly signed by the operator key. Verbatim was
#       rejected: the private manifest's paths carry the real domain in 18
#       nginx vhost filenames and the operator's name in two key filenames
#       -- the exact strings the scrubber substitutes in content. Blinding
#       keeps the attestation (any public file whose bytes the scrub left
#       untouched can be looked up by sha256 of its path and matched to the
#       private tree's content hash) without disclosing private-only names.
#       These two files are themselves covered by the public manifest.
#   security/signing.env, security/trusted-signing-key.pub.asc
#       re-injected from the private tree so the pinned-fingerprint
#       verification in verify-manifest.sh has what it needs. Both are
#       public-by-nature (a key fingerprint and a public key; the operator
#       confirmed the key is deliberately public, 2026-08-26) and both pass
#       the scrubber's substitution and verify_scrubbed() scans unchanged.
#
# Every function prints only paths/hashes/counts -- never a secret value.
set -euo pipefail

# Mirrors scripts/sign-manifest.sh and verify-manifest.sh's exclusion set
# exactly -- change all three together.
PM_EXCLUDE_RE='^(MANIFEST\.sha256(\..*)?|docs/LIVE_STATE_CHECK_[0-9-]+\.md|docs/CLAUDE_MD_DRIFT_REPORT\.md)$'

# pm_generate_manifest <tree-sha> <out-file>
# Hash every blob in the tree, same line format as sign-manifest.sh
# ("<sha256>  <path>", sorted by path). Hashing is done from git objects,
# never a checkout, so the result is a pure function of the tree.
pm_generate_manifest() {
    local tree="$1" out="$2" tmp
    tmp="$(mktemp)"
    git ls-tree -r --full-tree "${tree}" \
        | awk '$2=="blob" {print $3 "\t" substr($0, index($0,"\t")+1)}' \
        | while IFS=$'\t' read -r blob path; do
            if printf '%s' "${path}" | grep -qE "${PM_EXCLUDE_RE}"; then
                continue
            fi
            printf '%s  %s\n' "$(git cat-file blob "${blob}" | sha256sum | cut -d' ' -f1)" "${path}"
          done \
        | sort -k2 > "${tmp}"
    mv "${tmp}" "${out}"
    echo "[public-manifest] ${out}: $(wc -l < "${out}") files covered" >&2
}

# pm_blind_private_manifest <private-manifest-file> <out-file>
# "<content-sha256>  <sha256(path)>" per line, sorted. Paths never appear.
pm_blind_private_manifest() {
    local src="$1" out="$2"
    python3 - "${src}" "${out}" <<'PYEOF'
import hashlib, sys
src, out = sys.argv[1], sys.argv[2]
rows = []
with open(src, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        digest, path = line.split("  ", 1)
        rows.append((digest, hashlib.sha256(path.encode("utf-8")).hexdigest()))
rows.sort(key=lambda r: r[1])
with open(out, "w", encoding="utf-8") as fh:
    for digest, blinded in rows:
        fh.write(f"{digest}  {blinded}\n")
print(f"[public-manifest] {out}: {len(rows)} private entries, paths blinded", file=sys.stderr)
PYEOF
}

# pm_sign <repo-root> <file> <sig-out>
# Detached armored signature with the operator key pinned in
# <repo-root>/security/signing.env -- identical mechanism to
# scripts/sign-manifest.sh (operator-run, passphrase prompt). Fails cleanly
# (non-zero, nothing written) if the key or env is absent.
pm_sign() {
    local root="$1" file="$2" sig="$3" env_file fpr tmp
    env_file="${root}/security/signing.env"
    if [[ ! -f "${env_file}" ]]; then
        echo "[public-manifest] missing ${env_file} -- cannot sign" >&2
        return 2
    fi
    fpr="$(grep -m1 '^SIGNING_KEY_FINGERPRINT=' "${env_file}" | cut -d= -f2-)"
    if [[ -z "${fpr}" || "${fpr}" == "0000000000000000000000000000000000000000" ]]; then
        echo "[public-manifest] SIGNING_KEY_FINGERPRINT unset/placeholder in ${env_file} -- cannot sign" >&2
        return 2
    fi
    tmp="$(mktemp "${sig}.XXXXXX")"
    if gpg --local-user "${fpr}" --detach-sign --armor --yes -o "${tmp}" "${file}"; then
        mv "${tmp}" "${sig}"
        echo "[public-manifest] signed $(basename "${file}") -> $(basename "${sig}")" >&2
    else
        rm -f "${tmp}"
        echo "[public-manifest] gpg FAILED signing $(basename "${file}") -- nothing written" >&2
        return 1
    fi
}

# pm_add_to_tree <tree-sha> <path>=<local-file> [<path>=<local-file> ...]
# Returns (stdout) a new tree with each local file written in at <path>
# (nested paths fine). Uses a throwaway index so the real one is untouched.
pm_add_to_tree() {
    local tree="$1"; shift
    local idx spec path file blob
    idx="$(mktemp)"
    rm -f "${idx}"
    GIT_INDEX_FILE="${idx}" git read-tree "${tree}"
    for spec in "$@"; do
        path="${spec%%=*}"
        file="${spec#*=}"
        blob="$(git hash-object -w "${file}")"
        GIT_INDEX_FILE="${idx}" git update-index --add --cacheinfo "100644,${blob},${path}"
    done
    GIT_INDEX_FILE="${idx}" git write-tree
    rm -f "${idx}"
}

# pm_tree_blob_to_file <tree-sha> <path> <out-file>
pm_tree_blob_to_file() {
    git cat-file blob "$1:$2" > "$3"
}

# pm_extract_tree <tree-sha> <dir>
# Checkout-equivalent of the tree into <dir> and make it a git work tree
# (so verify-manifest.sh's full-tree coverage assertion, which enumerates
# via `git ls-files`, actually runs there). The .git inside is throwaway.
pm_extract_tree() {
    local tree="$1" dir="$2"
    git archive --format=tar "${tree}" | tar -x -C "${dir}"
    ( cd "${dir}" && git init -q && git add -A >/dev/null 2>&1 )
}
