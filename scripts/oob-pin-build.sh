#!/usr/bin/env bash
# scripts/oob-pin-build.sh -- build the lockdown OOB pin list from the config's pin SOURCES (2026-11-18).
# Run AS THE OPERATOR from the checkout, before signing (approval_signers is not readable to agents):
#
#   scripts/oob-pin-build.sh --material-dir DIR [--config FILE] [--out FILE]
#
# Sources (config "pins", each with a role label): board_signers / approval_signers by account, an
# OpenSSH public key file, an OpenPGP .asc file. The pin file (default config/network/oob-pinned-fingerprints,
# private) holds role, source, key type and fingerprint only. A source that cannot be found is
# recorded as MISSING and the build continues; nothing is ever invented. The public key material
# goes to DIR for root: sudo /usr/local/libexec/ctdc/ctdc-oob.sh install-keys DIR (root checks every
# key against the installed pins again). The FIDO mode is re-derived on every build (config ssh.fido:
# auto = sk-* keys only once an admin account has an active hardware key in the pinned set).
# Postgres as board-signer-ctl.sh does it: the password is read in-script and never echoed.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS=/etc/corporatetraveldc/dispatch-secrets.env
ENVF=/etc/corporatetraveldc/dispatch.env
OPERATOR="${CTDC_OPERATOR:-corporatetraveldc}"
CONFIG="${REPO_ROOT}/config/network/oob.json"
OUT="${REPO_ROOT}/config/network/oob-pinned-fingerprints"
LIB="${REPO_ROOT}/scripts/lib/ctdc_oob.py"
MAT=""; ROWS=""
while (( $# )); do
    case "$1" in
        --material-dir) MAT="${2:?}"; shift 2 ;;
        --config) CONFIG="${2:?}"; shift 2 ;;
        --out) OUT="${2:?}"; shift 2 ;;
        --rows) ROWS="${2:?}"; shift 2 ;;     # test seam only (CTDC_OOB_PIN_TEST=1): signer rows as JSON
        *) sed -n '2,15p' "$0" >&2; exit 64 ;;
    esac
done
[[ -n "$MAT" ]] || { echo "oob-pin-build: --material-dir DIR is required" >&2; exit 64; }
(( EUID != 0 )) || { echo "oob-pin-build: run as the operator, not root" >&2; exit 77; }
TEST=0; [[ "${CTDC_OOB_PIN_TEST:-}" == 1 && -n "$ROWS" ]] && TEST=1
(( TEST )) || [[ "$(id -un)" == "$OPERATOR" ]] || { echo "oob-pin-build: run as ${OPERATOR} (approval_signers is operator-only)" >&2; exit 77; }
py() { /usr/bin/python3 "$LIB" --config "$CONFIG" --default-account "$OPERATOR" "$@"; }
py validate >/dev/null

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
if (( TEST )); then
    cp "$ROWS" "$tmp/rows.json"
else
    cfg() { grep -m1 "^$1=" "$ENVF" 2>/dev/null | cut -d= -f2- || true; }
    PG_USER="$(cfg DISPATCH_PG_USER)"; PG_USER="${PG_USER:-dispatch}"
    PG_DB="$(cfg DISPATCH_PG_DB)";     PG_DB="${PG_DB:-corporatetraveldc}"
    : >"$tmp/rows"
    while read -r src acct; do
        [[ "$acct" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || continue     # validated by the config loader too
        case "$src" in
            board_signers) sql="SELECT 'board_signers', account, pubkey, COALESCE(role, ''), COALESCE(kind, ''), CASE WHEN active THEN 1 ELSE 0 END FROM board_signers WHERE account = '${acct}';" ;;
            approval_signers) sql="SELECT 'approval_signers', a.account, a.pubkey, COALESCE(b.role, ''), COALESCE(b.kind, ''), CASE WHEN a.active AND COALESCE(b.active, FALSE) THEN 1 ELSE 0 END FROM approval_signers a LEFT JOIN board_signers b ON b.account = a.account WHERE a.account = '${acct}';" ;;
            *) continue ;;
        esac
        pw="$(grep -m1 '^DISPATCH_PG_PASSWORD=' "$SECRETS" | cut -d= -f2-)"
        PGPASSWORD="$pw" podman exec -i -e PGPASSWORD corporatetraveldc-pgsql \
            psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -q -tA -F '|' >>"$tmp/rows" <<<"$sql" || true
        unset pw
    done < <(py pin-accounts)
    /usr/bin/python3 - "$tmp/rows" "$tmp/rows.json" <<'EOF'
import json, sys
rows = []
for ln in open(sys.argv[1]):
    f = ln.rstrip("\n").split("|")
    if len(f) == 6:
        rows.append({"source": f[0], "account": f[1], "pubkey": f[2], "role": f[3], "kind": f[4], "active": f[5] == "1"})
json.dump(rows, open(sys.argv[2], "w"))
EOF
fi

rm -rf "$MAT"; mkdir -p "$MAT"
py pins-build --rows "$tmp/rows.json" --repo "$REPO_ROOT" --material-dir "$MAT" >"$tmp/pins"
install -m 0644 "$tmp/pins" "$OUT"
echo "oob-pin-build: wrote ${OUT}"
sed -n 's/^\(fido\|mode\): /  \1 /p' "$OUT"
awk '$1 == "ssh" || $1 == "gpg" {print "  " $2 ": " $1 " " $4 " " $5 " " $7 " " $8}' "$OUT"
sed -n 's/^missing \([^ ]*\) \(.*\)/  MISSING \1: \2/p' "$OUT"
py pins-check --pins "$OUT" --keys "$MAT/ssh.pub" 2>&1 | sed 's/^/  /' || true
echo "  key material for root: ${MAT} ($(wc -l <"$MAT/ssh.pub") ssh line(s), $(find "$MAT" -name 'gpg-*.asc' | wc -l) gpg key(s))"
