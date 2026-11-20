#!/usr/bin/env bash
# scripts/backup/backup-restore-test-direct.sh -- prove the newest direct backup is intact on the remote
# (2026-11-19; docs/BACKUP.md "Direct backup").
#
# The box holds only the operator's PUBLIC key, so this automated test cannot decrypt anything. Instead it:
#   1. finds the newest complete direct run in the ledger;
#   2. streams each of its objects back from the remote through stream_tap.py (nothing written to disk) and
#      checks size and sha256 against the write-time ledger entry;
#   3. reads the first 64 KiB of the dispatch dump and checks, with `gpg --list-packets`, that it is a
#      public-key-encrypted packet for the configured recipient's encryption key.
# Decrypting and pg_restore --list is the operator's step, with the private key, documented in docs/BACKUP.md.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="${REPO_DIR}/scripts/backup"
ENV_FILE="${CTDC_ENV_FILE:-/etc/corporatetraveldc/dispatch.env}"
read_env() { [[ -f "$ENV_FILE" ]] && grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }
setting() { local v; v="${!1:-}"; [[ -n "$v" ]] || v="$(read_env "$1")"; echo "${v:-$2}"; }
STATE="${HOME}/.local/state/ctdc-backup"
TARGET="$(setting BACKUP_TARGET_DIRECT '')"
RECIPIENT="$(setting BACKUP_DIRECT_GPG_RECIPIENT '')"
PUBKEY="$(setting BACKUP_DIRECT_GPG_PUBKEY /etc/corporatetraveldc/backup/direct-recipient.asc)"
RCLONE_CONFIG="$(setting BACKUP_RCLONE_CONFIG "${HOME}/.config/rclone/rclone.conf")"
log() { printf '[%s] [restore-test-direct] %s\n' "$(date -u +%FT%TZ)" "$*"; }
result() {  # ok(bool) detail
  python3 "${HERE}/backup_ledger.py" append direct-restore-test --json "{\"ok\": $1, \"detail\": \"$(echo "$2" | tr -d '"\\' | head -c 300)\"}" >/dev/null
  log "$2"; [[ "$1" == true ]] || exit 1
}
[[ -n "$TARGET" ]] || result false "BACKUP_TARGET_DIRECT is not set"
last="$(python3 "${HERE}/backup_ledger.py" last direct-backup 2>/dev/null || true)"
[[ -n "$last" && "$last" != null ]] || result false "no direct backup in the ledger"
mapfile -t objs < <(python3 - "$last" <<'PY'
import json, sys
e = json.loads(sys.argv[1]); d = e.get("data", {})
if not d.get("ok"):
    sys.exit(0)
for o in d.get("objects", []):
    print(f'{d["run"]}\t{o["name"]}\t{o["bytes"]}\t{o["sha256"]}')
PY
)
(( ${#objs[@]} )) || result false "the newest direct run is not complete; nothing to test"
source "${REPO_DIR}/scripts/lib/heavy_lock.sh"
heavy_lock_acquire "restore test" 7200 || result false "heavy-work turn not available"
META="$(mktemp -d "${STATE}/rt.XXXX")"; trap 'rm -rf "$META"' EXIT
for line in "${objs[@]}"; do
  IFS=$'\t' read -r run name bytes sha <<<"$line"
  podman unshare rclone --config "$RCLONE_CONFIG" cat "${TARGET}/${run}/${name}" </dev/null \
    | python3 "${HERE}/stream_tap.py" "${META}/${name}.json" > /dev/null \
    || result false "could not read ${run}/${name} back from the remote"
  got="$(cat "${META}/${name}.json")"
  python3 "${HERE}/stream_tap.py" --same "$got" "{\"bytes\": ${bytes}, \"sha256\": \"${sha}\"}" \
    || result false "${run}/${name}: remote copy does not match the write-time ledger hash"
  log "${run}/${name}: ${bytes} bytes, sha256 matches the ledger"
done
G="$(mktemp -d "${STATE}/gpg.XXXX")"; chmod 700 "$G"; trap 'rm -rf "$META" "$G"' EXIT
gpg --homedir "$G" --batch --no-tty --no-autostart --quiet --import "$PUBKEY" 2>/dev/null || result false "cannot import $PUBKEY"
keyids="$(gpg --homedir "$G" --batch --no-tty --no-autostart --with-colons --list-keys "$RECIPIENT" 2>/dev/null | awk -F: '$1=="pub"||$1=="sub"{print $5}')"
IFS=$'\t' read -r run _ _ _ <<<"${objs[0]}"
pk="$(podman unshare rclone --config "$RCLONE_CONFIG" cat --head 65536 "${TARGET}/${run}/dispatch.pgdump.zst.gpg" </dev/null \
      | gpg --homedir "$G" --batch --no-tty --no-autostart --list-packets 2>&1 | grep -m1 -oE 'pubkey enc packet: .*keyid [0-9A-F]+' | grep -oE '[0-9A-F]+$' || true)"
[[ -n "$pk" ]] && grep -qx "$pk" <<<"$keyids" || result false "dispatch dump is not encrypted to the configured recipient (packet keyid '${pk:-none}')"
result true "run ${run}: ${#objs[@]} object(s) match the ledger on the remote; dispatch dump is encrypted to the recipient key"
