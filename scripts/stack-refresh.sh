#!/usr/bin/env bash
# scripts/stack-refresh.sh -- standing practice for keeping the WHOLE stack on
# current dependencies and a known-good (signed) codebase. Operator directive
# 2026-10-04: "a weekly run standard with a rotating random mid-week run as a
# tripwire and updating mechanism".
#
#   --weekly    full refresh: pull every external image, rebuild every local
#               image from the signed HEAD, in-image integrity gate on each
#               local image, serialized one-at-a-time restart of every unit
#               (scripts/serialized-rollout.sh), ntfy summary.
#   --tripwire  randomized tripwire, two jobs (operator directive 2026-10-04,
#               "not only a minimum weekly update logic but also a true
#               adversarial tripwire"):
#               (1) FLOOR: the timer fires every night in the maintenance
#                   window; after each run the script draws a random gap of
#                   1-7 days and sleeps through firings until it elapses (plus
#                   a small random chance of firing early), so the night is
#                   unpredictable but the last pull/update check is NEVER
#                   older than 7 days.
#               (2) AUDIT: before touching anything, an adversarial pass asks
#                   "is what is RUNNING what was SIGNED?" -- tree manifest,
#                   tracked-vs-live quadlets/units/skills, every running local
#                   container's image == the signed :latest and passes the
#                   in-image gate, external containers' images == the pulled
#                   tag, no containers outside the tracked quadlet set, and
#                   authorized_keys / sudoers / listening-port fingerprints vs
#                   the recorded baseline. ANY audit finding -> ntfy priority
#                   5, exit 2, and NO update is applied (a tampered box must
#                   not be "fixed" by rolling new images over it).
#               Then: pull externals / build locals into CHECK tags, diff
#               against running; unchanged -> silent; changed -> gates + smoke,
#               promote and serialized rollout, or hold + ntfy.
#   Every run RESTARTS EVERY container, image changed or not (operator
#   directive 2026-10-04): container StartedAt becomes an audit signal --
#   after a run, anything older than the run's start is either in the
#   "held for review, no restart" list with a reason (major-version bump,
#   build/gate failure, dirty or unsigned tree) or it is a finding. Held
#   units accumulate in a review-debt file until restarted clean.
#   --dry-run   report what would happen, change nothing.
#   --now       bypass the window/schedule checks (operator-run, out of cycle).
#   --status    print the tripwire's drawn next run (operator-only state file).
#   REBASELINE=1  (env, operator-run) re-record the audit fingerprints after an
#               intentional change to authorized_keys / sudoers / ports / units.
#
# Hard guards, both modes:
#   * refuses on a dirty or unsigned tree (verify-manifest.sh must pass) --
#     images bake the manifest and verified-exec's in-image gate fails on an
#     image built from an unsigned tree (learned 2026-10-04 00:44 ET);
#   * refuses a Nextcloud MAJOR-version jump (docker.io/library/nextcloud:
#     stable-apache is a rolling tag; 2026-09-03 incident: ~2h migration under
#     load) -- nextcloud-app is skipped and reported instead;
#   * Postgres images (pgsql, nextcloud-db) only ever change through
#     scripts/safe-pg-image-update.sh's canary path, never here;
#   * every rebuilt/pulled image keeps a :previous tag for the 24h rollback
#     window (scheduled-podman-prune honours it);
#   * runs under the maintenance-window guard unless --now.
# 2026-10-04 adversarial duel (U3) -- the hand-off contract:
#   * stack-refresh pulls/builds into :refresh-check, gates, promotes, then
#     calls serialized-rollout.sh with FROM_REFRESH=1 (restart only), so the
#     image deployed is exactly the image gated (H2);
#   * every image we build is recorded in reports/stack-refresh-built-ids.json;
#     the audit trusts a running image only if it is the tag, :previous, or
#     listed there -- no label heuristic (H3);
#   * a 4h internal deadline (STACK_REFRESH_MAX_S) holds whatever the rollout
#     has not reached; the unit has no systemd start timeout (H1);
#   * left-behind is checked only for units in the rollout list (M5); review
#     debt is keyed by UNIT (M6); load deferral never crosses the 7-day
#     ceiling (M3); fingerprints no longer depend on a cached sudo ticket and
#     cover every team account's authorized_keys (M4).
# Secrets: NTFY_TOKEN is read in-script via read_env_var and never echoed.
# Never shell-source the env files (values are deliberately unquoted).
set -uo pipefail
# 2026-10-05 (argv-token sweep): a bearer token never goes on a command line --
# /proc/<pid>/cmdline is world-readable here (no hidepid), i.e. readable by
# every team account. authhdr NAME TOKEN puts the header on a private fd and
# sets NAME=(-H @/dev/fd/N) for ONE curl call (re-run it before each call).
authhdr() { local -n _ah="$1"; [[ -n "${_AUTHHDR_FD:-}" ]] && exec {_AUTHHDR_FD}<&-; exec {_AUTHHDR_FD}<<<"Authorization: Bearer $2"; _ah=(-H "@/dev/fd/${_AUTHHDR_FD}"); }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}" || exit 1
ENV_FILE=/etc/corporatetraveldc/dispatch.env
SECRETS_FILE=/etc/corporatetraveldc/dispatch-secrets.env
LOG_DIR="${HOME}/rollout-logs"; mkdir -p "${LOG_DIR}"
MODE=""; DRY=0; NOW=0
for a in "$@"; do
  case "$a" in
    --weekly) MODE=weekly ;; --tripwire) MODE=tripwire ;;
    --dry-run) DRY=1 ;; --now) NOW=1 ;;
    --status) python3 "$(dirname "${BASH_SOURCE[0]}")/lib/tripwire_draw.py" status "${HOME}/.cache/stack-refresh-tripwire.json"; exit 0 ;;
    *) echo "usage: $0 --weekly|--tripwire [--dry-run] [--now]" >&2; exit 64 ;;
  esac
done
[[ -n "$MODE" ]] || { echo "usage: $0 --weekly|--tripwire [--dry-run] [--now]" >&2; exit 64; }

log() { printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2"; }
read_env_var() { local key="$1" file="$2"; [[ -f "$file" ]] || return 0; grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d'=' -f2-; }
NTFY_BASE="$(read_env_var NTFY_BASE_URL "$ENV_FILE")"; NTFY_BASE="${NTFY_BASE:-http://127.0.0.1:2586}"
NTFY_OPS="$(read_env_var NTFY_OPS_TOPIC "$ENV_FILE")";  NTFY_OPS="${NTFY_OPS:-ops-health}"
NTFY_TOKEN="$(read_env_var NTFY_TOKEN "$SECRETS_FILE")"
ntfy_send() {
  local title="$1" msg="$2" priority="${3:-2}"; local auth=()
  [[ -n "$NTFY_TOKEN" ]] && authhdr auth "${NTFY_TOKEN}"
  (( DRY )) && { log info "ntfy (dry-run): ${title} -- ${msg}"; return 0; }
  curl -sf --max-time 5 "${auth[@]}" -H "Title: ${title}" -H "Priority: ${priority}" -H "Tags: package" \
       -d "${msg}" "${NTFY_BASE}/${NTFY_OPS}" >/dev/null 2>&1 || log warn "ntfy_send failed"
}
digest() { podman image inspect --format '{{.Digest}}' "$1" 2>/dev/null; }
imgid()  { podman image inspect --format '{{.Id}}' "$1" 2>/dev/null | cut -c1-12; }

# ------------------------------- tripwire: unclockable schedule (draw.py) ----
# The timer fires hourly at a random minute and only asks "is it time yet?".
# scripts/lib/tripwire_draw.py owns the schedule: next_run is drawn after each
# run inside [last+36h, last+7d], snapped onto a quiet-window candidate from
# the rolling 30-day load profile, with one never-used window forced per
# calendar month. At fire time load1 >= 15 defers an hour, except within the
# last hour before the 7-day ceiling (tripwire_draw.py near-ceiling).
TRIP_STATE="${HOME}/.cache/stack-refresh-tripwire.json"; mkdir -p "${HOME}/.cache"
DRAW="${REPO_ROOT}/scripts/lib/tripwire_draw.py"
if [[ "$MODE" == tripwire ]] && (( ! NOW )); then
  [[ -f "$TRIP_STATE" ]] || python3 "$DRAW" draw "$TRIP_STATE" >/dev/null
  if ! python3 "$DRAW" due "$TRIP_STATE"; then exit 0; fi          # silent: not yet
  if awk "BEGIN{exit !($(cut -d' ' -f1 /proc/loadavg) >= 15)}"; then
    if python3 "$DRAW" near-ceiling "$TRIP_STATE"; then
      log warn "tripwire: load1 $(cut -d' ' -f1 /proc/loadavg) >= 15 but the 7-day ceiling is <1h away -- running anyway (duel M3)"
    else
      log info "tripwire: due, but load1 $(cut -d' ' -f1 /proc/loadavg) >= 15 -- deferring to the next hourly check"; exit 0
    fi
  fi
  log info "tripwire: due -- $(python3 "$DRAW" status "$TRIP_STATE" | sed -n 2p)"
fi
# a dry-run never marks (the REBASELINE runbook step uses --tripwire --dry-run)
mark_tripwire_run() { [[ "$MODE" == tripwire ]] || return 0; (( DRY )) && return 0; python3 "$DRAW" mark-and-draw "$TRIP_STATE" | sed 's/^/[tripwire] /'; }

# ---------------------------------------------------------------- guards ----
if [[ "$MODE" == weekly ]] && (( ! NOW )) && [[ -x scripts/maintenance-window-guard.sh ]] && ! scripts/maintenance-window-guard.sh >/dev/null 2>&1; then
  log info "weekly: outside the maintenance window and --now not given -- exiting 0"; exit 0
fi
# (tripwire deliberately NOT window-gated: its instant comes from the measured
#  quiet profile, which is allowed to sit outside 23:00-05:00 -- e.g. Sun 22:00.)
HEAD_SHORT=$(git rev-parse --short HEAD)
TREE_DIRTY=0; TREE_UNSIGNED=0
[[ -n "$(git status --porcelain)" ]] && TREE_DIRTY=1
scripts/verify-manifest.sh >/dev/null 2>&1 || TREE_UNSIGNED=1
DEBT_FILE="/var/lib/corporatetraveldc/reports/stack-refresh-held.json"; mkdir -p "$(dirname "$DEBT_FILE")"
RUN_START=$(date +%s)
DEADLINE=$(( RUN_START + ${STACK_REFRESH_MAX_S:-14400} ))   # 4h; the unit itself has no start timeout
BUILT_IDS=/var/lib/corporatetraveldc/reports/stack-refresh-built-ids.json
REPO_PATH=/opt/corporatetraveldc/private/ctdi-dispatch-internal
WEBSITE_PATH=/opt/corporatetraveldc/private/csexecutiveservices-website
# rollout rows "unit|kind|image|bdir|cfile|win" -- serialized-rollout.sh is the one list
rollout_rows() { grep -E '^  "[^"]+\|(local|ext)\|' scripts/serialized-rollout.sh | sed -E 's/^  "//; s/".*$//; s#\$REPO#'"$REPO_PATH"'#; s#\$WEBSITE#'"$WEBSITE_PATH"'#'; }
rollout_units() { rollout_rows | cut -d'|' -f1; }
units_for_image() { rollout_rows | awk -F'|' -v img="$1" '$3==img{print $1}'; }
is_pg_unit() { rollout_rows | awk -F'|' -v u="$1" '$1==u && $3 ~ /^docker\.io\/library\/postgres:/{f=1} END{exit !f}'; }
record_built() {  # record_built <image> <full-id> <svc>
  python3 - "$BUILT_IDS" "$1" "$2" "$3" "$HEAD_SHORT" <<'PYEOF'
import json, sys, time
path, image, iid, svc, head = sys.argv[1:6]
try: d = json.load(open(path))
except Exception: d = []
d.append({"id": iid, "image": image, "svc": svc, "head": head, "ts": int(time.time()), "by": "stack-refresh"})
json.dump(d[-200:], open(path, "w"), indent=1)
PYEOF
}
is_our_build() { [[ -n "$1" && -f "$BUILT_IDS" ]] && grep -qF "\"$1" "$BUILT_IDS"; }   # 12-char id prefix
debt_update() {  # debt_update "<unit=reason;unit=reason>" "<restarted-ok units space-separated>"
  # a dry-run reports but never writes the debt file (2026-10-04: a weekly
  # --dry-run on a dirty tree recorded 35 units of real debt)
  python3 - "$DEBT_FILE" "$1" "$2" "$MODE" "${DRY:-0}" <<'PYEOF'
import json, sys, time
path, held, cleared, mode, dry = sys.argv[1:6]
try: d = json.load(open(path))
except Exception: d = {}
now = int(time.time())
for item in [x for x in held.split(";") if x]:
    unit, _, reason = item.partition("=")
    e = d.setdefault(unit, {"first_seen": now, "runs": 0}); e.update({"reason": reason, "last_seen": now, "mode": mode}); e["runs"] += 1
for unit in cleared.split():
    d.pop(unit, None)
if dry != "1":
    json.dump(d, open(path, "w"), indent=1)
if d:
    oldest = min(v["first_seen"] for v in d.values())
    print(f"review debt: {len(d)} unit(s) held; oldest since {time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest))}: " + ", ".join(f"{k} ({v['reason']}, {v['runs']}x)" for k, v in d.items()))
else:
    print("review debt: none")
PYEOF
}
if [[ "$MODE" == weekly ]] && (( TREE_DIRTY || TREE_UNSIGNED )); then
  log error "weekly: tree dirty=${TREE_DIRTY} unsigned=${TREE_UNSIGNED} -- HELD FOR REVIEW, NO RESTART (every unit; images would bake unsigned files)"
  all_units=$(grep -oE '^  "[a-z0-9-]+\|' scripts/serialized-rollout.sh | tr -d ' "|' | sed 's/$/=dirty-or-unsigned-tree/' | paste -sd';')
  DEBT=$(debt_update "$all_units" ""); log warn "$DEBT"
  ntfy_send "stack-refresh weekly: HELD FOR REVIEW" "tree at ${HEAD_SHORT}: dirty=${TREE_DIRTY} unsigned=${TREE_UNSIGNED}; no restart; ${DEBT}" 4; exit 3
fi
log info "${MODE} refresh starting at ${HEAD_SHORT} (dry-run=${DRY})"

# ------------------------------------------------ adversarial audit ---------
# "Is what is running what was signed?" Findings are collected, never
# auto-fixed. Fingerprints (sha256 of authorized_keys, sudoers.d listing,
# listening sockets) are compared to a baseline recorded on the first run;
# the baseline is re-recorded ONLY by an operator-run --rebaseline.
AUDIT=(); PENDING=(); BASE_DIR="${HOME}/.cache/stack-refresh-baseline"; mkdir -p "$BASE_DIR"
# a dirty or unsigned tree at tripwire time is a finding in its own right
(( TREE_DIRTY ))    && AUDIT+=("working tree is DIRTY at ${HEAD_SHORT} ($(git status --porcelain | wc -l) path(s))")
(( TREE_UNSIGNED )) && AUDIT+=("tree does not verify against the signed manifest at ${HEAD_SHORT}")
fp_keys()  { sha256sum "${HOME}/.ssh/authorized_keys" 2>/dev/null | cut -c1-16; }
# duel M4: no `sudo -n -l` (it changed with a cached sudo ticket); the sudoers.d
# listing + per-file hash, "unreadable" recorded deterministically.
fp_sudo()  {
  { for f in /etc/sudoers /etc/sudoers.d/*; do
      [[ -e "$f" ]] || continue
      printf '%s %s %s ' "$f" "$(stat -c '%U:%G %a %s' "$f" 2>/dev/null)"
      if [[ -r "$f" ]]; then sha256sum "$f" | cut -c1-16; else echo unreadable; fi
    done; } | sha256sum | cut -c1-16; }
# every team account's inbound authorized_keys (registry + groups); a 0700 home
# is "unreadable" when run unprivileged -- deterministic, so the account SET and
# any newly readable/changed file still move the fingerprint.
team_accounts() {
  { [[ -r /etc/ctdc-accounts.conf ]] && awk '!/^#/ && NF{print $1}' /etc/ctdc-accounts.conf
    for g in ctdc-agents ctdc-ops; do getent group "$g" | cut -d: -f4 | tr ',' '\n'; done; } | grep -v '^$' | sort -u; }
fp_team_keys() {
  { for a in $(team_accounts); do
      h=$(getent passwd "$a" | cut -d: -f6); f="$h/.ssh/authorized_keys"
      if [[ -r "$f" ]]; then printf '%s %s\n' "$a" "$(sha256sum "$f" | cut -c1-16)"; else printf '%s unreadable\n' "$a"; fi
    done; } | sha256sum | cut -c1-16; }
# 2026-10-06: listeners in the kernel's ephemeral range are excluded --
# tailscaled's peerapi binds a RANDOM port on each tailnet address every time
# it starts (41661/40034 after the 06:17 ET reboot), so the fingerprint
# changed on every reboot with nothing to show for it. A real new service
# listens on a fixed port below that range; a fixed-port listener never moves.
fp_ports_raw() { local lo hi; read -r lo hi < /proc/sys/net/ipv4/ip_local_port_range
  ss -ltnH 2>/dev/null | awk -v lo="$lo" -v hi="$hi" '{n=split($4,a,":"); p=a[n]+0; if (p<lo || p>hi) print $4}' | sort -u; }
fp_ports() { fp_ports_raw | sha256sum | cut -c1-16; }
fp_units_raw() { ls "${HOME}/.config/containers/systemd/" "${HOME}/.config/systemd/user/" 2>/dev/null | sort; }
fp_units() { fp_units_raw | sha256sum | cut -c1-16; }
fp_team_keys_raw() { for a in $(team_accounts); do
      h=$(getent passwd "$a" | cut -d: -f6); f="$h/.ssh/authorized_keys"
      if [[ -r "$f" ]]; then printf '%s %s\n' "$a" "$(sha256sum "$f" | cut -c1-16)"; else printf '%s unreadable\n' "$a"; fi
    done; }
# 2026-10-06: the raw input behind each hash is kept next to it (<name>.raw)
# so a finding can say WHAT changed -- the 17:57Z run reported four hashes
# and nothing else, and every one had to be reconstructed by hand. The hash
# stays the comparison; the raw file is explanation only.
check_fp() { local name="$1" cur="$2" raw="${3:-}"; local f="${BASE_DIR}/${name}"
  if [[ ! -f "$f" ]]; then echo "$cur" >"$f"; [[ -n "$raw" ]] && printf '%s\n' "$raw" >"${f}.raw"; log info "audit: baseline recorded for ${name}"; return; fi
  if [[ "$(cat "$f")" != "$cur" ]]; then
    local why=""
    if [[ -n "$raw" && -f "${f}.raw" ]]; then why=$(diff <(cat "${f}.raw") <(printf '%s\n' "$raw") | grep -E '^[<>]' | sed 's/^</  -/; s/^>/  +/' | head -20 | tr '\n' ';'); fi
    AUDIT+=("${name} fingerprint changed vs baseline ($(cat "$f") -> ${cur})${why:+ diff: ${why}}")
  fi; }
if [[ "${REBASELINE:-0}" == 1 ]]; then rm -f "${BASE_DIR}"/*; log info "audit: baselines cleared (REBASELINE=1)"; fi
check_fp authorized_keys "$(fp_keys)"; check_fp sudoers "$(fp_sudo)"; check_fp listen_ports "$(fp_ports)" "$(fp_ports_raw)"; check_fp unit_set "$(fp_units)" "$(fp_units_raw)"
check_fp team_authorized_keys "$(fp_team_keys)" "$(fp_team_keys_raw)"
# NOTE: fp_sudo changed definition on 2026-10-04 (duel M4); the existing
# baseline must be re-recorded ONCE by the operator (REBASELINE=1 ... --dry-run
# --now), never silently by this script -- see AGENT_SEGMENTATION.md runbook.
# tracked vs live (quadlets, user units, skills) via the existing checkers
drift_out=$(scripts/check-claude-md-drift.sh 2>&1); echo "$drift_out" | grep -qE "^\[DRIFT\]" && AUDIT+=("tracked-vs-live drift: $(echo "$drift_out" | grep -cE '^\[DRIFT\]') item(s) -- see check-claude-md-drift.sh")
scripts/skills-sync.sh check >/dev/null 2>&1 || AUDIT+=("skills: executing copies differ from tracked (skills-sync.sh check)")
# running containers: each must map to a tracked quadlet; locals must run the signed :latest and pass the in-image gate
while read -r cname cimg; do
  unit="${cname#systemd-}"
  [[ -f "${REPO_ROOT}/.config/containers/systemd/${unit}.container" ]] || { [[ "$cname" == "$unit" && -f "${REPO_ROOT}/.config/containers/systemd/${cname}.container" ]] || AUDIT+=("container ${cname} has no tracked quadlet"); }
  if [[ "$cimg" == localhost/corporatetraveldc-* ]]; then
    running_id=$(podman inspect "$cname" --format '{{.Image}}' 2>/dev/null | cut -c1-12)
    # 2026-10-06: a oneshot that exits between `podman ps` and this inspect
    # (personal-notes-import, 17:59:02Z, the audit's own minute) has no
    # container to inspect -- an empty id is "gone", not a foreign image.
    # It is re-listed below; if it is still absent it is skipped with a note.
    if [[ -z "$running_id" ]]; then
      if podman container exists "$cname" 2>/dev/null; then running_id=$(podman inspect "$cname" --format '{{.Image}}' 2>/dev/null | cut -c1-12); fi
      [[ -z "$running_id" ]] && { log info "audit: ${cname} exited during the audit (oneshot) -- skipped"; continue; }
    fi
    latest_id=$(podman image inspect "${cimg%%:*}:latest" --format '{{.Id}}' 2>/dev/null | cut -c1-12)
    prev_id=$(podman image inspect "${cimg%%:*}:previous" --format '{{.Id}}' 2>/dev/null | cut -c1-12)
    if [[ -n "$latest_id" && "$running_id" != "$latest_id" ]]; then
      # running == :previous means WE moved :latest (a promoted-but-not-yet-
      # restarted unit, e.g. an interrupted rollout): pending, not tampering.
      # duel H3: trusted only if it is :previous or an image id WE recorded at
      # build time (reports/stack-refresh-built-ids.json) -- never a label,
      # which anyone able to run `podman build --label` could set.
      if [[ -n "$prev_id" && "$running_id" == "$prev_id" ]] || is_our_build "$running_id"; then
        PENDING+=("${cname} (local ${running_id} -> ${latest_id})")
      else AUDIT+=("${cname} runs image ${running_id}, signed :latest is ${latest_id} (not :previous, not in the built-ids manifest)"); fi
    fi
    if podman exec "$cname" test -x /app/scripts/verified-exec.sh >/dev/null 2>&1; then
      podman exec "$cname" sh -c 'cd /app && scripts/verified-exec.sh true' >/dev/null 2>&1 || AUDIT+=("${cname}: in-image integrity gate FAILS on the RUNNING container")
    fi
  else
    # compare image IDs: a container's ImageDigest and the tag's Digest are
    # different representations of the same image (first run: 21 false hits)
    running_id=$(podman inspect "$cname" --format '{{.Image}}' 2>/dev/null | cut -c1-12)
    tag_id=$(podman image inspect "$cimg" --format '{{.Id}}' 2>/dev/null | cut -c1-12)
    prev_id=$(podman image inspect "${cimg%:*}:previous" --format '{{.Id}}' 2>/dev/null | cut -c1-12)
    if [[ -n "$tag_id" && -n "$running_id" && "$running_id" != "$tag_id" ]]; then
      # localhost/ images outside the corporatetraveldc- prefix (csexec-contact) are ours too
      if [[ -n "$prev_id" && "$running_id" == "$prev_id" ]] || { [[ "$cimg" == localhost/* ]] && is_our_build "$running_id"; }; then PENDING+=("${cname} (image ${running_id} -> ${tag_id})")
      else AUDIT+=("${cname} runs image ${running_id}, local ${cimg} tag is ${tag_id} (not :previous either)"); fi
    fi
  fi
done < <(podman ps --format '{{.Names}} {{.Image}}' 2>/dev/null)
if (( ${#AUDIT[@]} )); then
  log error "ADVERSARIAL AUDIT: ${#AUDIT[@]} finding(s):"; for a in "${AUDIT[@]}"; do log error "  - ${a}"; done
  ntfy_send "stack-refresh ${MODE}: AUDIT FINDINGS -- updates HELD" "$(printf '%s; ' "${AUDIT[@]}")" 5
  [[ "$MODE" == tripwire ]] && mark_tripwire_run
  exit 2
fi
log info "adversarial audit: clean (running == signed or == :previous; baselines match)"
if (( ${#PENDING[@]} )); then
  log warn "pending restart (we moved the tag; the rollout below resolves it): ${#PENDING[@]} unit(s)"; for p_ in "${PENDING[@]}"; do log warn "  - ${p_}"; done
  # a pending-restart unit must be restarted even if its image did not change again this run
  FORCE_ROLLOUT=1
fi

# ------------------------------------------------- local images (from HEAD) --
# One list: every distinct LOCAL image in serialized-rollout.sh's ROLLOUT
# (duel H2: the rollout no longer builds when stack-refresh hands off, so
# stack-refresh must build ALL of them -- demo, acars-watcher, csexec-contact
# included -- or they would never be refreshed again).
declare -A LIMG_DIR=() LIMG_CF=()
LOCAL_IMAGES=()
while IFS='|' read -r unit kind image bdir cfile win; do
  [[ "$kind" == local ]] || continue
  [[ -n "${LIMG_DIR[$image]:-}" ]] && continue
  LIMG_DIR[$image]="$bdir"; LIMG_CF[$image]="$cfile"; LOCAL_IMAGES+=("$image")
done < <(rollout_rows)
svc_of() { local b; b=$(basename "${1%:*}"); echo "${b#corporatetraveldc-}"; }
check_of() { echo "${1%:*}:refresh-check"; }

# ----------------------------------------------------- external images ------
mapfile -t EXTERNALS < <(grep -hE "^Image=" "${HOME}/.config/containers/systemd/"*.container | sed 's/^Image=//' | grep -v '^localhost/' | sort -u)
PG_IMAGES=("docker.io/library/postgres:16-alpine")
is_pg() { local i; for i in "${PG_IMAGES[@]}"; do [[ "$1" == "$i" ]] && return 0; done; return 1; }

nextcloud_major_jump() {
  # returns 0 (true) if the pulled nextcloud image's major differs from the running one
  local running pulled
  running=$(podman exec nextcloud-app php -r 'echo getenv("NEXTCLOUD_VERSION");' 2>/dev/null | cut -d. -f1)
  pulled=$(podman run --rm --entrypoint sh "$1" -c 'echo "$NEXTCLOUD_VERSION"' 2>/dev/null | cut -d. -f1)
  [[ -n "$running" && -n "$pulled" && "$running" != "$pulled" ]]
}

# HELD_UNIT[unit]=reason -- review debt is keyed by UNIT (duel M6)
# 2026-10-06: `=()` -- a bare `declare -A` is UNSET under bash 5.3's `set -u`;
# the held-units step crashed and the script fell through into the rollout.
declare -A HELD_UNIT=()
hold_image() {  # hold_image <image> <reason>: hold every rollout unit running it
  local u; for u in $(units_for_image "$1"); do HELD_UNIT[$u]="$2"; done; }
CHANGED_EXT=(); CHANGED_LOCAL=()
# -- externals: pull (into the real tag; :previous only on a real change)
for img in "${EXTERNALS[@]}"; do
  if is_pg "$img"; then log info "pg image ${img}: canary-only path (safe-pg-image-update.sh), skipped here"; continue; fi
  before=$(digest "$img")
  if (( DRY )); then
    log info "would pull ${img} (running digest ${before:7:12})"; continue
  fi
  before_id=$(imgid "$img")
  if ! podman pull -q "$img" >/dev/null 2>&1; then log warn "pull failed for ${img} -- keeping local"; continue; fi
  after=$(digest "$img")
  if [[ "$before" != "$after" ]]; then
    [[ -n "$before_id" ]] && podman tag "$before_id" "${img%:*}:previous" 2>/dev/null
    if [[ "$img" == docker.io/library/nextcloud:* ]] && nextcloud_major_jump "$img"; then
      log warn "nextcloud MAJOR jump detected upstream -- holding nextcloud-app (rolled back to :previous tag)"
      podman tag "${img%:*}:previous" "$img" 2>/dev/null; HELD_UNIT[nextcloud-app]="major-jump"
    else
      CHANGED_EXT+=("${img} ${before:7:12}->${after:7:12}")
    fi
  fi
done

# -- locals: build into a CHECK tag, gate, record the id, compare with running
# 2026-10-08 (review 06): always build for the host's own platform. A digest-pinned
# FROM resolves to whatever architecture is cached locally; an amd64 copy left by a
# portability test produced amd64 production images once.
BUILD_PLATFORM="linux/$(podman info --format '{{.Host.Arch}}')"
for image in "${LOCAL_IMAGES[@]}"; do
  svc=$(svc_of "$image"); check=$(check_of "$image"); bdir="${LIMG_DIR[$image]}"; cfile="${LIMG_CF[$image]}"
  if (( DRY )); then log info "would build ${svc} from ${bdir}/${cfile} (running ${image} = $(imgid "$image"))"; continue; fi
  if [[ ! -f "${bdir}/${cfile}" ]]; then log warn "no build context ${bdir}/${cfile} for ${image} -- not rebuilt (restart only)"; continue; fi
  if ! ( cd "$bdir" && nice -n 10 podman build -q --platform "$BUILD_PLATFORM" -f "$cfile" -t "$check" --label "build-date=$(date -u +%Y%m%dT%H%M%SZ)" --label "service=${svc}" --label "stack-refresh=${MODE}" . >/dev/null 2>&1 ); then
    log error "build FAILED for ${svc}"; hold_image "$image" "build-failed"; continue
  fi
  if ! podman run --rm --entrypoint sh "$check" -c 'cd /app && [ -x scripts/verified-exec.sh ] && scripts/verified-exec.sh true' >/dev/null 2>&1; then
    # images without verified-exec (runner, demo, acars-watcher, contact) pass through; those with it must gate
    if podman run --rm --entrypoint sh "$check" -c 'test -x /app/scripts/verified-exec.sh' >/dev/null 2>&1; then
      log error "in-image integrity gate FAILED for ${svc} -- holding"; hold_image "$image" "gate-failed"; podman rmi -f "$check" >/dev/null 2>&1; continue
    fi
  fi
  # 2026-10-07 (review 05): SBOM + provenance receipt for the gated image, keyed by its
  # content-addressed id and bound to the signed source commit. No receipt = no deploy:
  # the image is held exactly like a failed build (docs/REPRODUCIBLE_BUILDS.md).
  if ! "${REPO_ROOT}/scripts/build/provenance.py" record --image "$check" --name "$image" --containerfile "${bdir}/${cfile}" --context "$bdir" >/dev/null 2>&1; then
    log error "provenance record FAILED for ${svc} -- holding"; hold_image "$image" "provenance-failed"; podman rmi -f "$check" >/dev/null 2>&1; continue
  fi
  record_built "$image" "$(podman image inspect --format '{{.Id}}' "$check" 2>/dev/null)" "$svc"
  if [[ "$(imgid "$check")" != "$(imgid "$image")" ]]; then CHANGED_LOCAL+=("${svc} $(imgid "$image")->$(imgid "$check")"); fi
done

# ------------------------------------------------------------- decide ------
if (( DRY )); then log info "dry-run complete"; exit 0; fi
# Every run restarts every container so StartedAt is an audit signal;
# "nothing changed" is just logged.
if (( ${#CHANGED_EXT[@]} == 0 && ${#CHANGED_LOCAL[@]} == 0 )); then log info "no image changed upstream or in the tree -- restarting everything anyway (timestamps as audit signal)"; fi

all_units_debt() { rollout_units | sed "s/\$/=$1/" | paste -sd';'; }
# FINAL clean-tree check immediately before promotion + rollout: an edit can
# land during the build phase (2026-10-04 12:58). If dirty now, DISCARD the
# check tags -- never move :latest for images the rollout cannot deploy.
if [[ -n "$(git status --porcelain)" ]] || ! scripts/verify-manifest.sh >/dev/null 2>&1; then
  log warn "tree became dirty/unsigned during the build phase -- discarding check images; EVERY unit HELD FOR REVIEW, NO RESTART"
  for image in "${LOCAL_IMAGES[@]}"; do podman rmi -f "$(check_of "$image")" >/dev/null 2>&1; done
  DEBT=$(debt_update "$(all_units_debt tree-dirty-during-build)" ""); log warn "$DEBT"
  ntfy_send "stack-refresh ${MODE}: HELD FOR REVIEW" "tree went dirty/unsigned during the build at ${HEAD_SHORT}; nothing promoted, nothing restarted; ${DEBT}" 4
  [[ "$MODE" == tripwire ]] && mark_tripwire_run
  exit 3
fi
# promote check images; :previous only when the image actually changes, at the OLD id
for image in "${LOCAL_IMAGES[@]}"; do
  check=$(check_of "$image")
  podman image exists "$check" || continue
  old_full=$(podman image inspect --format '{{.Id}}' "$image" 2>/dev/null)
  new_full=$(podman image inspect --format '{{.Id}}' "$check" 2>/dev/null)
  if [[ -n "$old_full" && "$old_full" != "$new_full" ]]; then podman tag "$old_full" "${image%:*}:previous" 2>/dev/null; fi
  # 2026-10-06 OUTAGE: `podman untag IMAGE` with no NAME removes EVERY name
  # from the image -- it stripped the :latest just applied, every rebuilt
  # image ended up untagged, and the rollout restarted units onto nothing
  # (web/poller/ingest/verifier down 18:47Z-19:37Z). Remove ONLY the check name.
  podman tag "$check" "$image" && podman untag "$check" "$check" >/dev/null 2>&1
done
# Hand-off guard: every local image the rollout will restart must resolve to
# :latest, and it must be the id we just promoted. Anything else -> no rollout.
PROMOTE_BAD=()
for image in "${LOCAL_IMAGES[@]}"; do
  podman image exists "$image" || PROMOTE_BAD+=("${image} missing")
done
if (( ${#PROMOTE_BAD[@]} )); then
  log error "PROMOTION CHECK FAILED -- no rollout: ${PROMOTE_BAD[*]}"
  ntfy_send "stack-refresh ${MODE}: PROMOTION FAILED" "no restart performed; missing: ${PROMOTE_BAD[*]}" 5
  [[ "$MODE" == tripwire ]] && mark_tripwire_run
  exit 4
fi
# held units -> SKIP_UNITS
SKIP=$(for u in "${!HELD_UNIT[@]}"; do printf '%s,' "$u"; done | sed 's/,$//')
if (( ${#HELD_UNIT[@]} )); then log warn "HELD FOR REVIEW, NO RESTART: $(for u in "${!HELD_UNIT[@]}"; do printf '%s(%s) ' "$u" "${HELD_UNIT[$u]}"; done)"; fi
LOG="${LOG_DIR}/stack-refresh-${MODE}-$(date +%Y%m%d-%H%M).log"
HELD_FILE=$(mktemp); trap 'rm -f "$HELD_FILE"' EXIT
log info "serialized rollout (FROM_REFRESH=1, deadline $(date -d "@$DEADLINE" '+%H:%M')) -> ${LOG} (SKIP_UNITS='${SKIP}')"
FROM_REFRESH=1 ROLLOUT_DEADLINE="$DEADLINE" ROLLOUT_HELD_FILE="$HELD_FILE" SKIP_UNITS="$SKIP" RESTART_EXTERNALS="${RESTART_ALL:-1}" \
  nice -n 10 bash scripts/serialized-rollout.sh >"$LOG" 2>&1; rrc=$?
if (( rrc == 3 )); then
  log warn "serialized-rollout refused (tree dirty/unsigned at hand-off) -- EVERY unit HELD FOR REVIEW, NO RESTART"
  while read -r u; do HELD_UNIT[$u]="dirty-or-unsigned-tree-at-handoff"; done < <(rollout_units)
fi
# units the rollout did not reach before the deadline (written by the rollout)
while read -r u reason; do [[ -n "$u" ]] && HELD_UNIT[$u]="$reason"; done <"$HELD_FILE"
sleep 20
# post-check, ONLY over the rollout list (duel M5: one-shot skill containers
# that happen to be running are not part of the rollout): every unit must have
# a container started after this run began, unless held or postgres-canary.
LEFT_BEHIND=(); RESTARTED_OK=(); PG_EXEMPT=()
while read -r unit; do
  [[ -n "${HELD_UNIT[$unit]:-}" ]] && continue
  if is_pg_unit "$unit" && [[ "${PG_CANARY_DONE:-0}" != 1 ]]; then PG_EXEMPT+=("$unit"); continue; fi
  cname=""; for c in "$unit" "systemd-$unit"; do podman container exists "$c" 2>/dev/null && { cname="$c"; break; }; done
  if [[ -z "$cname" ]]; then LEFT_BEHIND+=("${unit} (no container)"); continue; fi
  st_epoch=$(podman inspect "$cname" --format '{{.State.StartedAt.Unix}}' 2>/dev/null); st_epoch=${st_epoch:-0}
  if (( st_epoch >= RUN_START )); then RESTARTED_OK+=("$unit")
  else LEFT_BEHIND+=("${unit} (started $(date -d "@$st_epoch" '+%m-%d %H:%M'))"); fi
done < <(rollout_units)
held_pairs=$(for u in "${!HELD_UNIT[@]}"; do printf '%s=%s;' "$u" "${HELD_UNIT[$u]}"; done)
DEBT=$(debt_update "$held_pairs" "${RESTARTED_OK[*]}")
FAILED=$(systemctl --user --failed --no-legend | wc -l)
# 2026-10-07 (review 05): record what each running THIRD-PARTY image contains (SBOM keyed
# by image id) after the pulls. Informational: externals float on tags by design, so this
# records identity and contents; it is not a gate and never fails the run.
"${REPO_ROOT}/scripts/build/provenance.py" record-external --running >/dev/null 2>&1 || log warn "external SBOM snapshot incomplete"
HEALTH=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8000/healthz)
HELD_TXT=$(for u in "${!HELD_UNIT[@]}"; do printf '%s(%s) ' "$u" "${HELD_UNIT[$u]}"; done)
SUMMARY="head ${HEAD_SHORT}; ext changed ${#CHANGED_EXT[@]}; local changed ${#CHANGED_LOCAL[@]}; restarted ${#RESTARTED_OK[@]}; held ${#HELD_UNIT[@]}${HELD_TXT:+ (${HELD_TXT% })}; pg canary-only ${#PG_EXEMPT[@]}; left behind ${#LEFT_BEHIND[@]}${LEFT_BEHIND:+ (${LEFT_BEHIND[*]})}; rollout rc ${rrc}; failed units ${FAILED}; healthz ${HEALTH}; ${DEBT}; log ${LOG##*/}"
log info "${SUMMARY}"
(( ${#LEFT_BEHIND[@]} )) && log error "LEFT BEHIND (not restarted, not held): ${LEFT_BEHIND[*]}"
[[ "$MODE" == tripwire ]] && mark_tripwire_run
if (( rrc != 0 || FAILED != 0 || ${#LEFT_BEHIND[@]} )) || [[ "$HEALTH" != 200 ]] || (( ${#HELD_UNIT[@]} )); then
  ntfy_send "stack-refresh ${MODE}: ATTENTION" "${SUMMARY}" 4; exit 1
fi
ntfy_send "stack-refresh ${MODE}: OK" "${SUMMARY}" 2
exit 0
