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
REPO=/opt/corporatetraveldc/private/ctdi-dispatch-internal
WEBSITE=/opt/corporatetraveldc/private/csexecutiveservices-website
D=$(date -u +%Y-%m-%dT%H:%M:%SZ)
load() { cut -d' ' -f1 /proc/loadavg; }
guard() { journalctl --user -u corporatetraveldc-thermal-ingest-guard -n 8 --no-pager -o cat 2>/dev/null | grep -oE 'load1=[0-9.]+ tier=[0-9]' | tail -1; }
digest() { podman image inspect --format '{{.Digest}}' "$1" 2>/dev/null; }
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
)

built_dirs=""   # build each local image once (7 ingest instances share one)
for row in "${ROLLOUT[@]}"; do
  IFS='|' read -r unit kind image bdir cfile win <<<"$row"
  echo "################ $unit  ($kind: $image) ################"
  base=$(load); echo "$(date +%T)  baseline load $base  ($(guard))"
  changed=yes
  if [[ $kind == local ]]; then
    key="$bdir/$cfile"
    if [[ " $built_dirs " == *" $key "* ]]; then
      echo "$(date +%T)  image already rebuilt this run"
    elif [[ ! -f $bdir/$cfile ]]; then
      echo "$(date +%T)  NO BUILD CONTEXT at $bdir/$cfile -- restart only"
    else
      echo "$(date +%T)  build $image from $bdir/$cfile"
      if ( cd "$bdir" && podman build -f "$cfile" -t "$image" --label build-date=$D . >/dev/null 2>&1 ); then
        built_dirs="$built_dirs $key"; echo "$(date +%T)  build ok, load $(load)"
      else
        echo "$(date +%T)  BUILD FAILED for $image -- stopping here"; exit 1
      fi
    fi
  else
    before=$(digest "$image")
    echo "$(date +%T)  pull $image (update check)"
    podman pull -q "$image" >/dev/null 2>&1 || echo "$(date +%T)  pull FAILED (offline/registry?) -- keeping local image"
    after=$(digest "$image")
    if [[ $before == "$after" ]]; then changed=no; echo "$(date +%T)  image unchanged ($after)"; else echo "$(date +%T)  IMAGE UPDATED: ${before:7:12} -> ${after:7:12}"; fi
    if [[ $changed == no && $RESTART_EXTERNALS != 1 ]]; then echo "$(date +%T)  skip restart (unchanged)"; echo; continue; fi
  fi
  # never start the next unit on top of a spike we are still riding
  while awk "BEGIN{exit !($(load) >= 15)}"; do echo "$(date +%T)  load $(load) >= 15, holding before restart"; sleep 30; done
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
curl -s -o /dev/null -w 'keyed research %{http_code}\n' -H "X-Board-Key: $(sudo grep -m1 '^BOARD_KEY=' /etc/corporatetraveldc/dispatch-secrets.env | cut -d= -f2-)" 'http://127.0.0.1:8000/api/v1/board?thread=research'
journalctl --user -u corporatetraveldc-thermal-ingest-guard --since "@$(( $(date +%s) - 3600 ))" --no-pager -o cat | grep -E 'DORMANT|LOCKDOWN|shed:|SHED' | tail -3
echo "$(date +%T)  final load $(load)  ($(guard))"
