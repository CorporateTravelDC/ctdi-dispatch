#!/usr/bin/env bash
# scripts/rotate-credential.sh -- rotate one platform credential end to end,
# never printing a value (2026-10-05; the four names agents could read
# 2026-10-04 ~15:20-19:30 before the subsets were narrowed).
#
#   scripts/rotate-credential.sh NAME             # plan only (default)
#   scripts/rotate-credential.sh NAME --execute   # do it (asks for sudo once)
#
#   BOARD_KEY               new random master key; restart web; verify new 200 / old 401
#   DISPATCH_ADMIN_TOKEN    mint a new admin auth_tokens row (same label), swap the
#                           env, restart web + acars-watcher, verify, revoke the old row
#   NTFY_TOKEN              ntfy token add for user `dispatch`, swap, restart the
#                           running consumers, test publish, remove the old token
#   NEXTCLOUD_APP_PASSWORD  occ user:auth-tokens:add (named dispatch-automation-DATE),
#                           swap, restart the running consumers, verify WebDAV, delete
#                           the old `dispatch-automation` app password
#
# Runs as the operator (owns dispatch-secrets.env). The file is rewritten in
# place (its directory is root-owned, so no temp file beside it). Scoped
# container env files are regenerated with sudo before any restart. Values
# travel only through environment variables and private fds -- never argv,
# never stdout. If verification fails the OLD credential is left valid and the
# script stops with the exact rollback step.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS=/etc/corporatetraveldc/dispatch-secrets.env
ENVF=/etc/corporatetraveldc/dispatch.env
NAME="${1:-}"; EXEC=0; [[ "${2:-}" == --execute ]] && EXEC=1
case "$NAME" in BOARD_KEY|DISPATCH_ADMIN_TOKEN|NTFY_TOKEN|NEXTCLOUD_APP_PASSWORD) ;; *) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 64 ;; esac
[[ "$(id -un)" == corporatetraveldc ]] || { echo "run as corporatetraveldc" >&2; exit 2; }
STAMP=$(date +%Y%m%d)
API=http://127.0.0.1:8000

say() { printf '[rotate %s] %s\n' "$NAME" "$*"; }
getv() { grep -m1 "^$1=" "$SECRETS" | cut -d= -f2-; }
cfg() { grep -m1 "^$1=" "$ENVF" 2>/dev/null | cut -d= -f2- || true; }
# swap NAME's line in place: new value from $NEWV (environment), file keeps owner/mode
swap_value() {
  NEWV="$1" python3 - "$SECRETS" "$NAME" <<'PYEOF'
import os, sys
path, name = sys.argv[1:3]
lines = open(path).read().splitlines(keepends=True)
hit = 0
for i, l in enumerate(lines):
    if l.startswith(name + "="):
        lines[i] = f"{name}={os.environ['NEWV']}\n"; hit += 1
if hit != 1:
    sys.exit(f"expected exactly one {name}= line, found {hit}")
with open(path, "r+") as fh:
    fh.seek(0); fh.write("".join(lines)); fh.truncate()
PYEOF
}
# units to restart: running containers whose scoped env carries NAME
consumers() {
  local svc f u
  for al in "$REPO"/scripts/service-env/*.allowlist; do
    grep -qE "^${NAME}([[:space:]]|$)" "$al" || continue
    svc=$(basename "$al" .allowlist)
    for f in "$HOME"/.config/containers/systemd/*.container; do
      grep -q "^EnvironmentFile=/etc/corporatetraveldc/svc/${svc}.env" "$f" || continue
      u=$(basename "$f" .container)
      systemctl --user is-active --quiet "$u" && echo "$u"
    done
  done | sort -u
}
restart_consumers() {
  local u
  for u in $(consumers); do say "restart $u"; systemctl --user restart "$u"; sleep 5; done
}
regen_env() { say "regenerating scoped container env (sudo)"; sudo python3 "$REPO/scripts/service-env/generate.py" --write >/dev/null; }
pg() { local pw; pw="$(getv DISPATCH_PG_PASSWORD)"; PGPASSWORD="$pw" podman exec -i -e PGPASSWORD -e OLDH -e NEWH -e NEWP -e STAMP corporatetraveldc-pgsql sh -c 'psql -U dispatch -d corporatetraveldc -v ON_ERROR_STOP=1 -tA -v oldh="$OLDH" -v newh="$NEWH" -v newp="$NEWP" -v stamp="$STAMP"'; }
code_with() {  # header-name value path -> HTTP code
  curl -s -o /dev/null -w '%{http_code}' -H @<(printf '%s: %s\n' "$1" "$2") "${API}$3"
}

say "consumers that will be restarted: $(consumers | tr '\n' ' ')"
if (( ! EXEC )); then say "plan only -- re-run with --execute"; exit 0; fi
sudo -v

OLD="$(getv "$NAME")"; [[ -n "$OLD" ]] || { say "no current value in ${SECRETS}"; exit 3; }

case "$NAME" in
BOARD_KEY)
  NEW=$(python3 - <<'PYEOF'
import secrets; print(secrets.token_urlsafe(36))
PYEOF
)
  swap_value "$NEW"; regen_env; restart_consumers; sleep 10
  n=$(code_with X-Board-Key "$NEW" "/api/v1/board?thread=research"); o=$(code_with X-Board-Key "$OLD" "/api/v1/board?thread=research")
  say "verify: new key -> ${n} (want 200), old key -> ${o} (want 401/403)"
  [[ "$n" == 200 && ( "$o" == 401 || "$o" == 403 ) ]] || { say "VERIFY FAILED -- rollback: restore the old value in ${SECRETS} (it is still in this shell's memory only), regen + restart web"; exit 4; }
  ;;
DISPATCH_ADMIN_TOKEN)
  NEW=$(python3 - <<'PYEOF'
import secrets; print("ctdc_" + secrets.token_urlsafe(39))
PYEOF
)
  export OLDH NEWH NEWP STAMP   # STAMP was not exported on the first run: label came out "approval-gate-"
  OLDH=$(printf '%s' "$OLD" | sha256sum | cut -d' ' -f1); NEWH=$(printf '%s' "$NEW" | sha256sum | cut -d' ' -f1); NEWP="${NEW:0:25}"
  pg <<'SQL' >/dev/null
INSERT INTO auth_tokens (token_hash, token_prefix, user_label, tier, device_label, expires_at, department)
SELECT :'newh', :'newp', user_label, tier, 'approval-gate-' || :'stamp', NULL, department
  FROM auth_tokens WHERE token_hash = :'oldh' AND revoked_at IS NULL;
SQL
  n=$(code_with Authorization "Bearer ${NEW}" "/admin/approval-requests?command_pattern=rotation-check")
  [[ "$n" == 200 ]] || { say "new admin token does not authenticate (HTTP ${n}) -- nothing swapped; check the auth_tokens insert"; exit 4; }
  swap_value "$NEW"; regen_env; restart_consumers
  pg <<'SQL' >/dev/null
UPDATE auth_tokens SET revoked_at = EXTRACT(epoch FROM now()) WHERE token_hash = :'oldh' AND revoked_at IS NULL;
SQL
  o=$(code_with Authorization "Bearer ${OLD}" "/admin/approval-requests?command_pattern=rotation-check")
  say "verify: new token -> ${n} (want 200), old token -> ${o} (want 401/403)"
  # an unknown bearer resolves to a non-admin tier, so require_admin answers 403
  [[ "$o" == 401 || "$o" == 403 ]] || { say "old token still authenticates -- check auth_tokens"; exit 4; }
  ;;
NTFY_TOKEN)
  NEW=$(podman exec ntfy ntfy token add --label "dispatch-rotated-${STAMP}" dispatch 2>&1 | grep -oE 'tk_[A-Za-z0-9]+' | head -1)
  [[ "$NEW" == tk_* ]] || { say "ntfy token add did not return a token"; exit 4; }
  c=$(curl -s -o /dev/null -w '%{http_code}' -H @<(printf 'Authorization: Bearer %s\n' "$NEW") -H 'Priority: 1' -d "ntfy token rotation check ${STAMP}" http://127.0.0.1:2586/rotation-check)
  [[ "$c" == 200 ]] || { say "new ntfy token cannot publish (HTTP ${c}) -- nothing swapped"; exit 4; }
  swap_value "$NEW"; regen_env; restart_consumers
  OLDT="$OLD" podman exec -e OLDT ntfy sh -c 'ntfy token remove dispatch "$OLDT"' >/dev/null
  o=$(curl -s -o /dev/null -w '%{http_code}' -H @<(printf 'Authorization: Bearer %s\n' "$OLD") -d x http://127.0.0.1:2586/rotation-check)
  say "verify: new token published 200; old token -> ${o} (want 401/403)"
  ;;
NEXTCLOUD_APP_PASSWORD)
  NCU="$(cfg NEXTCLOUD_ADMIN_USER)"; [[ -n "$NCU" ]] || { say "NEXTCLOUD_ADMIN_USER missing from dispatch.env"; exit 3; }
  OLDID=$(podman exec -u www-data nextcloud-app php occ user:auth-tokens:list "$NCU" 2>/dev/null | awk -F'|' '$3 ~ /^ *dispatch-automation *$/ {gsub(/ /,"",$2); print $2}')
  [[ "$OLDID" =~ ^[0-9]+$ ]] || { say "could not find exactly one old 'dispatch-automation' app password"; exit 3; }
  NEW=$(podman exec -u www-data nextcloud-app php occ user:auth-tokens:add -n --name "dispatch-automation-${STAMP}" "$NCU" 2>/dev/null | grep -oE '[A-Za-z0-9]{72}' | tail -1)
  [[ ${#NEW} -eq 72 ]] || { say "occ did not return a 72-char app password"; exit 4; }
  c=$(NCU="$NCU" NEWP="$NEW" PYTHONPATH="$REPO/src" python3 - <<'PYEOF'
import os
os.environ["NEXTCLOUD_APP_PASSWORD"] = os.environ["NEWP"]
os.environ["NEXTCLOUD_ADMIN_USER"] = os.environ["NCU"]
from second_brain import webdav_client
try:
    print("ok" if webdav_client.list_files(webdav_client.BUSINESS_ROOT) is not None else "fail")
except Exception as e:
    print("fail", type(e).__name__)
PYEOF
)
  [[ "$c" == ok ]] || { say "new app password failed a WebDAV listing (${c}) -- nothing swapped; delete the new 'dispatch-automation-${STAMP}' entry in Nextcloud"; exit 4; }
  swap_value "$NEW"; regen_env; restart_consumers
  podman exec -u www-data nextcloud-app php occ user:auth-tokens:delete "$NCU" "$OLDID" >/dev/null
  say "verify: new app password lists the vault; old 'dispatch-automation' (id ${OLDID}) deleted"
  ;;
esac
unset OLD NEW
say "done. Host scripts read ${SECRETS} at run time; timer-driven containers pick up the new value at their next start."
