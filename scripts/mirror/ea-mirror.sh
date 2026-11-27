#!/usr/bin/env bash
# scripts/mirror/ea-mirror.sh -- the operator account's read mirror for the EA (operator 2026-11-26).
#
# Why: the EA account cannot read the operator's journals, the backup ledger, the Borg list, the SWIM census or its
# samples, and the operator kept running one-off relays just to show it. Standing rule (operator 11-26): anything the
# EA needs to read natively goes in here instead. Runs every 30 min (idle priority) from corporatetraveldc-ea-mirror.timer as the
# operator, and publishes into /srv/ctdc-ea/mirror/ (the operator + EA private folder; default ACLs give the EA read).
#
# Never a secret: everything is collected into a private working copy (~/.local/state/ea-mirror/work, 0700), then
# scripts/leak-guard/transcript_leak_scan.py REDACTS it (every live secret value, token shapes, LADD identifiers,
# receiver coordinates) and SCANS it again; only a clean copy is published. A scan that still finds anything, or a
# LADD/coords class that could not be checked, publishes nothing and fails the unit (OnFailure alerts).
#
# Contents (all text; .txt/.log so the scanner reads them):
#   journal/user/<unit>.log        corporatetraveldc-* user units, incremental (journal cursor), newest 3000 lines each
#   journal/system/                boots.txt, previous-boot-tail.log, kernel-warnings{,-previous-boot}.log
#   backup/                        ledger-tail.jsonl (400 lines), proton-sync.last.log, borg-archives.txt (hourly)
#   census/unparsed-7d.txt         SWIM types the census saw but no parser handled, per feed, last 7 days
#   census/feeds-24h.txt           per feed: handled / unhandled message counts, last 24 h
#   census/samples/<feed>/<day>/   the census's own (already redacted) samples, last 3 days, as .xml.txt
#   db/table-sizes.txt, db/demo-snapshots-age.txt
#   UPDATED                        when this run published
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="${REPO_DIR}/scripts/mirror"
DEST="${EA_MIRROR_DIR:-/srv/ctdc-ea/mirror}"
PRIV="${HOME}/.local/state/ea-mirror"
W="${PRIV}/work"
SAMPLES="${SWIM_SAMPLE_DIR:-/var/lib/corporatetraveldc/swim-samples}"
BSTATE="${HOME}/.local/state/ctdc-backup"
ENV_FILE=/etc/corporatetraveldc/dispatch.env
log() { printf '[%s] [ea-mirror] %s\n' "$(date -u +%FT%TZ)" "$*"; }
envval() { grep -m1 "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true; }   # parsed, never sourced
psql_q() {   # $1 = SQL file; tab-free " | " separated rows
  podman exec -i corporatetraveldc-pgsql sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -w -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F " | " -v ON_ERROR_STOP=1' < "$1"
}

install -d -m 0700 "$PRIV" "$W" "$W/journal/user" "$W/journal/system" "$W/backup" "$W/census" "$W/db"
exec 7>"${PRIV}/.lock"; flock -n 7 || { log "another mirror run is active"; exit 0; }

# ---- journals (incremental through the journal cursor; each unit file keeps its newest lines) ----------------
# journalctl takes ONE of --since / --cursor-file (2026-11-26: both together failed every run). The first run reads
# the last 26 h and then records where the journal ends; every later run continues from that cursor.
JF=(-o json --output-fields=_SYSTEMD_USER_UNIT,MESSAGE --no-pager -q)
if [[ -s "${PRIV}/user.cursor" ]]; then
  journalctl --user --cursor-file="${PRIV}/user.cursor" "${JF[@]}" | python3 "${HERE}/ea_mirror_split.py" "$W/journal/user" 3000
else
  journalctl --user --since -26h "${JF[@]}" | python3 "${HERE}/ea_mirror_split.py" "$W/journal/user" 3000
  journalctl --user -n 1 --cursor-file="${PRIV}/user.cursor" -o cat --no-pager -q >/dev/null
fi
find "$W/journal/user" -name '*.log' ! -name 'corporatetraveldc-*' -delete 2>/dev/null || true
journalctl --list-boots --no-pager > "$W/journal/system/boots.txt" 2>&1 || true
journalctl -b -1 -n 300 -o short-iso --no-pager -q > "$W/journal/system/previous-boot-tail.log" 2>&1 || true
journalctl -k -b 0 -p warning -o short-iso --no-pager -q 2>/dev/null | tail -n 500 > "$W/journal/system/kernel-warnings.log" || true
journalctl -k -b -1 -p warning -o short-iso --no-pager -q 2>/dev/null | tail -n 500 > "$W/journal/system/kernel-warnings-previous-boot.log" || true

# ---- backup state ---------------------------------------------------------------------------------------------
[[ -f "$BSTATE/ledger.jsonl" ]] && tail -n 400 "$BSTATE/ledger.jsonl" > "$W/backup/ledger-tail.jsonl"
[[ -f "$BSTATE/proton-sync.last.log" ]] && cp "$BSTATE/proton-sync.last.log" "$W/backup/proton-sync.last.log"
# borg list takes the repository lock; a `borg create` starting at that instant would fail. So: once a day, only
# 12:00-20:00Z (the nightly runs 03:40-03:50Z), and only while holding the backup's own lock (the hourly catch-up
# steps aside quietly when that lock is held).
hour=$(date -u +%H)
if (( 10#$hour >= 12 && 10#$hour < 20 )) && { [[ ! -f "$W/backup/borg-archives.txt" ]] || [[ -n "$(find "$W/backup/borg-archives.txt" -mmin +1380 2>/dev/null)" ]]; }; then
  R="$(envval BACKUP_REPO)"; R="${R:-$BSTATE/borg}"
  P="$(envval BACKUP_PASSPHRASE_FILE)"; P="${P:-/etc/corporatetraveldc/backup/borg-passphrase}"
  if out="$(flock -n "$BSTATE/.lock" podman unshare env BORG_PASSCOMMAND="cat $P" BORG_REPO="$R" borg list --lock-wait 5 --format '{time} {archive}{NL}' 2>/dev/null)"; then
    { echo "# borg list at $(date -u +%FT%TZ): $(grep -c . <<< "$out") archive(s)"; printf '%s\n' "$out"; } > "$W/backup/borg-archives.txt"
  fi                                   # a backup holds the lock: keep the previous list
fi

# ---- SWIM census + database sizes (SELECT only) --------------------------------------------------------------
Q="$(mktemp "${PRIV}/q.XXXX")"
cat > "$Q" <<'SQL'
SELECT feed, msg_type, sum(n) AS msgs,
       to_char(to_timestamp(min(hour_start)) AT TIME ZONE 'UTC', 'MM-DD HH24"Z"') AS first_seen,
       to_char(to_timestamp(max(hour_start)) AT TIME ZONE 'UTC', 'MM-DD HH24"Z"') AS last_seen
FROM swim_message_census
WHERE handled = 0 AND feed NOT LIKE '%@dest%' AND hour_start >= extract(epoch FROM now() - interval '7 days')
GROUP BY feed, msg_type ORDER BY feed, msgs DESC;
SQL
{ echo "# feed | msg_type | msgs | first_seen | last_seen   (unhandled, last 7 days, at $(date -u +%FT%TZ))"; psql_q "$Q"; } > "$W/census/unparsed-7d.txt" 2>&1 || true
cat > "$Q" <<'SQL'
SELECT feed, sum(n) FILTER (WHERE handled = 1) AS handled, sum(n) FILTER (WHERE handled = 0) AS unhandled
FROM swim_message_census WHERE feed NOT LIKE '%@dest%' AND hour_start >= extract(epoch FROM now() - interval '24 hours')
GROUP BY feed ORDER BY feed;
SQL
{ echo "# feed | handled | unhandled   (last 24 h)"; psql_q "$Q"; } > "$W/census/feeds-24h.txt" 2>&1 || true
cat > "$Q" <<'SQL'
SELECT relname, pg_total_relation_size(c.oid) AS bytes, pg_size_pretty(pg_total_relation_size(c.oid))
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY 2 DESC LIMIT 30;
SELECT 'database total', pg_database_size(current_database()), pg_size_pretty(pg_database_size(current_database()));
SQL
{ echo "# table | bytes | size   (dispatch database, at $(date -u +%FT%TZ))"; psql_q "$Q"; } > "$W/db/table-sizes.txt" 2>&1 || true
cat > "$Q" <<'SQL'
WITH s AS (SELECT left(captured_at, 10) AS day, count(*) AS n, sum(octet_length(payload)) AS b FROM demo_snapshots GROUP BY 1)
SELECT day, n, b, pg_size_pretty(b) FROM s ORDER BY day;
SQL
{ echo "# day | rows | payload bytes | size   (demo_snapshots by capture day)"; psql_q "$Q"; } > "$W/db/demo-snapshots-age.txt" 2>&1 || true
rm -f "$Q"

# ---- census samples (the census already redacts them when it writes them) ------------------------------------
rm -rf "$W/census/samples"; mkdir -p "$W/census/samples"
if [[ -d "$SAMPLES" ]]; then
  for feed in "$SAMPLES"/*/; do
    f="$(basename "$feed")"
    for day in $(ls "$feed" 2>/dev/null | sort | tail -n 3); do
      [[ -d "$feed/$day" ]] || continue
      mkdir -p "$W/census/samples/$f/$day"
      for s in $(ls "$feed/$day" | awk '{t=$0; sub(/-[0-9]+\.xml$/, "", t); if (++c[t] <= 3) print}'); do
        cp "$feed/$day/$s" "$W/census/samples/$f/$day/${s}.txt"
      done
    done
  done
fi

# ---- redact, verify, publish ---------------------------------------------------------------------------------
SCAN=("${REPO_DIR}/scripts/with-dispatch-env.sh" python3 "${REPO_DIR}/scripts/leak-guard/transcript_leak_scan.py")
"${SCAN[@]}" redact --root "$W" --include-live --require-ladd --require-coords </dev/null >/dev/null
if ! "${SCAN[@]}" scan --root "$W" --require-ladd --require-coords </dev/null >/dev/null; then
  log "the leak scan still finds something (or could not check LADD / coordinates) after redaction: NOT publishing"
  exit 1
fi
date -u +%FT%TZ > "$W/UPDATED"
install -d -m 0750 "$DEST"
rsync -a --delete --chmod=D0750,F0640 "$W"/ "$DEST"/
# 2026-11-26: the copied files did not inherit the EA's read entry from the folder's default ACL (the census hand-off
# samples were unreadable to it), so it is set explicitly on every publish.
setfacl -R -m "u:${EA_MIRROR_READER:-ctdc-agent-anthropic-claude-ea}:rX" "$DEST"
log "published to ${DEST} ($(find "$DEST" -type f | wc -l) files, $(du -sh "$DEST" | cut -f1))"
