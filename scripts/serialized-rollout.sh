#!/bin/bash
# serialized-rollout.sh -- EVERY container, one at a time (operator directive
# 2026-09-06: "Build + restart one at a time and annotate the spike on
# launch ... EVERY container even externals (including a update check on
# those)"). First live run 2026-09-06 15:41-16:30 EDT: 31/31 active, peak
# load 9.61 for the entire stack (the same stack restarted flat that
# morning peaked 40.28 and tripped LOCKDOWN). This discipline is to become
# the boot (stack-boot-ctl.sh) and LOCKDOWN-recovery bring-up -- work-order
# item 1. ASCII only.
#
# Usage: bash scripts/serialized-rollout.sh 2>&1 | tee ~/rollout-$(date +%H%M).log
#   RESTART_EXTERNALS=0  restart third-party containers only if their image changed
#
# One at a time: local images rebuilt from their repo,
# external images pulled (update check -- prints whether the digest
# changed), then the unit restarted, load sampled every 10 s through the
# launch window, spike annotated. Nothing starts until the previous
# window closes. Third-party containers are restarted only if their image
# changed OR RESTART_EXTERNALS=1 (default: 1 -- "every container").
set -u
RESTART_EXTERNALS=${RESTART_EXTERNALS:-1}
# 2026-10-04 (stack-refresh standing practice): comma-separated units to
# skip entirely this run, e.g. SKIP_UNITS=nextcloud-app when the wrapper
# detected a Nextcloud MAJOR-version jump upstream (09-03 incident).
SKIP_UNITS=${SKIP_UNITS:-}
# 2026-10-04 duel H2: when stack-refresh.sh hands off it has ALREADY pulled,
# built, gated and promoted the images. FROM_REFRESH=1 = restart only: no
# build, no pull, no tag -- so the deployed image IS the gated image and the
# :previous rollback tag stack-refresh set is not overwritten.
FROM_REFRESH=${FROM_REFRESH:-0}
# Postgres rows (corporatetraveldc-pgsql, nextcloud-db) change only through
# scripts/safe-pg-image-update.sh's canary path; they are skipped entirely
# unless the operator says the canary has been done (duel H2: today's manual
# rollouts pulled postgres:16-alpine at 14:29/14:31 and restarted both DBs).
PG_CANARY_DONE=${PG_CANARY_DONE:-0}
# Wall-clock deadline (epoch) from stack-refresh: rows not reached by then are
# HELD FOR REVIEW, NO RESTART and written to ROLLOUT_HELD_FILE ("unit reason").
ROLLOUT_DEADLINE=${ROLLOUT_DEADLINE:-0}
ROLLOUT_HELD_FILE=${ROLLOUT_HELD_FILE:-}
BUILT_IDS=/var/lib/corporatetraveldc/reports/stack-refresh-built-ids.json
# 2026-10-04 09:35 ET: this script rebuilds local images FROM THE WORKING TREE
# with no gate of its own. A sanity run overlapped two agents editing src/ and
# only timing kept a half-edited db.py out of the ingest image. Refuse a dirty
# tree unless the operator says so (ALLOW_DIRTY=1) -- images must come from a
# signed tree (see feedback: sign before build).
if [[ "${ALLOW_DIRTY:-0}" != 1 ]] && [[ -n "$(git -C "$(dirname "${BASH_SOURCE[0]}")/.." status --porcelain 2>/dev/null)" ]]; then
  echo "serialized-rollout: working tree is DIRTY -- refusing to build images from unsigned files (ALLOW_DIRTY=1 to override)" >&2
  exit 3
fi
REPO=/opt/corporatetraveldc/private/ctdi-dispatch-internal
WEBSITE=/opt/corporatetraveldc/private/csexecutiveservices-website
D=$(date -u +%Y-%m-%dT%H:%M:%SZ)
load() { cut -d' ' -f1 /proc/loadavg; }
guard() { journalctl --user -u corporatetraveldc-thermal-ingest-guard -n 8 --no-pager -o cat 2>/dev/null | grep -oE 'load1=[0-9.]+ tier=[0-9]' | tail -1; }
digest() { podman image inspect --format '{{.Digest}}' "$1" 2>/dev/null; }
imgid_full() { podman image inspect --format '{{.Id}}' "$1" 2>/dev/null; }
held() {  # held <unit> <reason>
  echo "################ $1  HELD FOR REVIEW, NO RESTART ($2) ################"; echo
  [[ -n "$ROLLOUT_HELD_FILE" ]] && printf '%s %s\n' "$1" "$2" >>"$ROLLOUT_HELD_FILE"
}
past_deadline() { (( ROLLOUT_DEADLINE > 0 && $(date +%s) >= ROLLOUT_DEADLINE )); }
record_built() {  # record_built <image> <full-id> -- run-manifest the stack-refresh audit trusts
  mkdir -p "$(dirname "$BUILT_IDS")"
  python3 - "$BUILT_IDS" "$1" "$2" <<'PYEOF'
import json, sys, time
path, image, iid = sys.argv[1:4]
try: d = json.load(open(path))
except Exception: d = []
d.append({"id": iid, "image": image, "ts": int(time.time()), "by": "serialized-rollout"})
json.dump(d[-200:], open(path, "w"), indent=1)
PYEOF
}
SUMMARY=()

# unit | kind | image | build-dir | containerfile | window-seconds
# kind: local (build) / ext (pull)
ROLLOUT=(
  # --- SWIM ingest feeds, lightest first (ingest-feed-ctl.sh LIGHT_ORDER), core last
  "corporatetraveldc-ingest-notam|local|localhost/corporatetraveldc-ingest:latest|$REPO|Containerfile.ingest|60"
  "corporatetraveldc-ingest-itws|local|localhost/corporatetraveldc-ingest:latest|$REPO|Containerfile.ingest|60"
  "corporatetraveldc-ingest-tbfm|local|localhost/corporatetraveldc-ingest:latest|$REPO|Containerfile.ingest|60"
  "corporatetraveldc-ingest-tfms|local|localhost/corporatetraveldc-ingest:latest|$REPO|Containerfile.ingest|60"
  "corporatetraveldc-ingest-stdds|local|localhost/corporatetraveldc-ingest:latest|$REPO|Containerfile.ingest|60"
  "corporatetraveldc-ingest-fdps|local|localhost/corporatetraveldc-ingest:latest|$REPO|Containerfile.ingest|90"
  "corporatetraveldc-ingest-core|local|localhost/corporatetraveldc-ingest:latest|$REPO|Containerfile.ingest|90"
  # --- app tier
  "corporatetraveldc-poller|local|localhost/corporatetraveldc-poller:latest|$REPO|Containerfile.poller|180"
  # 2026-10-04: runs the poller image but was never in this list -- the first
  # stack-refresh audit caught it two poller rebuilds stale.
  "corporatetraveldc-execstandard-verifier|local|localhost/corporatetraveldc-poller:latest|/opt/corporatetraveldc/private/ctdi-dispatch-internal|Containerfile.poller|60"
  "corporatetraveldc-pusher|local|localhost/corporatetraveldc-pusher:latest|$REPO|Containerfile.pusher|60"
  "corporatetraveldc-web|local|localhost/corporatetraveldc-web:latest|$REPO|Containerfile.web|90"
  "corporatetraveldc-runner|local|localhost/corporatetraveldc-runner:latest|$REPO|Containerfile.runner|90"
  "corporatetraveldc-runner-demo|local|localhost/corporatetraveldc-runner:latest|$REPO|Containerfile.runner|60"
  "corporatetraveldc-demo-api|local|localhost/corporatetraveldc-demo:latest|$REPO|Containerfile.demo|60"
  "corporatetraveldc-demo|local|localhost/corporatetraveldc-demo:latest|$REPO|Containerfile.demo|60"
  "amtrak-tracker|local|localhost/corporatetraveldc-amtrak-tracker:latest|$REPO|Containerfile.amtrak-tracker|60"
  "corporatetraveldc-acars-watcher|local|localhost/corporatetraveldc-acars-watcher:latest|$REPO/src/acars_watcher|Containerfile|60"
  "csexec-contact|local|localhost/csexec-contact:latest|$WEBSITE/contact-api|Containerfile|60"
  # --- externals: pull = update check
  "corporatetraveldc-pgsql|ext|docker.io/library/postgres:16-alpine|||60"
  "nextcloud-db|ext|docker.io/library/postgres:16-alpine|||60"
  "nextcloud-app|ext|docker.io/library/nextcloud:stable-apache|||120"
  "ntfy|ext|docker.io/binwiederhier/ntfy:v2.25.0|||30"
  "rss-bridge|ext|docker.io/rssbridge/rss-bridge:latest|||30"
  "openwebui|ext|ghcr.io/open-webui/open-webui:main|||120"
  "corporatetraveldc-protonbridge|ext|docker.io/schklom/protonmail-bridge:latest-arm64|||60"
  "corporatetraveldc-ultrafeeder|ext|ghcr.io/sdr-enthusiasts/docker-adsb-ultrafeeder:latest|||90"
  "corporatetraveldc-piaware|ext|ghcr.io/sdr-enthusiasts/docker-piaware:latest|||60"
  "corporatetraveldc-fr24feed|ext|ghcr.io/sdr-enthusiasts/docker-flightradar24:latest|||60"
  "corporatetraveldc-planefinder|ext|ghcr.io/sdr-enthusiasts/docker-planefinder:latest|||60"
  "corporatetraveldc-airnavradar|ext|ghcr.io/sdr-enthusiasts/docker-airnavradar:latest|||60"
  "corporatetraveldc-dumpvdl2|ext|ghcr.io/sdr-enthusiasts/docker-dumpvdl2:latest|||60"
  "corporatetraveldc-acarsrouter|ext|ghcr.io/sdr-enthusiasts/acars_router:latest|||60"
  "corporatetraveldc-acarshub|ext|ghcr.io/sdr-enthusiasts/docker-acarshub:latest|||90"
  # 2026-10-04: the two password-gated static demo sites (tracked this morning; the
  # first restart-everything run left them behind because they were not listed)
  "corporatetraveldc-ccw-demo|ext|docker.io/library/nginx:alpine|||30"
  "corporatetraveldc-ccw-preview1|ext|docker.io/library/nginx:alpine|||30"
)

built_dirs=""   # build each local image once (7 ingest instances share one)
for row in "${ROLLOUT[@]}"; do
  IFS='|' read -r unit kind image bdir cfile win <<<"$row"
  if [[ ",$SKIP_UNITS," == *",$unit,"* ]]; then echo "################ $unit  HELD FOR REVIEW, NO RESTART (SKIP_UNITS) ################"; echo; continue; fi
  if [[ $image == docker.io/library/postgres:* && $PG_CANARY_DONE != 1 ]]; then
    echo "################ $unit  SKIPPED: postgres changes only via safe-pg-image-update.sh (PG_CANARY_DONE=1 to include) ################"; echo; continue
  fi
  if past_deadline; then held "$unit" "deadline"; continue; fi
  echo "################ $unit  ($kind: $image) ################"
  base=$(load); echo "$(date +%T)  baseline load $base  ($(guard))"
  changed=yes
  if [[ $FROM_REFRESH == 1 ]]; then
    echo "$(date +%T)  FROM_REFRESH: restart only (image already pulled/built, gated and promoted by stack-refresh)"
  elif [[ $kind == local ]]; then
    key="$bdir/$cfile"
    if [[ " $built_dirs " == *" $key "* ]]; then
      echo "$(date +%T)  image already rebuilt this run"
    elif [[ ! -f $bdir/$cfile ]]; then
      echo "$(date +%T)  NO BUILD CONTEXT at $bdir/$cfile -- restart only"
    else
      echo "$(date +%T)  build $image from $bdir/$cfile"
      # 2026-10-03 operator rule: keep a ready-to-go rollback for 24h
      # (scheduled-podman-prune honours the window). duel H2: :previous is
      # re-pointed only on a REAL change, at the OLD image id, after the build.
      old_id=$(imgid_full "$image")
      if ( cd "$bdir" && podman build -f "$cfile" -t "$image" --label build-date=$D --label "service=$(basename "${image%:*}" | sed 's/^corporatetraveldc-//')" . >/dev/null 2>&1 ); then
        new_id=$(imgid_full "$image")
        if [[ -n "$old_id" && "$old_id" != "$new_id" ]]; then podman tag "$old_id" "${image%:*}:previous" 2>/dev/null || true; fi
        record_built "$image" "$new_id"
        built_dirs="$built_dirs $key"; echo "$(date +%T)  build ok, load $(load)"
      else
        echo "$(date +%T)  BUILD FAILED for $image -- stopping here"; exit 1
      fi
    fi
  else
    before=$(digest "$image"); before_id=$(imgid_full "$image")
    echo "$(date +%T)  pull $image (update check)"
    podman pull -q "$image" >/dev/null 2>&1 || echo "$(date +%T)  pull FAILED (offline/registry?) -- keeping local image"
    after=$(digest "$image")
    if [[ $before == "$after" ]]; then changed=no; echo "$(date +%T)  image unchanged ($after)"
    else
      echo "$(date +%T)  IMAGE UPDATED: ${before:7:12} -> ${after:7:12}"
      # duel H2: :previous only on a real change, pointed at the OLD image id
      [[ -n "$before_id" ]] && podman tag "$before_id" "${image%:*}:previous" 2>/dev/null || true
    fi
    # 2026-10-04: a container still running an OLDER image than the tag (a
    # previous pull that never got its restart) counts as changed too.
    running_id=$(podman inspect "$unit" --format '{{.Image}}' 2>/dev/null | cut -c1-12); [[ -z "$running_id" ]] && running_id=$(podman inspect "systemd-$unit" --format '{{.Image}}' 2>/dev/null | cut -c1-12)
    tag_id=$(podman image inspect "$image" --format '{{.Id}}' 2>/dev/null | cut -c1-12)
    if [[ $changed == no && -n "$running_id" && -n "$tag_id" && "$running_id" != "$tag_id" ]]; then changed=yes; echo "$(date +%T)  running ${running_id} != tag ${tag_id} -- restart pending from an earlier pull"; fi
    if [[ $changed == no && $RESTART_EXTERNALS != 1 ]]; then echo "$(date +%T)  skip restart (unchanged)"; echo; continue; fi
  fi
  # never start the next unit on top of a spike we are still riding (but the
  # deadline wins: a unit not started by then is held, not started late)
  hit_deadline=0
  while awk "BEGIN{exit !($(load) >= 15)}"; do
    if past_deadline; then hit_deadline=1; break; fi
    echo "$(date +%T)  load $(load) >= 15, holding before restart"; sleep 30
  done
  if (( hit_deadline )); then held "$unit" "deadline-while-holding-for-load"; continue; fi
  # 2026-10-06: never restart a unit onto an image that does not exist -- a
  # restart cannot be undone and the unit comes back failed (the 18:47Z outage).
  if ! podman image exists "$image"; then
    echo "$(date +%T)  IMAGE MISSING ($image) -- NOT restarting $unit, stopping the rollout"
    held "$unit" "image-missing"; exit 1
  fi
  echo "$(date +%T)  restart $unit.service"
  t0=$(date +%s)
  systemctl --user restart "$unit.service"
  peak=0; peak_at=0
  for ((s=0; s<win; s+=10)); do
    sleep 10
    l=$(load); st=$(systemctl --user is-active "$unit.service")
    printf '%s  +%3ds  load %-6s %s\n' "$(date +%T)" $((s+10)) "$l" "$st"
    if awk "BEGIN{exit !($l > $peak)}"; then peak=$l; peak_at=$((s+10)); fi
  done
  st=$(systemctl --user is-active "$unit.service")
  line="$unit: baseline $base -> peak $peak at +${peak_at}s -> end $(load)  [$st] $( [[ $kind == ext ]] && echo "image:$changed" )"
  echo "$(date +%T)  >>> $line  ($(guard))"; SUMMARY+=("$line")
  if [[ $unit == corporatetraveldc-poller ]]; then
    echo "--- poller start-up lines:"
    journalctl --user -u corporatetraveldc-poller.service --since "@$t0" --no-pager -o cat | grep -iE 'hold-?off|stagger|not_before|coalesc|llama|Traceback|Error' | head -10
  fi
  echo
done

echo "################ summary ################"
printf '%s\n' "${SUMMARY[@]}"
echo
systemctl --user list-units 'corporatetraveldc-*.service' 'nextcloud-*.service' 'ntfy.service' 'rss-bridge.service' 'openwebui.service' 'amtrak-tracker.service' 'csexec-contact.service' --state=failed --no-legend
curl -s -o /dev/null -w 'anon research %{http_code}\n' 'http://127.0.0.1:8000/api/v1/board?thread=research'
# 2026-09-20: was a bare `sudo grep` -- prompted for a password every run
# (no matching NOPASSWD grant) and silently degraded to an unauthenticated
# request instead of actually testing keyed auth. Reading a secret is more
# sensitive than the ollama.service/dnf grants this gate already covers,
# so it goes through the same request-and-approve flow rather than a
# static passwordless sudoers line -- see scripts/sudo-approval-gate.sh.
# FROM_REFRESH (unattended stack-refresh) never raises an approval request
if [[ $FROM_REFRESH == 1 ]]; then echo "$(date +%T)  final load $(load)  ($(guard))"; exit 0; fi
# 2026-10-05: no approval at all -- this script runs as the operator, who OWNS
# dispatch-secrets.env (0600), so `sudo` was never needed; the gate turned a
# health check into a 10-minute phone prompt that expires whenever the
# operator is mobile (07:35 today: expired, keyed check read 403). Read it
# directly, never echo it, hand it to curl on a private fd.
BOARD_KEY_VAL=$(grep -m1 '^BOARD_KEY=' /etc/corporatetraveldc/dispatch-secrets.env 2>/dev/null | cut -d= -f2- || true)
curl -s -o /dev/null -w 'keyed research %{http_code}\n' -H @<(printf 'X-Board-Key: %s\n' "${BOARD_KEY_VAL}") 'http://127.0.0.1:8000/api/v1/board?thread=research'
journalctl --user -u corporatetraveldc-thermal-ingest-guard --since "@$(( $(date +%s) - 3600 ))" --no-pager -o cat | grep -E 'DORMANT|LOCKDOWN|shed:|SHED' | tail -3
echo "$(date +%T)  final load $(load)  ($(guard))"
