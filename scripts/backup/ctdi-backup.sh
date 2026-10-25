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
for a in "$@"; do case "$a" in --no-sync) SYNC=0 ;; --dry-run) DRY=1 ;; *) echo "usage: $0 [--no-sync] [--dry-run]" >&2; exit 64 ;; esac; done

log() { printf '[%s] [backup] %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*"; python3 "$LEDGER_PY" append note --json "{\"error\": \"$(echo "$*" | tr -d '"\\' | head -c 300)\"}" >/dev/null 2>&1 || true; exit 1; }

command -v borg >/dev/null || die "borg is not installed (dnf install borgbackup)"
[[ -r "$PASSFILE" ]] || die "Borg passphrase file $PASSFILE is missing or unreadable"
[[ "$(stat -c %a "$PASSFILE")" =~ ^[46]00$ ]] || die "$PASSFILE must be mode 0600 or 0400"
export BORG_PASSCOMMAND="cat ${PASSFILE}" BORG_REPO="$BACKUP_REPO" BORG_RELOCATED_REPO_ACCESS_IS_OK=no
mkdir -p -m 0700 "$STATE" "$STATE/cui-local"
exec 9>"${STATE}/.lock"; flock -n 9 || die "another backup is running"

# 2026-10-12: a dry run exits HERE -- before any dump, ledger write or CUI rotation. It used to run both
# Postgres dumps (~35 min) and append a cui-stamp before exiting.
if [[ "$DRY" == 1 ]]; then
  log "dry run: would dump dispatch + Nextcloud Postgres, archive to $BACKUP_REPO, sync to '${BACKUP_TARGET:-<local only>}' (soft ${BW_SOFT} MB/s, hard ${BW_HARD} MB/s on ${BW_IFACE})"
  exit 0
fi
# 2026-10-12: one heavy job at a time (backup, restore test, rollout, stack refresh) -- wait our turn.
source "$(dirname "${BASH_SOURCE[0]}")/../lib/heavy_lock.sh"
heavy_lock_acquire "nightly backup" 7200 || die "heavy-work turn still taken after 2 h (holder: $(heavy_lock_holder)); backup skipped"

ts=$(date -u +%Y%m%dT%H%M%SZ)
stage=$(mktemp -d "${STATE}/stage.${ts}.XXXX"); chmod 700 "$stage"
# 2026-10-11: plain removal. shred cannot overwrite in place on btrfs (copy-on-write), so it only cost
# ~15 min per run on ~7 GB of dumps; the stage dir is 0700 and its contents are in the encrypted archive.
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT

pg_dump_in() {   # container, output file, extra pg_dump args (single-quoted inside the container shell)
  local c="$1" out="$2" extra="${3:-}"
  podman exec -i "$c" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc -Z 6 ${extra}" > "$out" </dev/null
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
ladd_rows=$(podman exec -i "$DISPATCH_PG" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"${count_sql}\"" </dev/null)
cui_meta=$(python3 "$LEDGER_PY" file "$cui_file")
# head of the in-database reference-import ledger (pg_schema/0074): pins WHICH LADD/registry source files are loaded
import_head=$(podman exec -i "$DISPATCH_PG" sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"SELECT json_build_object('seq', seq, 'entry_hash', entry_hash) FROM reference_import_ledger ORDER BY seq DESC LIMIT 1\"" </dev/null 2>/dev/null || true)
python3 "$LEDGER_PY" append cui-stamp --json "{\"stamp\": \"#\", \"rows\": ${ladd_rows:-null}, \"off_box\": $( [[ "$INCLUDE_CUI" == 1 ]] && echo true || echo false ), \"local_dump\": ${cui_meta}, \"import_ledger_head\": ${import_head:-null}}" >/dev/null
ls -1t "$STATE/cui-local"/ladd-*.pgdump 2>/dev/null | tail -n +$((CUI_KEEP + 1)) | while read -r old; do shred -fu "$old"; done

dumps_meta=$(python3 "$LEDGER_PY" file "$stage/dispatch.pgdump" "$stage/nextcloud.pgdump")
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
podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" \
  borg prune --keep-daily "$KEEP_D" --keep-weekly "$KEEP_W" --keep-monthly "$KEEP_M" --glob-archives 'ctdi-*'
podman unshare env BORG_PASSCOMMAND="$BORG_PASSCOMMAND" BORG_REPO="$BORG_REPO" borg compact
python3 "$LEDGER_PY" append backup --json "{\"archive\": ${arch_id}, \"dumps\": ${dumps_meta}, \"include_cui\": $( [[ "$INCLUDE_CUI" == 1 ]] && echo true || echo false ), \"borg_warnings\": $( (( borg_warn )) && echo true || echo false ), \"repo\": \"local\"}" >/dev/null
log "archive ${archive} done"

if [[ "$SYNC" == 1 && -n "$BACKUP_TARGET" ]]; then
  command -v rclone >/dev/null || die "rclone is not installed (dnf install rclone)"
  # 2026-10-12: never sync on top of the one-off seed upload (same target, same files).
  for _ in $(seq 1 90); do systemctl --user is-active -q corporatetraveldc-proton-seed.service || break; log "seed upload still running; waiting"; sleep 60; done
  systemctl --user is-active -q corporatetraveldc-proton-seed.service && die "seed upload still running after 90 min; sync skipped"
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
    podman unshare rclone --config "$RCLONE_CONFIG" sync --checksum "$BACKUP_REPO" "$BACKUP_TARGET" --stats-one-line --stats 0 \
      --bwlimit "$soft_k" --rc --rc-addr "unix://${rcdir}/rc.sock" --rc-no-auth \
      --protondrive-replace-existing-draft </dev/null &   # 2026-10-12: an interrupted upload leaves a Proton draft that blocks the next sync
    rpid=$!
    set +e
    guard_json=$(python3 "$(dirname "$0")/rate_guard.py" --pid "$rpid" --rc-sock "${rcdir}/rc.sock" --iface "$BW_IFACE" --soft-mb "$BW_SOFT" --hard-mb "$BW_HARD" "${metered_args[@]}" --metered-soft-mb "$BW_M_SOFT" --metered-hard-mb "$BW_M_HARD" </dev/null); grc=$?
    wait "$rpid"; src=$?; set -e; rm -rf "$rcdir"
    log "rate guard (attempt ${attempt}): ${guard_json:-<no summary>}"
    if (( src == 0 )) || (( grc != 4 )) || (( attempt == 2 )); then break; fi
    log "sync stalled; retrying once"
  done
  if (( src == 0 )); then
    python3 "$LEDGER_PY" append sync --json "{\"target\": \"${BACKUP_TARGET}\", \"archive\": \"${archive}\", \"ok\": true, \"rate_guard\": ${guard_json:-null}}" >/dev/null
  else
    python3 "$LEDGER_PY" append sync --json "{\"target\": \"${BACKUP_TARGET}\", \"archive\": \"${archive}\", \"ok\": false}" >/dev/null
    die "sync to ${BACKUP_TARGET} failed (the local archive is complete)"
  fi
fi
python3 "$LEDGER_PY" verify
