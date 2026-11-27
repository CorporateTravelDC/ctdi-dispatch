#!/usr/bin/env bash
# config/pihole/profiles/merged-blocklists/apply.sh -- 2026-11-27: install the blocklist merge and switch Pi-hole
# from the separate Blocklist Project adlists to the merged pair (docs/BLOCKLIST_MERGE.md). Root only. Root never
# runs code from the checkout: the engine is copied to /usr/local/libexec/ctdc-blmerge/ and every step runs that copy.
#
#   install            copy the engine, write /etc/ctdc-blmerge/merge.conf from the example if missing, create the
#                      state directories, install + enable the units (daily build, hourly checks), run one build
#   switch [--dry-run] after a build: switch OFF every enabled Blocklist Project adlist (their ids are recorded for
#                      revert), add the two merged lists as file:// adlists, then `pihole -g`. Refuses unless the
#                      HaGeZi lists the merge relies on are enabled adlists (HAGEZI_LISTS)
#   revert [--dry-run] switch the recorded Blocklist Project adlists back ON, remove the two file:// adlists, `pihole -g`
#   status             units, the last report, which adlists are on
#   remove             stop and remove the units and the installed copy (config, state, archive and lists are kept)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB=/usr/local/libexec/ctdc-blmerge
CONF=/etc/ctdc-blmerge/merge.conf
GRAVITY_DB="${PIHOLE_GRAVITY_DB:-/etc/pihole/gravity.db}"
UNITS=(ctdc-blmerge-build.service ctdc-blmerge-build.timer ctdc-blmerge-check.service ctdc-blmerge-check.timer)
BLP_PREFIX='https://blocklistproject.github.io/Lists/'
die() { echo "blocklist-merge: $*" >&2; exit 1; }
(( EUID == 0 )) || die "run as root (sudo)"

conf_get() { grep -m1 -E "^$1=" "$CONF" | cut -d= -f2-; }
sql() { pihole-FTL sqlite3 "$GRAVITY_DB"; }          # SQL on stdin (a heredoc); only constants and integer ids reach it

copy_lib() {
  install -d -m 0755 -o root -g root "$LIB"
  install -m 0755 -o root -g root "$HERE/blocklist_merge.py" "$LIB/blocklist_merge.py"
}

install_all() {
  copy_lib
  install -d -m 0755 -o root -g root /etc/ctdc-blmerge
  if [[ ! -f "$CONF" ]]; then
    install -m 0644 -o root -g root "$HERE/merge.conf.example" "$CONF"
    echo "blocklist-merge: wrote $CONF from the example"
  fi
  python3 "$LIB/blocklist_merge.py" status --conf "$CONF" >/dev/null || die "$CONF does not validate"
  local out check lists group
  out="$(conf_get OUT_DIR)"; check="$(conf_get CHECK_DIR)"; lists="$(conf_get LIST_DIR)"; group="$(conf_get READ_GROUP)"
  install -d -m 0750 -o root -g "${group:-root}" "$out"
  install -d -m 0755 -o unbound -g unbound "$check"
  install -d -m 0755 -o root -g root "$lists"
  command -v restorecon >/dev/null 2>&1 && restorecon -R "$lists" || true   # Pi-hole's label (pihole_var_lib_t) on SELinux hosts
  local u; for u in "${UNITS[@]}"; do install -m 0644 -o root -g root "$HERE/$u" "/etc/systemd/system/$u"; done
  systemctl daemon-reload
  systemctl enable ctdc-blmerge-build.timer ctdc-blmerge-check.timer >/dev/null 2>&1
  systemctl start ctdc-blmerge-build.timer ctdc-blmerge-check.timer
  echo "blocklist-merge: first build (fetches about 5M names; gravity is NOT touched until you run: apply.sh switch)"
  python3 "$LIB/blocklist_merge.py" build --conf "$CONF" || echo "blocklist-merge: the first build reported a problem (above)"
  status
}

ids_enabled_blp() {
  sql <<SQL
SELECT id FROM adlist WHERE address LIKE '${BLP_PREFIX}%' AND enabled = 1 AND type = 0;
SQL
}

switch() {
  local dry="${1:-}" lists out h missing=0 ids file
  lists="$(conf_get LIST_DIR)"; out="$(conf_get OUT_DIR)"
  for file in blp-merged-hosts.txt blp-merged-abp.txt; do
    [[ -s "$lists/$file" ]] || die "$lists/$file is missing: run apply.sh install (it builds) first"
  done
  for h in $(conf_get HAGEZI_LISTS); do
    local n
    n="$(sql <<SQL
SELECT count(*) FROM adlist WHERE enabled = 1 AND address LIKE '%hagezi/dns-blocklists%/${h}.txt';
SQL
)"
    if [[ "${n//[^0-9]/}" == 0 ]]; then echo "   !! HaGeZi '${h}' is not an enabled adlist"; missing=1; fi
  done
  (( missing == 0 )) || die "the merge leaves out what HaGeZi blocks, so those lists must be enabled first (adblock/<name>.txt); nothing changed"
  ids="$(ids_enabled_blp | tr -cd '0-9\n' | paste -sd, -)"
  echo "   Blocklist Project adlists to switch off: ${ids:-none}"
  echo "   merged adlists to add: file://$lists/blp-merged-hosts.txt, file://$lists/blp-merged-abp.txt"
  [[ "$dry" == --dry-run ]] && { echo "   (dry run: nothing changed)"; return 0; }
  [[ -n "$ids" ]] && tr ',' '\n' <<< "$ids" >> "$out/switched-adlists.txt" && sort -u -o "$out/switched-adlists.txt" "$out/switched-adlists.txt"
  sql <<SQL
UPDATE adlist SET enabled = 0 WHERE id IN (${ids:-0});
INSERT INTO adlist (address, enabled, comment, type)
  SELECT 'file://${lists}/blp-merged-hosts.txt', 1, 'Blocklist Project, merged (exact names) -- blocklist-merge', 0
  WHERE NOT EXISTS (SELECT 1 FROM adlist WHERE address = 'file://${lists}/blp-merged-hosts.txt');
INSERT INTO adlist (address, enabled, comment, type)
  SELECT 'file://${lists}/blp-merged-abp.txt', 1, 'Blocklist Project, merged (parent + subdomains) -- blocklist-merge', 0
  WHERE NOT EXISTS (SELECT 1 FROM adlist WHERE address = 'file://${lists}/blp-merged-abp.txt');
UPDATE adlist SET enabled = 1 WHERE address IN ('file://${lists}/blp-merged-hosts.txt', 'file://${lists}/blp-merged-abp.txt');
SQL
  nice -n 19 pihole -g
  local got
  got="$(sql <<SQL
SELECT coalesce(sum(number), 0) FROM adlist WHERE enabled = 1 AND address IN ('file://${lists}/blp-merged-hosts.txt', 'file://${lists}/blp-merged-abp.txt');
SQL
)"
  got="${got//[^0-9]/}"
  if [[ "${got:-0}" -lt 1000 ]]; then
    echo "   !! Pi-hole loaded only ${got:-0} entries from the merged lists: reverting so nothing goes unblocked"
    revert
    die "switch undone; check that Pi-hole can read file://$lists (owner, SELinux label) and send the EA the gravity output"
  fi
  echo "blocklist-merge: switched; Pi-hole loaded ${got} entries from the merged lists (revert: apply.sh revert)"
}

revert() {
  local dry="${1:-}" lists out ids
  lists="$(conf_get LIST_DIR)"; out="$(conf_get OUT_DIR)"
  ids="$(tr -cd '0-9\n' < "$out/switched-adlists.txt" 2>/dev/null | paste -sd, - || true)"
  echo "   Blocklist Project adlists to switch back on: ${ids:-none recorded}"
  echo "   merged adlists to remove: file://$lists/blp-merged-hosts.txt, file://$lists/blp-merged-abp.txt"
  [[ "$dry" == --dry-run ]] && { echo "   (dry run: nothing changed)"; return 0; }
  sql <<SQL
UPDATE adlist SET enabled = 1 WHERE id IN (${ids:-0});
DELETE FROM adlist WHERE address IN ('file://${lists}/blp-merged-hosts.txt', 'file://${lists}/blp-merged-abp.txt');
SQL
  nice -n 19 pihole -g
  echo "blocklist-merge: reverted"
}

status() {
  local u; for u in "${UNITS[@]}"; do printf '   %-30s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"; done
  [[ -x "$LIB/blocklist_merge.py" ]] && python3 "$LIB/blocklist_merge.py" status --conf "$CONF" | sed 's/^/   /' || true
  sql <<SQL | sed 's/^/   /'
SELECT enabled, substr(address, 1, 80) FROM adlist WHERE address LIKE '${BLP_PREFIX}%' OR address LIKE 'file://%' OR address LIKE '%hagezi%' ORDER BY enabled DESC, address;
SQL
}

remove_all() {
  systemctl disable --now ctdc-blmerge-build.timer ctdc-blmerge-check.timer 2>/dev/null || true
  local u; for u in "${UNITS[@]}"; do rm -f "/etc/systemd/system/$u"; done
  rm -rf "$LIB"
  systemctl daemon-reload
  echo "blocklist-merge: removed (config, state, archive and lists kept; run apply.sh revert first if Pi-hole uses the merged lists)"
}

case "${1:-}" in
  install) install_all ;;
  switch) switch "${2:-}" ;;
  revert) revert "${2:-}" ;;
  status) status ;;
  remove) remove_all ;;
  *) die "usage: apply.sh install | switch [--dry-run] | revert [--dry-run] | status | remove" ;;
esac
