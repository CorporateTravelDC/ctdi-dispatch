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
#      state (models excluded: pinned by digest), /etc/corporatetraveldc, the ntfy and Open WebUI
#      state dirs and the named volumes in BACKUP_VOLUMES (2026-10-24), plus a copy of the
#      private ledger, into a local repository (fast local restore).
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
#   BACKUP_WORK_DIR          [~/.local/state/ctdc-backup]  the bulky working files: the ~7 GB dump stage,
#                            the local-only LADD dumps (cui-local/) and the Borg cache (borg-cache/).
#                            2026-11-03 (operator): all backup data lives on the USB backup filesystem;
#                            the NVMe keeps only the small ledgers and the lock.
#   BACKUP_REQUIRE_MOUNT     ['']  a mount point that must be MOUNTED before anything is written; unset
#                            = no check. When the USB drops, its mount point is an empty directory on the
#                            NVMe and a backup would silently fill the root filesystem (2026-11-03).
#
# Usage: ctdi-backup.sh [--no-sync] [--dry-run] [--preflight-only]
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
# 2026-11-22 (operator): rclone's config is encrypted at rest (rclone config encryption set); its password is read
# from this 0600 file only by the sync, so a copy of rclone.conf (in an archive, a leaked file) carries no usable
# Proton session. And the sync itself needs the operator's weekly signed gate (scripts/backup/sync_gate.py).
RCLONE_PASS_FILE="$(setting BACKUP_RCLONE_CONFIG_PASS_FILE /etc/corporatetraveldc/backup/rclone-config-pass)"
SYNC_GATE="$(setting BACKUP_SYNC_GATE 1)"
SYNC_GATE_CMD="$(setting BACKUP_SYNC_GATE_CMD "${REPO_DIR}/scripts/with-dispatch-env.sh env PYTHONPATH=${REPO_DIR}/src python3 ${REPO_DIR}/scripts/backup/sync_gate.py")"
STAGE_STALE_MIN="$(setting BACKUP_STAGE_STALE_MIN 360)"
INCLUDE_CUI="$(setting BACKUP_INCLUDE_CUI 0)"
# 2026-11-26 (operator): the stick keeps a 7-30 night local Borg copy (dedup, network-down and portable fallback);
# long history lives in Proton, which is append-only (old segments and index.N files are never deleted there), so
# weekly/monthly archives are no longer kept locally. BACKUP_KEEP_DAILY must stay within 7..30.
KEEP_D="$(setting BACKUP_KEEP_DAILY 30)"; KEEP_W="$(setting BACKUP_KEEP_WEEKLY 0)"; KEEP_M="$(setting BACKUP_KEEP_MONTHLY 0)"
CUI_KEEP="$(setting BACKUP_CUI_LOCAL_KEEP 7)"
# 2026-11-03: Proton drops connections under parallel uploads (2003 "Upload file empty", closed
# connections, 502/422); rclone's default 4 transfers turned a few GB into hours of whole-segment
# retries, while the same upload one file at a time finished clean in ~20 min the same day.
RCLONE_TRANSFERS="$(setting BACKUP_RCLONE_TRANSFERS 1)"
# 2026-11-25 (operator): the off-box sync uses Proton's official Drive CLI through scripts/backup/proton_sync.py.
# rclone's protondrive backend (reverse-engineered) crawled at 55-66 KiB/s and failed on Proton's drafts (404
# Code=2501) every night from 11-22; the CLI moved the same data at 1.4-2.7 MiB/s in the 11-25 test. rclone stays
# for the METERED hotspot (only rclone takes the live 2 Mbps cap) and as BACKUP_SYNC_TOOL=rclone. The CLI binary
# is pinned by SHA-512 and its session is kept only as a systemd-creds --user blob (scripts/backup/proton_session.sh).
SYNC_TOOL="$(setting BACKUP_SYNC_TOOL proton-cli)"
PROTON_CLI="$(setting BACKUP_PROTON_CLI "${HOME}/proton-drive-cli/proton-drive")"
PROTON_CLI_SHA512="$(setting BACKUP_PROTON_CLI_SHA512 c8d5a6b174e57f06d05cb5483400b9963faf58e743b6b33706bb73ecff718047ffce6b439994255b94b5b226b90d2e91bef7418ec96c1c3f5d8bf39b6cd01321)"
PROTON_REMOTE="$(setting BACKUP_PROTON_REMOTE /my-files/ctdi-backup)"
PROTON_SESSION="$(setting BACKUP_PROTON_SESSION "${STATE}/proton-session.cred")"
PROTON_MAX_HOURS="$(setting BACKUP_PROTON_MAX_HOURS 6)"
WORK="$(setting BACKUP_WORK_DIR "$STATE")"
REQUIRE_MOUNT="$(setting BACKUP_REQUIRE_MOUNT '')"
# 2026-10-12 (operator): off-box sync caps from the measured uplink ramp -- soft 2.5 MB/s (rclone's own
# limit), hard 3 MB/s (rate_guard.py lowers rclone live, then stops it, if total upload stays above it).
BW_SOFT="$(setting BACKUP_BWLIMIT_SOFT_MB 2.5)"; BW_HARD="$(setting BACKUP_BWLIMIT_HARD_MB 3)"; BW_IFACE="$(setting BACKUP_IFACE wld0)"
# 2026-10-23 (operator): on the METERED failover hotspot the Proton upload still runs (never more than
# ~24 h without a live off-site backup, grid-down included) but at 2 Mbps -- soft 0.22 / hard 0.25 MB/s --
# so SWIM can still ride the link. rate_guard.py follows the default route and switches caps live if the
# uplink changes mid-sync. Every other heavy job defers on the hotspot (scripts/lib/heavy_lock.sh).
BW_M_SOFT="$(setting BACKUP_METERED_BWLIMIT_SOFT_MB 0.22)"; BW_M_HARD="$(setting BACKUP_METERED_BWLIMIT_HARD_MB 0.25)"
DISPATCH_PG=corporatetraveldc-pgsql
NEXTCLOUD_PG=nextcloud-db
# 2026-10-16 (demo/production database split): the DEMO instance, corporatetraveldc-pgsql-demo,
# is deliberately NOT dumped. Everything in it is a scrubbed derivative of the production dump
# above plus demo_profiles (copied once with scrub-demo-source.py --profiles) and demo chat
# history; `scripts/scrub-demo-source.py --backfill` rebuilds it from production in one run.
# Backing it up would only ship a second, weaker copy of production data. docs/BACKUP.md.
BACKUP_EXCLUDED_INSTANCES=(corporatetraveldc-pgsql-demo)   # rebuildable by backfill, never dumped
LADD_TABLES=(faa_ladd_aircraft faa_ladd_removals)   # CUI: identifiers of LADD-listed aircraft
SYNC=1; DRY=0
PRE=0
# 2026-11-26 (operator: off-grid): --sync-only is the hourly catch-up (corporatetraveldc-backup-catchup.timer). No dump,
# no archive: when something on the stick is not yet verified in Proton (a failed nightly push, days of network
# down), it syncs, runs the hash run and the local release. Quiet no-op otherwise; never on the metered hotspot.
SYNC_ONLY=0
for a in "$@"; do case "$a" in --no-sync) SYNC=0 ;; --dry-run) DRY=1 ;; --preflight-only) PRE=1 ;; --sync-only) SYNC_ONLY=1 ;; *) echo "usage: $0 [--no-sync] [--dry-run] [--preflight-only] [--sync-only]" >&2; exit 64 ;; esac; done

log() { printf '[%s] [backup] %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*"; python3 "$LEDGER_PY" append note --json "{\"error\": \"$(echo "$*" | tr -d '"\\' | head -c 300)\"}" >/dev/null 2>&1 || true; exit 1; }

command -v borg >/dev/null || die "borg is not installed (dnf install borgbackup)"
[[ "$KEEP_D" =~ ^[0-9]+$ ]] && (( KEEP_D >= 7 && KEEP_D <= 30 )) || die "BACKUP_KEEP_DAILY=${KEEP_D}: the local copy must keep 7 to 30 nights (operator 11-26)"
[[ -r "$PASSFILE" ]] || die "Borg passphrase file $PASSFILE is missing or unreadable"
[[ "$(stat -c %a "$PASSFILE")" =~ ^[46]00$ ]] || die "$PASSFILE must be mode 0600 or 0400"
# 2026-11-22 (operator: "lock the repo's configs"): no backup secret or its folder may be group/other-accessible
private_dir() { [[ ! -e "$1" ]] || (( ( 8#$(stat -c %a "$1") & 8#077 ) == 0 )) || die "$1 must not be group/other-accessible (chmod 700)"; }
private_file() { [[ ! -e "$1" ]] || [[ "$(stat -c %a "$1")" =~ ^[46]00$ ]] || die "$1 must be mode 0600 or 0400"; }
private_dir "$(dirname "$PASSFILE")"
private_dir "$BACKUP_REPO"
if [[ -n "$BACKUP_TARGET" ]]; then
  private_file "$RCLONE_CONFIG"; private_dir "$(dirname "$RCLONE_CONFIG")"; private_file "$RCLONE_PASS_FILE"
  private_file "$PROTON_SESSION"
  if [[ -r "$RCLONE_PASS_FILE" ]]; then
    export RCLONE_PASSWORD_COMMAND="cat ${RCLONE_PASS_FILE}"
  elif head -n 1 "$RCLONE_CONFIG" 2>/dev/null | grep -q '^# Encrypted rclone configuration'; then
    die "$RCLONE_CONFIG is encrypted but $RCLONE_PASS_FILE is missing or unreadable"
  fi
fi
if [[ -n "$REQUIRE_MOUNT" ]] && ! mountpoint -q "$REQUIRE_MOUNT"; then
  die "backup storage $REQUIRE_MOUNT is not mounted; refusing to write to the disk underneath it"
fi
export BORG_PASSCOMMAND="cat ${PASSFILE}" BORG_REPO="$BACKUP_REPO" BORG_RELOCATED_REPO_ACCESS_IS_OK=no
mkdir -p -m 0700 "$STATE" "$WORK" "$WORK/cui-local" "$WORK/borg-cache"
export BORG_CACHE_DIR="$WORK/borg-cache"
exec 9>"${STATE}/.lock"
if ! flock -n 9; then
  [[ "$SYNC_ONLY" == 1 ]] && { log "catch-up: another backup holds the lock; next hour"; exit 0; }
  die "another backup is running"
fi
# 2026-11-22: a run killed before its cleanup trap leaves its dump stage behind; three of them (94 GiB) filled
# the stick and failed every run from 11-21. With the lock held no other run is using a stage, so any older
# than BACKUP_STAGE_STALE_MIN (6 h; a run takes well under that) is removed here.
while IFS= read -r -d '' old; do
  rm -rf -- "$old"; log "removed stale stage $(basename "$old") (left by an interrupted run)"
done < <(find "$WORK" -mindepth 1 -maxdepth 1 -type d -name 'stage.*' -mmin +"$STAGE_STALE_MIN" -print0 2>/dev/null)

# 2026-10-12: a dry run exits HERE -- before any dump, ledger write or CUI rotation. It used to run both
# Postgres dumps (~35 min) and append a cui-stamp before exiting.
if [[ "$DRY" == 1 ]]; then
  log "dry run: would dump dispatch + Nextcloud Postgres, archive to $BACKUP_REPO, sync to '${BACKUP_TARGET:-<local only>}' with ${SYNC_TOOL} (${PROTON_REMOTE} for proton-cli; soft ${BW_SOFT} MB/s, hard ${BW_HARD} MB/s on ${BW_IFACE})"
  exit 0
fi
# 2026-11-18: the stage-space preflight runs BEFORE waiting for the heavy-work turn, so a run that
# cannot fit fails at once instead of after up to 2 h in the queue (and --preflight-only answers in seconds).
STAGE_GUARD="${REPO_DIR}/scripts/backup/stage_guard.py"
# 2026-11-26 (operator): the 10 GB preflight reserve (a round number set on 11-18) is HELD OFF while the stage
# monitor below measures what a run really needs; the preflight reserve now defaults to 0. Every dump still streams
# through the hard limit, which never lets the stage filesystem drop under STAGE_FLOOR (2 GiB, not a setting):
# a full btrfs is what made 11-18 hard to recover.
MIN_FREE=$(( $(setting BACKUP_MIN_FREE_GB 0) * 1024 * 1024 * 1024 ))
STAGE_FLOOR=$(( 2 * 1024 * 1024 * 1024 ))
STAGE_FACTOR="$(setting BACKUP_STAGE_FACTOR 2.0)"
read -r -a COMPRESS_TABLES <<< "$(setting BACKUP_COMPRESS_TABLES '')"
# 2026-11-26 (operator): table DATA left out of the nightly dump entirely (the schema is still dumped). demo_snapshots,
# the demo recorder, grew 27 -> ~36 GB in 8 days and pushed the stage past the USB; its rows stay in the live database
# on the NVMe, they are just not in the backup. A table here is also dropped from BACKUP_COMPRESS_TABLES.
read -r -a EXCLUDE_TABLES <<< "$(setting BACKUP_EXCLUDE_TABLE_DATA demo_snapshots)"
if (( ${#EXCLUDE_TABLES[@]} > 0 && ${#COMPRESS_TABLES[@]} > 0 )); then
  keep=(); for t in "${COMPRESS_TABLES[@]}"; do [[ " ${EXCLUDE_TABLES[*]} " == *" $t "* ]] || keep+=("$t"); done
  COMPRESS_TABLES=("${keep[@]}")
fi
pg_dump_in() {   # container, output file, extra pg_dump args (single-quoted inside the container shell), [level]
  local c="$1" out="$2" extra="${3:-}" z="${4:-$DUMP_Z}" room
  local keep_free=$(( MIN_FREE > STAGE_FLOOR ? MIN_FREE : STAGE_FLOOR ))
  room=$(( $(python3 "$STAGE_GUARD" free "$(dirname "$out")") - keep_free ))
  (( room > 0 )) || die "stage-guard: no room for $(basename "$out") on $(dirname "$out") (keeps $(( keep_free / 1073741824 )) GiB free)"
  if ! podman exec -i "$c" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc -Z ${z} ${extra}" </dev/null \
       | python3 "$STAGE_GUARD" limit "$out" "$room"; then
    die "dump of $(basename "$out") stopped before it could fill $(dirname "$out") (see stage-guard line above); nothing archived"
  fi
}
psql_in() { podman exec -i "$1" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"$2\"" </dev/null; }

if [[ "$SYNC_ONLY" == 1 ]]; then
  source "$(dirname "${BASH_SOURCE[0]}")/../lib/uplink.sh"
  if uplink_is_metered; then log "catch-up: uplink is metered ($(uplink_default_iface)); waiting for the primary"; exit 0; fi
  [[ -n "$BACKUP_TARGET" && "$SYNC_TOOL" == proton-cli ]] || { log "catch-up: needs BACKUP_TARGET and BACKUP_SYNC_TOOL=proton-cli"; exit 0; }
  if [[ -s "${STATE}/proton-verified.json" ]] && [[ -z "$(podman unshare find "$BACKUP_REPO" -type f -newer "${STATE}/proton-verified.json" ! -name 'lock.*' -print -quit 2>/dev/null)" ]]; then
    log "catch-up: nothing pending (every file on the stick is older than the last verified hash run)"; exit 0
  fi
  log "catch-up: files on the stick are newer than the last verified hash run; syncing"
fi
if [[ "$SYNC_ONLY" != 1 ]]; then
log "preflight: stage space"
db_bytes=$(psql_in "$DISPATCH_PG" "SELECT pg_database_size(current_database())") || die "preflight: cannot read the dispatch database size"
nc_bytes=$(psql_in "$NEXTCLOUD_PG" "SELECT pg_database_size(current_database())") || die "preflight: cannot read the Nextcloud database size"
xt_bytes=0
if (( ${#EXCLUDE_TABLES[@]} > 0 )); then
  xt_bytes=$(psql_in "$DISPATCH_PG" "SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c WHERE c.relname IN ($(printf "'%s'," "${EXCLUDE_TABLES[@]}" | sed 's/,$//'))") || xt_bytes=0
fi
ct_bytes=0
if (( ${#COMPRESS_TABLES[@]} > 0 )); then
  ct_bytes=$(psql_in "$DISPATCH_PG" "SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c WHERE c.relname IN ($(printf "'%s'," "${COMPRESS_TABLES[@]}" | sed 's/,$//'))") || ct_bytes=0
fi
need=$(python3 - "$db_bytes" "$nc_bytes" "$ct_bytes" "$STAGE_FACTOR" "$xt_bytes" <<'PY'
import sys
db, nc, ct, f, xt = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
# uncompressed stage ~ factor x the live size (TOAST is written out decompressed); compressed tables ~0.5 x;
# tables in BACKUP_EXCLUDE_TABLE_DATA are not dumped at all
print(int(max(db - ct - xt, 0) * f + ct * 0.5 + nc * f))
PY
)
if ! python3 "$STAGE_GUARD" preflight "$WORK" "$need" "$MIN_FREE"; then
  die "backup NOT started: the estimated stage (dispatch $(( db_bytes / 1073741824 )) GB live, factor ${STAGE_FACTOR}) does not fit on ${WORK} with the ${MIN_FREE} byte reserve. Free space there, or set BACKUP_COMPRESS_TABLES (docs/BACKUP.md)."
fi
if [[ "$PRE" == 1 ]]; then log "preflight only: the stage fits; nothing dumped"; exit 0; fi
fi   # SYNC_ONLY

# 2026-10-12: one heavy job at a time (backup, restore test, rollout, stack refresh) -- wait our turn.
source "$(dirname "${BASH_SOURCE[0]}")/../lib/heavy_lock.sh"
if [[ "$SYNC_ONLY" == 1 ]]; then
  heavy_lock_acquire "backup catch-up" 60 || { log "catch-up: heavy-work turn taken ($(heavy_lock_holder)); next hour"; exit 0; }
else
  heavy_lock_acquire "nightly backup" 7200 || die "heavy-work turn still taken after 2 h (holder: $(heavy_lock_holder)); backup skipped"
fi

ts=$(date -u +%Y%m%dT%H%M%SZ)
stage=$(mktemp -d "${WORK}/stage.${ts}.XXXX"); chmod 700 "$stage"
# 2026-10-11: plain removal. shred cannot overwrite in place on btrfs (copy-on-write), so it only cost
# ~15 min per run on ~7 GB of dumps; the stage dir is 0700 and its contents are in the encrypted archive.
# 2026-11-03 (operator): a progress line every BACKUP_HEARTBEAT_MIN minutes, so a long dump, archive
# or sync never looks hung -- phase, elapsed, the stage's size on disk, upload rate on the uplink.
# Sizes and rates only; no file names, no values. BACKUP_HEARTBEAT_MIN=0 turns it off.
HEARTBEAT_MIN="$(setting BACKUP_HEARTBEAT_MIN 5)"
phase_file="$stage/.phase"; t0=$(date +%s)
phase() { printf '%s' "$1" > "$phase_file"; }
heartbeat() {
  local a b
  while sleep "$(( HEARTBEAT_MIN * 60 ))"; do
    a=$(awk -v i="${BW_IFACE}:" '$1 == i {print $10}' /proc/net/dev); sleep 10
    b=$(awk -v i="${BW_IFACE}:" '$1 == i {print $10}' /proc/net/dev)
    log "progress: $(cat "$phase_file" 2>/dev/null || echo '?'); $(( ($(date +%s) - t0) / 60 )) min in; stage $(du -sh "$stage" 2>/dev/null | cut -f1); upload $(( (${b:-0} - ${a:-0}) / 10240 )) KiB/s"
  done
}
hb_pid=""
if (( HEARTBEAT_MIN > 0 )); then heartbeat & hb_pid=$!; fi
# 2026-11-26 (operator: "put on a monitor to see how much we actually need"): sample the stage filesystem's free
# space every 15 s through the dumps and borg create; the run records the real peak in the ledger (stage-usage).
minfree_file="${STATE}/.stage-minfree.$$"
python3 "$STAGE_GUARD" free "$WORK" > "$minfree_file" 2>/dev/null || echo 0 > "$minfree_file"
free_monitor() {
  local f m
  while sleep 15; do
    f=$(python3 "$STAGE_GUARD" free "$WORK" 2>/dev/null) || continue
    m=$(cat "$minfree_file" 2>/dev/null || echo "$f")
    (( f < m )) && echo "$f" > "$minfree_file"
  done
}
mon_pid=""
if [[ "$SYNC_ONLY" != 1 ]]; then free_monitor & mon_pid=$!; fi
cleanup() { [[ -n "$hb_pid" ]] && kill "$hb_pid" 2>/dev/null; [[ -n "$mon_pid" ]] && kill "$mon_pid" 2>/dev/null; rm -f "$minfree_file"; rm -rf "$stage"; }
trap cleanup EXIT

if [[ "$SYNC_ONLY" == 1 ]]; then
  archive="catch-up-${ts}"
else
# 2026-11-03 (operator: dedupe in place, uncompressed): the dumps that go INTO Borg are written
# uncompressed (-Z 0, DUMP_Z). A pg_dump -Z 6 stream changes almost entirely every night, so Borg could
# not deduplicate it (2-5 GB a night, docs/BACKUP.md); uncompressed, Borg's content-defined chunks match
# the unchanged tables and Borg's own zstd compresses what is new. The local-only LADD dump never enters
# Borg, so it stays compressed (-Z 6). The stage lives on the USB (BACKUP_WORK_DIR).
DUMP_Z="$(setting BACKUP_DUMP_COMPRESS 0)"
# 2026-11-18: the stage can no longer fill a disk. Every dump streams through stage_guard.py, which stops
# it (and deletes the partial file) once it passes the space left above BACKUP_MIN_FREE_GB, and a
# preflight refuses to start when the estimated stage does not fit. On 11-18 the uncompressed dispatch
# dump hit 52 GB and filled the USB: the database is 38 GB, 27 GB of it demo_snapshots' TOAST, which
# pg_dump writes out decompressed.
# BACKUP_COMPRESS_TABLES (space-separated, default empty): tables whose DATA leaves the uncompressed main
# dump and goes into a second, compressed (-Z 6) dump that is archived alongside it. The data is still
# backed up in full; what is lost for those tables is Borg's deduplication (each night's copy is new data
# for the repository and for the off-box sync). Operator decision -- see docs/BACKUP.md.

phase "1/4 dump: dispatch Postgres"
log "dump: dispatch Postgres (LADD data $( [[ "$INCLUDE_CUI" == 1 ]] && echo included || echo 'excluded -> local-only'))"
main_excl=""
[[ "$INCLUDE_CUI" == 1 ]] || main_excl="$(printf -- '--exclude-table-data=%s ' "${LADD_TABLES[@]}")"
(( ${#COMPRESS_TABLES[@]} == 0 )) || main_excl+="$(printf -- '--exclude-table-data=%s ' "${COMPRESS_TABLES[@]}")"
if (( ${#EXCLUDE_TABLES[@]} > 0 )); then
  main_excl+="$(printf -- '--exclude-table-data=%s ' "${EXCLUDE_TABLES[@]}")"
  log "dump: data of ${EXCLUDE_TABLES[*]} left out (BACKUP_EXCLUDE_TABLE_DATA; the rows stay in the live database)"
fi
pg_dump_in "$DISPATCH_PG" "$stage/dispatch.pgdump" "$main_excl"
if (( ${#COMPRESS_TABLES[@]} > 0 )); then
  phase "1/4 dump: compressed tables"
  log "dump: ${#COMPRESS_TABLES[@]} table(s) compressed (-Z 6, BACKUP_COMPRESS_TABLES)"
  pg_dump_in "$DISPATCH_PG" "$stage/dispatch-compressed.pgdump" "--data-only $(printf -- '--table=%s ' "${COMPRESS_TABLES[@]}")" 6
fi
phase "1/4 dump: Nextcloud"
log "dump: Nextcloud database"
pg_dump_in "$NEXTCLOUD_PG" "$stage/nextcloud.pgdump"

# CUI "#" stamp: local-only LADD dump + its hash in the ledger
cui_file="$WORK/cui-local/ladd-${ts}.pgdump"
pg_dump_in "$DISPATCH_PG" "$cui_file" "--data-only $(printf -- '--table=%s ' "${LADD_TABLES[@]}")" 6
chmod 600 "$cui_file"
count_sql=$(printf "SELECT json_build_object(%s)" "$(printf "'%s', (SELECT count(*) FROM %s)," $(for t in "${LADD_TABLES[@]}"; do echo "$t $t"; done) | sed 's/,$//')")
ladd_rows=$(podman exec -i "$DISPATCH_PG" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"${count_sql}\"" </dev/null)
cui_meta=$(python3 "$LEDGER_PY" file "$cui_file")
# head of the in-database reference-import ledger (pg_schema/0074): pins WHICH LADD/registry source files are loaded
import_head=$(podman exec -i "$DISPATCH_PG" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"SELECT json_build_object('seq', seq, 'entry_hash', entry_hash) FROM reference_import_ledger ORDER BY seq DESC LIMIT 1\"" </dev/null 2>/dev/null || true)
python3 "$LEDGER_PY" append cui-stamp --json "{\"stamp\": \"#\", \"rows\": ${ladd_rows:-null}, \"off_box\": $( [[ "$INCLUDE_CUI" == 1 ]] && echo true || echo false ), \"local_dump\": ${cui_meta}, \"import_ledger_head\": ${import_head:-null}}" >/dev/null
ls -1t "$WORK/cui-local"/ladd-*.pgdump 2>/dev/null | tail -n +$((CUI_KEEP + 1)) | while read -r old; do shred -fu "$old"; done

dumps_meta=$(python3 "$LEDGER_PY" file "$stage/dispatch.pgdump" "$stage/nextcloud.pgdump" $( [[ -f "$stage/dispatch-compressed.pgdump" ]] && echo "$stage/dispatch-compressed.pgdump" ))
cp "${BACKUP_LEDGER:-$STATE/ledger.jsonl}" "$stage/ledger.jsonl"

# 2026-10-24 (persistence audit, operator-approved): every named volume that holds state the operator would lose
# on an image rebuild rides in the archive, read the way nextcloud-html always was (its mountpoint, under
# podman unshare, because the files belong to the container's mapped uids):
#   nextcloud-html                         Nextcloud files
#   corporatetraveldc-ultrafeeder-graphs   graphs1090 collectd RRDs (receiver message-rate / range / aircraft baseline)
#   corporatetraveldc-ultrafeeder-history  readsb + tar1090 /var/globe_history (daily traces, heatmap, internal_state)
#   protonbridge-data                      Proton Bridge identity: vault + keychain (.config, .gnupg, .password-store);
#                                          losing it means a fresh interactive login and a new bridge password for
#                                          every SMTP consumer. Its mail cache, logs and updates are excluded below.
# Postgres data volumes are never read raw: the dispatch and Nextcloud instances are dumped above, the demo
# instance is rebuildable (BACKUP_EXCLUDED_INSTANCES), the office instance is documented as pending (docs/BACKUP.md).
# A volume that does not exist yet (the relay that creates it has not run) is logged and skipped, never fatal.
BACKUP_VOLUMES=(nextcloud-html corporatetraveldc-ultrafeeder-graphs corporatetraveldc-ultrafeeder-history protonbridge-data)
vol_paths=()
for v in "${BACKUP_VOLUMES[@]}"; do
  if mp=$(podman volume inspect "$v" --format '{{.Mountpoint}}' 2>/dev/null) && [[ -n "$mp" ]]; then vol_paths+=("$mp")
  else log "volume ${v} not present -- skipped (once created it is archived from the next run on)"; fi
done
# 2026-10-11: + /var/lib/vnstat (traffic history; unlimited daily retention since 2026-10-11; db is 0644, dir 0755)
# 2026-10-24: + /var/lib/ntfy (auth.db: users, ACLs, access tokens; cache.db: message cache) and /var/lib/openwebui
# (webui.db: accounts, chats, connection settings). Both are host bind mounts that already survived rebuilds but had
# never been archived, so a disk loss would have taken them. Same operator account owns both (readable here).
# 2026-10-24 (operator: "just the operational directories in ~/ and /etc"): config a rebuild needs that is not
# in git or /etc/corporatetraveldc. Missing or unreadable paths are logged and skipped, never fatal. Deliberately
# NOT included: /etc/NetworkManager/system-connections (root-only profiles holding Wi-Fi/hotspot passwords) and
# root-only Pi-hole lists. ~/.config/nextcloud and ~/.config/rclone carry credentials -- they are inside the
# encrypted Borg repository (repokey + passphrase file), same as /etc/corporatetraveldc.
OP_PATHS=(/etc/ntfy /etc/pihole/pihole.toml /etc/pihole/hosts /etc/nftables.d /etc/nginx/conf.d /etc/fail2ban/jail.d /etc/unbound
          "${HOME}/.config/nextcloud" "${HOME}/.config/containers/systemd" "${HOME}/.config/systemd/user"
          "${HOME}/.config/rclone" "${HOME}/demos" "${HOME}/.local/state/corporatetraveldc")
for _u in /etc/systemd/system/corporatetraveldc-*; do [[ -e "$_u" ]] && OP_PATHS+=("$_u"); done
op_paths=()
for _p in "${OP_PATHS[@]}"; do
  if [[ -r "$_p" ]]; then op_paths+=("$_p"); else log "operational path skipped (missing or unreadable): $_p"; fi
done
paths=("$stage" "${vol_paths[@]}" /var/lib/corporatetraveldc /etc/corporatetraveldc /var/lib/vnstat /var/lib/ntfy /var/lib/openwebui "${op_paths[@]}")
# 2026-10-11: root-only files the operator account cannot read (re-issuable credentials held for
# root services) are excluded rather than letting Borg warn on them every night.
# 2026-10-24: Proton Bridge's local mail cache (gluon, ~1.3 GB, re-synced from Proton on login), its logs (~190 MB)
# and downloaded updates (~200 MB), Open WebUI's derived cache, and tar1090's own git checkout
# (globe_history/tar1090-update, re-cloned by the image) are not state anyone would lose -- excluded.
excludes=(--exclude /var/lib/corporatetraveldc/models --exclude 'sh:**/__pycache__' --exclude 'sh:**/*.tmp'
          --exclude /etc/corporatetraveldc/cf-honeypot.token
          --exclude 'sh:**/protonbridge-data/_data/.local/share/protonmail/bridge-v3/gluon'
          --exclude 'sh:**/protonbridge-data/_data/.local/share/protonmail/bridge-v3/logs'
          --exclude 'sh:**/protonbridge-data/_data/.local/share/protonmail/bridge-v3/updates'
          --exclude 'sh:**/protonbridge-data/_data/.cache'
          --exclude /var/lib/openwebui/cache
          --exclude 'sh:**/corporatetraveldc-ultrafeeder-history/_data/tar1090-update')


if [[ ! -d "$BACKUP_REPO" ]]; then
  log "initialising Borg repository (repokey, AES-256 + HMAC-SHA256) at $BACKUP_REPO"
  mkdir -p -m 0700 "$(dirname "$BACKUP_REPO")"
  borg init --encryption=repokey "$BACKUP_REPO"
fi

archive="ctdi-${ts}"
stage_bytes=$(du -sb "$stage" 2>/dev/null | cut -f1)
phase "2/4 borg create (archive on ${BACKUP_REPO})"
log "borg create ::${archive}"
# podman unshare: the Nextcloud volume belongs to the container's mapped uids
# Borg exit codes: 0 ok, 1 WARNING (archive written; e.g. an unreadable file), >=2 error.
# 2026-10-11: a warning no longer aborts the whole backup -- it is logged and recorded in the ledger.
set +e
stats=$(podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" \
  borg create --compression zstd,6 --one-file-system --json "${excludes[@]}" "::${archive}" "${paths[@]}" 2>"$stage/borg-create.err")
brc=$?
set -e
borg_warn=0
if (( brc == 1 )); then borg_warn=1; log "borg create finished with WARNINGS (archive written): $(tr '\n' ' ' < "$stage/borg-create.err" | head -c 400)"
elif (( brc >= 2 )); then cat "$stage/borg-create.err" >&2; die "borg create failed (rc=$brc)"; fi
arch_id=$(BORG_STATS="$stats" python3 - <<'PY'
# 2026-10-11: stats come via the environment -- the heredoc IS stdin here, so a pipe was silently replaced.
import json, os
d = json.loads(os.environ.get("BORG_STATS") or "{}"); a = d.get("archive", {})
print(json.dumps({"id": a.get("id"), "name": a.get("name"), "original_size": a.get("stats", {}).get("original_size"),
                  "compressed_size": a.get("stats", {}).get("compressed_size"),
                  "deduplicated_size": a.get("stats", {}).get("deduplicated_size"), "nfiles": a.get("stats", {}).get("nfiles")}))
PY
)
# the stage monitor's result for this run: estimate vs what the dumps really used, and the lowest free space seen
[[ -n "$mon_pid" ]] && { kill "$mon_pid" 2>/dev/null; mon_pid=""; }
start_free=$(head -n 1 "$minfree_file" 2>/dev/null || echo 0)
python3 "$LEDGER_PY" append stage-usage --json "{\"archive\": \"${archive}\", \"estimate_bytes\": ${need:-0}, \"stage_bytes\": ${stage_bytes:-0}, \"min_free_bytes\": ${start_free:-0}, \"reserve_bytes\": ${MIN_FREE}, \"floor_bytes\": ${STAGE_FLOOR}, \"excluded\": \"${EXCLUDE_TABLES[*]:-}\"}" >/dev/null || true
log "stage monitor: dumps used $(( ${stage_bytes:-0} / 1073741824 )) GiB (estimate $(( ${need:-0} / 1073741824 )) GiB); lowest free on ${WORK} during the run: $(( ${start_free:-0} / 1073741824 )) GiB"
# 2026-11-26 (operator): `borg prune` only marks archives; `borg compact` is what deletes files on the stick. It
# now runs AFTER the off-box sync, and only when the hash run has verified every local file in Proton by sha1
# (local_release below). Until then the stick keeps everything: it is the network-down, portable backup.
phase "3/4 prune"
podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" \
  borg prune --keep-daily "$KEEP_D" --keep-weekly "$KEEP_W" --keep-monthly "$KEEP_M" --glob-archives 'ctdi-*'
python3 "$LEDGER_PY" append backup --json "{\"archive\": ${arch_id}, \"dumps\": ${dumps_meta}, \"include_cui\": $( [[ "$INCLUDE_CUI" == 1 ]] && echo true || echo false ), \"borg_warnings\": $( (( borg_warn )) && echo true || echo false ), \"repo\": \"local\"}" >/dev/null
log "archive ${archive} done"

# 2026-11-26 (operator: off-grid 7-30 days): how many nights the stick can keep archiving with the network down.
# Offline nothing is compacted (the local release needs a verified Proton copy), so each night adds about one
# archive's deduplicated size; each run also needs the dump stage plus the reserve free. Recorded in the ledger;
# backup_freshness.py alerts when the estimate is under BACKUP_OFFLINE_MIN_DAYS.
OFF_MIN="$(setting BACKUP_OFFLINE_MIN_DAYS 7)"; OFF_TARGET="$(setting BACKUP_OFFLINE_TARGET_DAYS 30)"
free_b=$(df -B1 --output=avail "$BACKUP_REPO" 2>/dev/null | tail -n 1 | tr -d ' ')
if hr="$(python3 - "$LEDGER_PY" "${free_b:-0}" "${need:-0}" "$MIN_FREE" "$OFF_MIN" "$OFF_TARGET" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("bl", sys.argv[1]); bl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bl)
free, need, reserve, lo, target = (int(float(x)) for x in sys.argv[2:7])
g = [e["data"]["archive"].get("deduplicated_size") for e in bl.entries() if e.get("kind") == "backup"
     and isinstance(e.get("data", {}).get("archive"), dict)]
g = [x for x in g if isinstance(x, (int, float)) and x > 0][-7:]
avg = sum(g) / len(g) if g else 0
room = free - need - reserve
days = int(room // avg) if avg > 0 else None
print(json.dumps({"free_bytes": free, "stage_need_bytes": need, "reserve_bytes": reserve,
                  "avg_nightly_growth_bytes": int(avg), "nights_sampled": len(g), "offline_days": days,
                  "min_days": lo, "target_days": target, "ok": days is None or days >= lo}, separators=(",", ":")))
PY
)"; then
  python3 "$LEDGER_PY" append offline-headroom --json "$hr" >/dev/null || true
  log "off-grid headroom: $(python3 - "$hr" <<'PY'
import json, sys
h = json.loads(sys.argv[1]); d = h["offline_days"]
print(("unknown (no growth history yet)" if d is None else f"about {d} night(s) of archives with the network down")
      + f"; free {h['free_bytes'] / 1073741824:.1f} GiB, stage {h['stage_need_bytes'] / 1073741824:.1f} GiB,"
      f" nightly growth {h['avg_nightly_growth_bytes'] / 1073741824:.2f} GiB; minimum {h['min_days']}, target {h['target_days']}"
      + ("" if h["ok"] else "  ** BELOW THE MINIMUM **"))
PY
)"
fi
fi   # SYNC_ONLY (catch-up skips dump, archive, prune and headroom)

if [[ "$SYNC" == 1 && -n "$BACKUP_TARGET" && "$SYNC_GATE" == 1 ]]; then
  # the weekly signed gate (same machinery as the liveness switch). A lapse HOLDS the sync: the archive is
  # complete on the stick, nothing on the target is changed or deleted, and a new signature is requested.
  if gate_out="$($SYNC_GATE_CMD check </dev/null 2>&1)"; then
    log "sync gate: ${gate_out}"
  else
    SYNC=0
    log "sync HELD: no signed backup-sync approval in the last 7 days (${gate_out:-gate check failed}); archive ${archive} is on the stick; ${BACKUP_TARGET} unchanged"
    python3 "$LEDGER_PY" append sync --json "{\"target\": \"${BACKUP_TARGET}\", \"archive\": \"${archive}\", \"ok\": false, \"held\": \"weekly-signature\"}" >/dev/null
    log "$($SYNC_GATE_CMD request --target "$BACKUP_TARGET" </dev/null 2>&1 || echo 'could not request a new signature')"
  fi
fi
VERIFIED_MANIFEST="${STATE}/proton-verified.json"
RELEASE_VERIFIED_IN=""
release_skipped() {   # $1 = why; recorded, nothing deleted
  log "local release skipped: $1 (the stick keeps every file)"
  python3 "$LEDGER_PY" append local-release --json "{\"archive\": \"${archive}\", \"released\": false, \"why\": \"$(echo "$1" | tr -d '"\\' | head -c 200)\"}" >/dev/null || true
}
local_release() {   # $1 = where the data is verified; borg compact, then the audit entry of what it removed
  local before after
  before="$(mktemp "${STATE}/release.before.XXXX")"; after="$(mktemp "${STATE}/release.after.XXXX")"
  podman unshare find "$BACKUP_REPO" -type f -printf '%P\t%s\n' | sort > "$before"
  if ! podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" borg compact; then
    rm -f "$before" "$after"; release_skipped "borg compact failed"; return 0
  fi
  podman unshare find "$BACKUP_REPO" -type f -printf '%P\t%s\n' | sort > "$after"
  local audit
  audit="$(python3 - "$VERIFIED_MANIFEST" "$before" "$after" "$1" "$archive" <<'PY'
import json, sys
manifest, before, after, where, archive = sys.argv[1:6]
m = json.load(open(manifest))
rows = lambda f: dict(l.rstrip("\n").split("\t", 1) for l in open(f) if "\t" in l)
b, a = rows(before), rows(after)
gone = sorted(set(b) - set(a))
files = [[f, m.get(f), int(b[f])] for f in gone]
out = {"archive": archive, "released": True, "removed_files": len(gone), "removed_bytes": sum(int(b[f]) for f in gone),
       "unverified_removed": [f for f in gone if f not in m],
       "verified_in": where, "files": files[:500], "files_truncated": len(files) > 500,
       "note": "local space preservation: removed from the stick by borg compact only after the hash run verified "
               "every local file in " + where + " by sha1; the Proton copy is append-only and keeps them"}
print(json.dumps(out, separators=(",", ":")))
PY
)" || { rm -f "$before" "$after"; release_skipped "audit of the compaction failed"; return 0; }
  rm -f "$before" "$after"
  python3 "$LEDGER_PY" append local-release --json "$audit" >/dev/null
  log "local release: borg compact ran; $(python3 - "$audit" <<'PY'
import json, sys
a = json.loads(sys.argv[1])
print(f"{a['removed_files']} file(s), {a['removed_bytes'] / 1073741824:.2f} GiB removed from the stick, all verified in {a['verified_in']}"
      + (f"; WARNING {len(a['unverified_removed'])} not in the verified manifest" if a["unverified_removed"] else ""))
PY
)"
}
SYNC_VIA_RCLONE=0
if [[ "$SYNC" == 1 && -n "$BACKUP_TARGET" ]]; then
  # 2026-10-12: never sync on top of the one-off seed upload (same target, same files).
  for _ in $(seq 1 90); do systemctl --user is-active -q corporatetraveldc-proton-seed.service || break; log "seed upload still running; waiting"; sleep 60; done
  systemctl --user is-active -q corporatetraveldc-proton-seed.service && die "seed upload still running after 90 min; sync skipped"
  SYNC_VIA_RCLONE=1
  if [[ "$SYNC_TOOL" == proton-cli ]]; then
    if uplink_is_metered; then
      log "uplink is METERED ($(uplink_default_iface)): the Proton CLI has no live rate cap, so this sync uses rclone at the metered cap"
    else
      SYNC_VIA_RCLONE=0
      phase "4/4 sync to Proton Drive ${PROTON_REMOTE} (official CLI)"
      stop_args=(); for m in $(uplink_metered_ifaces); do stop_args+=(--stop-if-iface "$m"); done
      pdir="/run/user/$(id -u)/ctdc-proton.$$"
      plog="${STATE}/proton-sync.last.log"
      rm -f "$VERIFIED_MANIFEST"        # a manifest left by an earlier run never counts
      set +e
      bash "$(dirname "$0")/proton_session.sh" open "$PROTON_SESSION" "$pdir"; prc=$?
      if (( prc == 0 )); then
        log "sync repository -> ${PROTON_REMOTE} (average capped at ${BW_SOFT} MB/s; up to ${PROTON_MAX_HOURS} h)"
        podman unshare env PROTON_DRIVE_CREDENTIALS_STORE=unsafe_file PROTON_DRIVE_CACHE_DIR="$pdir" \
          python3 "$(dirname "$0")/proton_sync.py" --repo "$BACKUP_REPO" --remote "$PROTON_REMOTE" \
            --cli "$PROTON_CLI" --cli-sha512 "$PROTON_CLI_SHA512" --avg-cap-mb "$BW_SOFT" \
            --max-hours "$PROTON_MAX_HOURS" --verify --hash-cache "${STATE}/proton-sha1-cache.json" \
            --manifest-out "$VERIFIED_MANIFEST" "${stop_args[@]}" </dev/null 2>&1 | tee "$plog"
        prc=${PIPESTATUS[0]}
        # the CLI refreshes its session while it works: always keep the refreshed one
        bash "$(dirname "$0")/proton_session.sh" save "$PROTON_SESSION" "$pdir" \
          || { log "WARNING: could not save the refreshed Proton session; the next run may need a new sign-in"; bash "$(dirname "$0")/proton_session.sh" wipe "$pdir"; }
        sum_json="$(tail -n 1 "$plog")"
      else
        prc=3; sum_json='{"error": "no usable Proton session (sign in with the push relay)"}'
      fi
      set -e
      [[ "$sum_json" == \{*\} ]] || sum_json=null
      case "$prc" in
        0) python3 "$LEDGER_PY" append sync --json "{\"target\": \"${PROTON_REMOTE}\", \"tool\": \"proton-cli\", \"archive\": \"${archive}\", \"ok\": true, \"verified\": true, \"proton_sync\": ${sum_json}}" >/dev/null
           RELEASE_VERIFIED_IN="${PROTON_REMOTE}" ;;
        5) log "the uplink turned metered during the sync; finishing with rclone at the metered cap"; SYNC_VIA_RCLONE=1 ;;
        *) python3 "$LEDGER_PY" append sync --json "{\"target\": \"${PROTON_REMOTE}\", \"tool\": \"proton-cli\", \"archive\": \"${archive}\", \"ok\": false, \"exit\": ${prc}, \"proton_sync\": ${sum_json}}" >/dev/null
           release_skipped "sync or hash run did not pass (proton_sync exit ${prc})"
           [[ "$SYNC_ONLY" == 1 ]] && { log "catch-up: sync did not complete (exit ${prc}); next hour"; exit 0; }
           die "sync to ${PROTON_REMOTE} failed (proton_sync exit ${prc}: 4 = stall or time budget, 6 = hash run failed, 3 = refused; the local archive is complete and nothing was deleted on the stick; the next run resumes)" ;;
      esac
    fi
  fi
fi
if [[ "$SYNC_VIA_RCLONE" == 1 ]]; then
  command -v rclone >/dev/null || die "rclone is not installed (dnf install rclone)"
  phase "4/4 sync to ${BACKUP_TARGET}"
  log "sync repository -> ${BACKUP_TARGET} (primary soft ${BW_SOFT} / hard ${BW_HARD} MB/s on ${BW_IFACE}; metered soft ${BW_M_SOFT} / hard ${BW_M_HARD} MB/s)"
  # 2026-10-14: a sync the rate guard stops for a STALL (no bytes moved for 20 min) is retried
  # once from scratch -- that also covers Proton's transient "Upload file empty" (2003).
  metered_args=(); for m in $(uplink_metered_ifaces); do metered_args+=(--metered-iface "$m"); done
  for attempt in 1 2; do
    # start rclone at the caps of the uplink in use NOW (the retry may start on the other one)
    start_soft="$BW_SOFT"; uplink_note="primary"
    if uplink_is_metered; then start_soft="$BW_M_SOFT"; uplink_note="METERED $(uplink_default_iface), 2 Mbps cap"; fi
    log "sync attempt ${attempt}: starting at ${start_soft} MB/s (${uplink_note})"
    soft_k=$(python3 -c "print(int(float('${start_soft}')*1000000/1024))")k
    rcdir=$(mktemp -d "${STATE}/rc.XXXX"); chmod 700 "$rcdir"
    # 2026-11-26: copy, never sync -- `rclone sync` deletes target files that are gone locally; Proton is append-only.
    podman unshare rclone --config "$RCLONE_CONFIG" copy --checksum "$BACKUP_REPO" "$BACKUP_TARGET" --stats-one-line --stats 0 \
      --bwlimit "$soft_k" --rc --rc-addr "unix://${rcdir}/rc.sock" --rc-no-auth \
      --transfers "$RCLONE_TRANSFERS" --checkers 2 \
      --protondrive-replace-existing-draft </dev/null &   # 2026-10-12: an interrupted upload leaves a Proton draft that blocks the next sync
    rpid=$!
    set +e
    guard_json=$(python3 "$(dirname "$0")/rate_guard.py" --pid "$rpid" --rc-sock "${rcdir}/rc.sock" --iface "$BW_IFACE" --soft-mb "$BW_SOFT" --hard-mb "$BW_HARD" "${metered_args[@]}" --metered-soft-mb "$BW_M_SOFT" --metered-hard-mb "$BW_M_HARD" </dev/null); grc=$?
    wait "$rpid"; src=$?; set -e; rm -rf "$rcdir"
    log "rate guard (attempt ${attempt}): ${guard_json:-<no summary>}"
    # 2026-11-22: the guard stops rclone with `rc core/quit`, after which rclone exits 0 -- so rclone's code alone
    # said "done" for a sync the guard had STOPPED (11-22 20:08Z: stall after 2.85 GB, no retry, ledger ok=true,
    # Proton incomplete). A sync counts only when rclone exited 0 AND the guard did not stop it (grc 0).
    if (( src == 0 && grc == 0 )) || (( grc != 4 )) || (( attempt == 2 )); then break; fi
    log "sync stalled (guard stopped rclone); retrying once"
  done
  if (( src == 0 && grc == 0 )); then
    python3 "$LEDGER_PY" append sync --json "{\"target\": \"${BACKUP_TARGET}\", \"archive\": \"${archive}\", \"ok\": true, \"rate_guard\": ${guard_json:-null}}" >/dev/null
  else
    python3 "$LEDGER_PY" append sync --json "{\"target\": \"${BACKUP_TARGET}\", \"archive\": \"${archive}\", \"ok\": false}" >/dev/null
    die "sync to ${BACKUP_TARGET} failed (the local archive is complete)"
  fi
fi
if [[ -n "$RELEASE_VERIFIED_IN" && -s "$VERIFIED_MANIFEST" ]]; then
  phase "5/5 local release"
  local_release "$RELEASE_VERIFIED_IN"
elif [[ "$SYNC_VIA_RCLONE" == 1 ]]; then
  release_skipped "the rclone path runs no sha1 hash run"
else
  release_skipped "no verified off-box copy this run (sync off, held, or no target)"
fi
python3 "$LEDGER_PY" verify
