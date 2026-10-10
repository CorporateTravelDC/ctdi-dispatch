#!/usr/bin/env bash
# scripts/backup/ctdi-backup.sh -- encrypted, deduplicated, ledgered backups with a
# parameterized off-box target (2026-10-09; docs/BACKUP.md).
#
#   1. Dump the dispatch Postgres and the Nextcloud database (credentials stay
#      inside each container's own environment -- nothing on a host command line).
#      The LADD table (CUI) is excluded from the off-box set by default; its data
#      goes to a LOCAL-ONLY dump and the private ledger records a "#" stamp
#      (sha256, row count, local path) so its state is provable without shipping it.
#   2. Borg (repokey: AES-256-CTR + HMAC-SHA256, PBKDF2-SHA256 key derivation --
#      NIST-approved algorithms) archives the dumps, the Nextcloud files, platform
#      state (models excluded: pinned by digest) and /etc/corporatetraveldc, plus a
#      copy of the private ledger, into a local repository (fast local restore).
#   3. Prune to the retention policy, then rclone-sync the repository to
#      BACKUP_TARGET: any rclone remote (Proton Drive, SMB, Google Workspace,
#      Microsoft 365 / OneDrive / SharePoint, S3, SFTP ...) or a mounted path
#      (e.g. an organization's Z: drive mounted on this host). The target only
#      ever sees Borg-encrypted chunks; Proton adds its own end-to-end layer.
#   4. Every step lands in the hash-chained private ledger (backup_ledger.py).
#
# No passphrase agents (operator rule): Borg reads its key passphrase from a
# root/operator-only file through BORG_PASSCOMMAND at run time; nothing caches it.
#
# Settings (non-secret, /etc/corporatetraveldc/dispatch.env; defaults in brackets):
#   BACKUP_TARGET            rclone remote:path or absolute path ['' = local only]
#   BACKUP_REPO              [~/.local/state/ctdc-backup/borg]
#   BACKUP_PASSPHRASE_FILE   [/etc/corporatetraveldc/backup/borg-passphrase]
#   BACKUP_RCLONE_CONFIG     [~/.config/rclone/rclone.conf]
#   BACKUP_INCLUDE_CUI       [0]  1 = LADD table data goes in the off-box set too
#   BACKUP_KEEP_DAILY/WEEKLY/MONTHLY  [30 / 12 / 12]
#   BACKUP_CUI_LOCAL_KEEP    [7]  local-only LADD dumps kept
#
# Usage: ctdi-backup.sh [--no-sync] [--dry-run]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE=/etc/corporatetraveldc/dispatch.env
LEDGER_PY="${REPO_DIR}/scripts/backup/backup_ledger.py"
read_env() { [[ -f "$ENV_FILE" ]] && grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2- || true; }   # parsed, never sourced
setting() { local v; v="${!1:-}"; [[ -n "$v" ]] || v="$(read_env "$1")"; echo "${v:-$2}"; }

STATE="${HOME}/.local/state/ctdc-backup"
BACKUP_TARGET="$(setting BACKUP_TARGET '')"
BACKUP_REPO="$(setting BACKUP_REPO "${STATE}/borg")"
PASSFILE="$(setting BACKUP_PASSPHRASE_FILE /etc/corporatetraveldc/backup/borg-passphrase)"
RCLONE_CONFIG="$(setting BACKUP_RCLONE_CONFIG "${HOME}/.config/rclone/rclone.conf")"
INCLUDE_CUI="$(setting BACKUP_INCLUDE_CUI 0)"
KEEP_D="$(setting BACKUP_KEEP_DAILY 30)"; KEEP_W="$(setting BACKUP_KEEP_WEEKLY 12)"; KEEP_M="$(setting BACKUP_KEEP_MONTHLY 12)"
CUI_KEEP="$(setting BACKUP_CUI_LOCAL_KEEP 7)"
DISPATCH_PG=corporatetraveldc-pgsql
NEXTCLOUD_PG=nextcloud-db
LADD_TABLES=(faa_ladd_aircraft faa_ladd_removals)   # CUI: identifiers of LADD-listed aircraft
SYNC=1; DRY=0
for a in "$@"; do case "$a" in --no-sync) SYNC=0 ;; --dry-run) DRY=1 ;; *) echo "usage: $0 [--no-sync] [--dry-run]" >&2; exit 64 ;; esac; done

log() { printf '[%s] [backup] %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*"; python3 "$LEDGER_PY" append note --json "{\"error\": \"$(echo "$*" | tr -d '"\\' | head -c 300)\"}" >/dev/null 2>&1 || true; exit 1; }

command -v borg >/dev/null || die "borg is not installed (dnf install borgbackup)"
[[ -r "$PASSFILE" ]] || die "Borg passphrase file $PASSFILE is missing or unreadable"
[[ "$(stat -c %a "$PASSFILE")" =~ ^[46]00$ ]] || die "$PASSFILE must be mode 0600 or 0400"
export BORG_PASSCOMMAND="cat ${PASSFILE}" BORG_REPO="$BACKUP_REPO" BORG_RELOCATED_REPO_ACCESS_IS_OK=no
mkdir -p -m 0700 "$STATE" "$STATE/cui-local"
exec 9>"${STATE}/.lock"; flock -n 9 || die "another backup is running"

ts=$(date -u +%Y%m%dT%H%M%SZ)
stage=$(mktemp -d "${STATE}/stage.${ts}.XXXX"); chmod 700 "$stage"
cleanup() { find "$stage" -type f -exec shred -fu {} + 2>/dev/null; rm -rf "$stage"; }
trap cleanup EXIT

pg_dump_in() {   # container, output file, extra pg_dump args (single-quoted inside the container shell)
  local c="$1" out="$2" extra="${3:-}"
  podman exec -i "$c" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc -Z 6 ${extra}" > "$out"
}

log "dump: dispatch Postgres (LADD data $( [[ "$INCLUDE_CUI" == 1 ]] && echo included || echo 'excluded -> local-only'))"
if [[ "$INCLUDE_CUI" == 1 ]]; then pg_dump_in "$DISPATCH_PG" "$stage/dispatch.pgdump"
else pg_dump_in "$DISPATCH_PG" "$stage/dispatch.pgdump" "$(printf -- '--exclude-table-data=%s ' "${LADD_TABLES[@]}")"; fi
log "dump: Nextcloud database"
pg_dump_in "$NEXTCLOUD_PG" "$stage/nextcloud.pgdump"

# CUI "#" stamp: local-only LADD dump + its hash in the ledger
cui_file="$STATE/cui-local/ladd-${ts}.pgdump"
pg_dump_in "$DISPATCH_PG" "$cui_file" "--data-only $(printf -- '--table=%s ' "${LADD_TABLES[@]}")"
chmod 600 "$cui_file"
count_sql=$(printf "SELECT json_build_object(%s)" "$(printf "'%s', (SELECT count(*) FROM %s)," $(for t in "${LADD_TABLES[@]}"; do echo "$t $t"; done) | sed 's/,$//')")
ladd_rows=$(podman exec -i "$DISPATCH_PG" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"${count_sql}\"")
cui_meta=$(python3 "$LEDGER_PY" file "$cui_file")
# head of the in-database reference-import ledger (pg_schema/0074): pins WHICH LADD/registry source files are loaded
import_head=$(podman exec -i "$DISPATCH_PG" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"SELECT json_build_object('seq', seq, 'entry_hash', entry_hash) FROM reference_import_ledger ORDER BY seq DESC LIMIT 1\"" 2>/dev/null || true)
python3 "$LEDGER_PY" append cui-stamp --json "{\"stamp\": \"#\", \"rows\": ${ladd_rows:-null}, \"off_box\": $( [[ "$INCLUDE_CUI" == 1 ]] && echo true || echo false ), \"local_dump\": ${cui_meta}, \"import_ledger_head\": ${import_head:-null}}" >/dev/null
ls -1t "$STATE/cui-local"/ladd-*.pgdump 2>/dev/null | tail -n +$((CUI_KEEP + 1)) | while read -r old; do shred -fu "$old"; done

dumps_meta=$(python3 "$LEDGER_PY" file "$stage/dispatch.pgdump" "$stage/nextcloud.pgdump")
cp "${BACKUP_LEDGER:-$STATE/ledger.jsonl}" "$stage/ledger.jsonl"

nc_vol=$(podman volume inspect nextcloud-html --format '{{.Mountpoint}}')
paths=("$stage" "$nc_vol" /var/lib/corporatetraveldc /etc/corporatetraveldc)
excludes=(--exclude /var/lib/corporatetraveldc/models --exclude 'sh:**/__pycache__' --exclude 'sh:**/*.tmp')

if [[ "$DRY" == 1 ]]; then log "dry run: would archive ${paths[*]} to $BACKUP_REPO and sync to '${BACKUP_TARGET:-<local only>}'"; exit 0; fi

if [[ ! -d "$BACKUP_REPO" ]]; then
  log "initialising Borg repository (repokey, AES-256 + HMAC-SHA256) at $BACKUP_REPO"
  mkdir -p -m 0700 "$(dirname "$BACKUP_REPO")"
  borg init --encryption=repokey "$BACKUP_REPO"
fi

archive="ctdi-${ts}"
log "borg create ::${archive}"
# podman unshare: the Nextcloud volume belongs to the container's mapped uids
stats=$(podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" \
  borg create --compression zstd,6 --one-file-system --json "${excludes[@]}" "::${archive}" "${paths[@]}")
arch_id=$(echo "$stats" | python3 - <<'PY'
import json, sys
d = json.load(sys.stdin); a = d.get("archive", {})
print(json.dumps({"id": a.get("id"), "name": a.get("name"), "original_size": a.get("stats", {}).get("original_size"),
                  "compressed_size": a.get("stats", {}).get("compressed_size"),
                  "deduplicated_size": a.get("stats", {}).get("deduplicated_size"), "nfiles": a.get("stats", {}).get("nfiles")}))
PY
)
podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" \
  borg prune --keep-daily "$KEEP_D" --keep-weekly "$KEEP_W" --keep-monthly "$KEEP_M" --glob-archives 'ctdi-*'
podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" borg compact
python3 "$LEDGER_PY" append backup --json "{\"archive\": ${arch_id}, \"dumps\": ${dumps_meta}, \"include_cui\": $( [[ "$INCLUDE_CUI" == 1 ]] && echo true || echo false ), \"repo\": \"local\"}" >/dev/null
log "archive ${archive} done"

if [[ "$SYNC" == 1 && -n "$BACKUP_TARGET" ]]; then
  command -v rclone >/dev/null || die "rclone is not installed (dnf install rclone)"
  log "sync repository -> ${BACKUP_TARGET}"
  if podman unshare rclone --config "$RCLONE_CONFIG" sync --checksum "$BACKUP_REPO" "$BACKUP_TARGET" --stats-one-line --stats 0; then
    python3 "$LEDGER_PY" append sync --json "{\"target\": \"${BACKUP_TARGET}\", \"archive\": \"${archive}\", \"ok\": true}" >/dev/null
  else
    python3 "$LEDGER_PY" append sync --json "{\"target\": \"${BACKUP_TARGET}\", \"archive\": \"${archive}\", \"ok\": false}" >/dev/null
    die "sync to ${BACKUP_TARGET} failed (the local archive is complete)"
  fi
fi
python3 "$LEDGER_PY" verify
