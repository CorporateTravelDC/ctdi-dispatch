#!/usr/bin/env bash
# scripts/installed-check.sh -- integrity check for ROOT-installed copies.
#
#   /usr/local/libexec/ctdc/installed-check.sh scripts/cf-honeypot-ban.sh [scripts/...]
#
# install-root-copies.sh verifies the signed manifest BEFORE it copies, then
# records each installed file's sha256 in the root-owned .ctdc-installed. At
# run time a root-run script (or fail2ban's action.d chain) re-checks the
# installed bytes against that record -- never against the operator-writable
# checkout, and never by running the checkout's verify-manifest.sh as root
# (2026-10-04: fail2ban's actions did exactly that; the follow-up to duel C1).
# Exit 0 only when every named file is installed and unchanged.
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REC="${SELF_DIR}/.ctdc-installed"
[[ -f "$REC" && ! -L "$REC" ]] || { echo "installed-check: no install record at ${REC}" >&2; exit 2; }
[[ "$(stat -c %u "$REC")" == 0 ]] || { echo "installed-check: ${REC} is not root-owned" >&2; exit 2; }
(( $# )) || { echo "usage: $0 scripts/NAME [...]" >&2; exit 64; }
rc=0
for rel in "$@"; do
  case "$rel" in scripts/*) f="${SELF_DIR}/$(basename "$rel")" ;; src/*) f="${SELF_DIR}/lib/${rel#src/}" ;; *) echo "installed-check: ${rel}: not a root-run path" >&2; rc=1; continue ;; esac
  want=$(awk -v p="$rel" '$2==p {print $1; exit}' "$REC")
  if [[ -z "$want" || ! -f "$f" || -L "$f" ]]; then echo "installed-check: ${rel} is not installed" >&2; rc=1; continue; fi
  [[ "$(stat -c %u "$f")" == 0 ]] || { echo "installed-check: ${f} is not root-owned" >&2; rc=1; continue; }
  have=$(sha256sum "$f" | cut -d' ' -f1)
  [[ "$have" == "$want" ]] || { echo "installed-check: ${rel} CHANGED since install (${have:0:12} != ${want:0:12})" >&2; rc=1; }
done
exit $rc
