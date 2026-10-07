# CHANGES — ops domain

Verified against HEAD `db64018` and live state on 2026-10-06 18:10Z / 14:10 ET.
Docs rewritten: `docs/HARDWARE_GUIDANCE.md`, `docs/PI5-BOOT-CONFIG.md`,
`docs/SDR_SERVICES.md`, `docs/SDR_SERVICES_README.md`,
`docs/NETWORK_FAILOVER.md`, `docs/NETWORK_STORAGE_CONSTRAINTS.md`,
`docs/BOOT_STORM_TIMER_REQUIRES.md`, `docs/TAILNET_MIGRATION_INVENTORY.md`,
`docs/COST_STRUCTURE.md`.

## docs/HARDWARE_GUIDANCE.md

| claim as written | evidence | fix |
|---|---|---|
| USB bus table: `ADSB1090` at `1-2`, `ACARS0130` at `3-2`, symlinks → `bus/usb/001/017` / `bus/usb/003/005` | live: `for d in /sys/bus/usb/devices/*/serial; do ...; done` → `1-2`=`ACARS0130`, `3-2`=`ADSB1090`; `ls -l /dev/rtl_sdr_*` → `rtl_sdr_acars -> bus/usb/001/003`, `rtl_sdr_adsb -> bus/usb/003/002` | table swapped and symlink targets updated to current live values; doc's own caveat ("bus positions change across reboots") predicted exactly this |
| "on-demand LLM inference (Ollama)" / SIGSTOP/SIGCONT governor as current mitigation | `systemctl --user cat corporatetraveldc-llama.service` → single service, llama.cpp, Qwen3-4B-Instruct-2507, hard-capped 2 cores/2 slots; `systemctl --user list-unit-files \| grep -i ollama` → none; `ollama.service` not found | reworded: Ollama retired, current mitigation is the core/slot cap, not a thermal-pause governor; kept the 2026-07-26 sample as history |
| RAM "15 GiB usable" | `free -h` → `15Gi` total | confirmed, kept, added live snapshot |
| swap "no disk swap partition" implied by zram-only framing (actually in PI5-BOOT-CONFIG, cross-checked here) | `swapon --show` → zram0 8G **and** `/var/swap/nvme-overflow.swap` 8G file | flagged in HARDWARE_GUIDANCE and detailed in PI5-BOOT-CONFIG |
| 7 ingest containers (6 SWIM + core) | `podman ps \| grep ingest` → notam, itws, tbfm, tfms, stdds, fdps, core = 7, all `Up` | confirmed unchanged |
| thermal thresholds 74°C/79°C, LOCKDOWN load1≥40 | `grep -n "74\|79\|LOAD_LOCKDOWN" scripts/thermal-ingest-guard.py` → `THERMAL_GUARD_TIER1_TEMP_C=74.0`, `TIER2_TEMP_C=79.0`, `LOAD_LOCKDOWN=40.0` | confirmed unchanged, kept |

[UNVERIFIED]
- Whether Argon ever responded to the filed support ticket — not tracked in this repo.
- 8 GB Pi 5 workability claim — no live 8GB unit to test against; left as guidance.

## docs/PI5-BOOT-CONFIG.md

| claim as written | evidence | fix |
|---|---|---|
| kernel `6.18.36-1.rpi5.fc44.aarch64` | live `uname -a` → `6.18.50-1.rpi5.fc44.aarch64` | updated; noted a kernel update landed since 2026-08-23 |
| root/home "48% used" | live `df -h` → `/dev/nvme0n1p2 238G 146G 81G 65%` | updated to 65%, noted growth |
| "Swap: zram0 ... no disk swap partition" | live `swapon --show` / `/proc/swaps` → zram0 8G (858.8M used) **plus** `/var/swap/nvme-overflow.swap` 8G file (0B used) | corrected; flagged as a Finding (undocumented addition) |
| `rpi-eeprom-config`/`vcgencmd` not installed | live `which rpi-eeprom-config vcgencmd` → neither found | confirmed unchanged |
| hwmon3=`pwmfan` | live `/sys/class/hwmon/hwmon3/name` → `pwmfan` | confirmed unchanged |
| Recovery step 5: "`llama-server` binary + shared phi3-mini GGUF plus the `corporatetraveldc-llama-*` systemd user units" (plural, per-tier) | live `systemctl --user cat corporatetraveldc-llama.service` → single unit, Description says it "replaces corporatetraveldc-llama-hot / -chat / -report-1 / -report-2"; model is Qwen3-4B-Instruct-2507 (q4_0) since 2026-09-21, not phi3-mini | corrected to single-service architecture and current model name |
| `dtparam=nvme`/`dtparam=pciex1_gen=3` not present | live `/boot/config.txt` read directly — neither line present | confirmed unchanged |

[UNVERIFIED]
- EEPROM `BOOT_ORDER` exact register value — tooling to read it isn't installed on Fedora; carried forward as unverified.
- Origin/intent of `/var/swap/nvme-overflow.swap` — not documented anywhere else found in this repo.

## docs/SDR_SERVICES.md / docs/SDR_SERVICES_README.md

| claim as written | evidence | fix |
|---|---|---|
| Enabled set (ultrafeeder, acarsrouter, dumpvdl2, acarshub, acars-watcher, piaware/fr24feed/planefinder/airnavradar) all running | live `systemctl --user list-units --all \| grep -iE '...'` → all listed `active running` | confirmed unchanged |
| No `.disabled` quadlets installed live | live `ls ~/.config/containers/systemd/*.disabled` → no matches | confirmed unchanged |
| Only `acarsdec.disabled` staged under `.config/containers/systemd/` | live `find . -name "*.disabled"` → confirms, plus found a new unrelated `amtrak-tracker.container.disabled` and an archived `systemd/retired-20261003/corporatetraveldc-acarsdec.container.disabled` not in the original doc | noted both as out-of-scope/archival additions |
| `AIS_STATIC_MMSI`/`ACARS_STATIC_REGS` unset | live `grep -c '^AIS_STATIC_MMSI=\|^ACARS_STATIC_REGS=' /etc/corporatetraveldc/dispatch.env` → `0` | confirmed unchanged (name-count check only, no values read) |
| udev rule file contents | live `cat /etc/udev/rules.d/99-rtlsdr-adsb.rules` | confirmed byte-identical |

No [UNVERIFIED] items for these two docs.

## docs/NETWORK_FAILOVER.md

| claim as written | evidence | fix |
|---|---|---|
| wld0 address 10.x.x.x | live `ip -br addr` → `wld0 ... 10.x.x.x/24` | updated |
| "No bond" | live `cat /proc/net/bonding/*` → directory empty/no files | confirmed unchanged |
| NM connectivity probe disabled | live `cat /etc/NetworkManager/conf.d/no-connectivity-check.conf` → `enabled=false` | confirmed unchanged |
| `/etc/resolv.conf` is `nameserver 127.0.0.1`, no forward-zone | live `cat /etc/resolv.conf`; `grep -c forward-zone /etc/unbound/unbound.conf` → `0` | confirmed unchanged |
| Watchdog timer every 60s | live `systemctl --user list-timers corporatetraveldc-net-failover-watchdog.timer` → fires every ~60s, last 18:05:06 UTC | confirmed unchanged |
| "connection NAME" primary = `Jorransgateway` (used in script default) | live `nmcli -t connection show` → actual ID is lowercase `jorransgateway`; `scripts/net-failover-watchdog.sh --status` → `primary: Jorransgateway (wld0) metric=` (empty, because the name lookup fails) | new Finding — case mismatch, documented |
| Backup uplink healthy / "truly unlimited" framing implies it's a working fallback today | live: direct `ping -I enu1` to 1.1.1.1/8.8.8.8/9.9.9.9` → 100% loss all three; `curl --interface enu1` → `http_code=000` on both tested; watchdog log `backup=enu1:DOWN` continuously 2026-10-06 05:30 ET → 18:10 ET (12.5h) | new, most consequential Finding — backup is currently non-functional despite looking "up" at L2/DHCP |

[UNVERIFIED]
- None outstanding — all checkable claims in this doc were verified live.

## docs/NETWORK_STORAGE_CONSTRAINTS.md

| claim as written | evidence | fix |
|---|---|---|
| Live deployment stays on local SSD/NVMe, not NAS | live `/proc/mounts` → no network filesystem mounted for `/`, `/home`, or any Postgres-relevant path | confirmed unchanged |
| References to `POSTGRES_MIGRATION.md` §1 and §3.1 | live `grep -n "^## 1\.\|^### 3.1" docs/POSTGRES_MIGRATION.md` → both sections exist as named | confirmed unchanged |
| `translate_sql()` in `db_backend.py` | live `grep -n "def translate_sql" src/common/db_backend.py` → `db_backend.py:344` | confirmed unchanged |
| unix-socket connection path | live `grep -n unix src/common/db_backend.py` → line 423 confirms containers reach Postgres over a unix-socket volume mount | confirmed unchanged |
| "geometric reasoning's Phase 0" described as a large backfill | live `sed -n '230,260p' docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md` → Phase 0 explicitly backfills `assign_geometry()` across "the full existing vault corpus... all ~7,000+ notes" | confirmed accurate, kept |

No [UNVERIFIED] items — this doc is forward-looking guidance for a
deployment shape that doesn't exist yet; everything checkable against
the current repo/live system checked out.

## docs/BOOT_STORM_TIMER_REQUIRES.md

| claim as written | evidence | fix |
|---|---|---|
| "Standing rule ... enforced by `scripts/check-timer-requires.sh`" (implying full coverage) | read `scripts/check-timer-requires.sh` lines 56-64 → loop only covers `.config/systemd/user/*.timer` (repo) and `~/.config/systemd/user/*.timer` (live); never scans `systemd/*.timer` or `/etc/systemd/system/*.timer` | **major correction** — rule is not fully enforced; documented the gap explicitly |
| (implicit) no live timer still violates the rule | live: `systemd/corporatetraveldc-watchdog.timer` and `systemd/corporatetraveldc-tailscale-cert-renew.timer` both carry `Requires=<self>.service` in `[Unit]`, confirmed identical in both the tracked repo copy and the installed `/etc/systemd/system/` copy; `systemctl list-timers` shows both active/enabled | **new Finding** — two live, root-managed timers currently violate the standing rule the doc claims is enforced |
| 61 of 67 timers carried the line (2026-09-05 incident) | historical, from the pre-existing doc narrative — not re-derivable from current state (units have since changed) | kept as history, labeled as such |
| Multi-tier LLM units (`-hot`/`-chat`/`-report-1`) existed at the time of the incident | cross-checked against `corporatetraveldc-llama.service`'s own Description, which says it "replaces corporatetraveldc-llama-hot / -chat / -report-1 / -report-2" | confirmed consistent; clarified in the rewrite that this architecture no longer exists today |
| All other `Requires=`/`Wants=` self-references are in retired directories | live: `grep -rl` across `.timer` files with a `[Unit]`-section Requires/Wants scan → every hit outside the two live root timers above is under `retired-20260803`/`retired-20260814`/`retired-20260816`/`retired-20260830` | confirmed, kept as supporting evidence for the Finding |

[UNVERIFIED]
- None — the central claim of this doc (full enforcement) was directly falsifiable and was checked by reading the enforcement script itself plus live unit state.

## docs/TAILNET_MIGRATION_INVENTORY.md

| claim as written | evidence | fix |
|---|---|---|
| Node IP `100.x.x.x`, tailnet `tailxxxxxxx.ts.net` | live `tailscale ip -4` → `100.x.x.x`; `tailscale status` → `corporatetraveldc-dispatch...tailxxxxxxx.ts.net` | confirmed unchanged |
| Peer `corporatetraveldc-pixel10` at `100.x.x.x` | live `tailscale status` → same IP now reports hostname `corporatetraveldc` (base name, not `-pixel10`) | noted as a device-side rename, not a migration defect; same IP so no action implied |
| "exactly one file" (`/etc/systemd/system/corporatetraveldc-tailscale-cert-renew.service`) still carried old suffix in `Description=` | live `grep -rl tailxxxxxxx /etc/nginx/conf.d /etc/systemd/system` → **no matches now** | updated — this has since been closed (repo/live resync) |
| "four files, all legitimate" carry `tailxxxxxxx` repo-wide | live `grep -rl tailxxxxxxx --exclude-dir=.git .` → same 4 files (`TAILNET_MIGRATION_INVENTORY.md`, `HEADLESS_ACCESS.md`, `INFRA_MAP.md`, `scrub-public-tree.py`) | confirmed unchanged |
| Historical grep-dump sections (LIVE-ETC/PUBLIC-REPO-REAL/INTERNAL-REPO/PIHOLE-REPO/PLACEHOLDER-ONLY) | not re-run line-by-line (pre-migration, 2026-08-04 snapshot by design) | kept as historical record, explicitly marked `[UNVERIFIED: not re-grepped line-by-line]`, condensed the INTERNAL-REPO file list to a file/module summary rather than reproducing all 83 lines (the STATUS section already re-confirms the only facts that matter going forward) |
| `OLLAMA_BASE_URL`/`ollama.*` entries in LIVE-ETC | live: Ollama retired 2026-08-27, replaced by `LLAMA_BASE_URL` | added a note distinguishing historical pre-migration fact from current config, without altering the historical record itself |

[UNVERIFIED]
- Full line-by-line re-verification of all 83 INTERNAL-REPO references and the 43 PLACEHOLDER-ONLY references — this document's own purpose is a dated pre-migration snapshot; re-running every grep would reproduce the original audit, not correct it. The STATUS section's targeted re-checks stand in.

## docs/COST_STRUCTURE.md

| claim as written | evidence | fix |
|---|---|---|
| SR-1 log "24,338 logged invocations spanning 2026-07-09 → 2026-08-23" | live `wc -l /var/lib/corporatetraveldc/api-usage.csv` → 73,285 lines (73,284 data rows); date range `awk -F, '{print $1}' \| sort \| sed -n '1p;$p'` → 2026-06-27 → 2026-10-06 | updated counts and date range |
| "Zero cloud calls" | live `grep -icE 'claude\|anthropic\|gpt\|openai' /var/lib/corporatetraveldc/api-usage.csv` → `0` | confirmed unchanged |
| "every row's model is ... a local `corporatetraveldc-pi5-*` **Ollama** model" | live: Ollama retired 2026-08-27; `corporatetraveldc-llama.service` (llama.cpp) is the current server; `ollama.service` not found live | corrected — the model-name convention is unchanged but the serving provider is not Ollama |
| `deterministic` row count "5,208" / "341 of ~810 in the last 24h" | live `grep -c ',deterministic,' api-usage.csv` → 22,431 of 73,284 (~31%) | updated |
| `ANTHROPIC_FALLBACK_ENABLED=false` at `dispatch.env:200` | live `grep -n ANTHROPIC_FALLBACK_ENABLED /etc/corporatetraveldc/dispatch.env` → now at line 99, value still `false` | corrected line number, confirmed value unchanged |
| token columns sum to 0 | live `awk -F, '{it+=$4;ot+=$5;cr+=$6;cw+=$7}' api-usage.csv` → all 0 across 73,284 rows | confirmed unchanged |
| Offload Pi "PROPOSED / not yet built" | live `grep -n "3.1" docs/INFRA_MAP.md` → still "PROPOSED — ... NOT built" | confirmed unchanged |

[UNVERIFIED]
- All CapEx figures (Pi/SSD/case/PSU prices, SDR/antenna tier adders, FlightAware/FR24 subscription prices) — explicitly operator-stated planning estimates from 2026-08-05, not independently priced or re-verified; the brief's "verified-from-config / operator-stated / estimate" labeling applies: **all §1 and §2 dollar figures are operator-stated**, not verified-from-config or independently estimated by this review.
- Power (~$50-100/yr) and internet (~$5-6k/yr) operating-cost figures — operator-stated, not metered/verified.

## Findings for the operator

1. **Backup internet uplink (enu1 / Verizon Hotspot) is currently non-functional** — 100% packet loss on ICMP and failed TCP:443 to three well-known IPs, continuously for at least 12.5 hours at review time, despite showing L2 carrier-up and a valid DHCP lease. If the primary Wi-Fi fails right now, there is no working failover path; the watchdog would correctly take the "both down, alert only" branch rather than a real failover. Consistent with the reported 2026-10-06 06:01 ET incident — the backup does not appear to have recovered since. **Most consequential finding in this batch.** (`docs/NETWORK_FAILOVER.md`)
2. **The boot-storm `Requires=` guard has a real coverage gap and two live violations.** `scripts/check-timer-requires.sh` never scans root-managed timers (`systemd/*.timer` / `/etc/systemd/system/*.timer`), and `corporatetraveldc-watchdog.timer` plus `corporatetraveldc-tailscale-cert-renew.timer` both still carry the exact self-referential `Requires=` the standing rule bans — live and tracked, not retired. (`docs/BOOT_STORM_TIMER_REQUIRES.md`)
3. **Net-failover-watchdog's primary-connection name doesn't match live NetworkManager state** (`Jorransgateway` hardcoded default vs. live `jorransgateway`) — currently harmless (the script never acts on the primary by name) but makes `--status` print a blank metric for the primary and would silently no-op any future code path that does act on it by name. No env override compensates. (`docs/NETWORK_FAILOVER.md`)
4. **An undocumented disk-backed swap file exists** (`/var/swap/nvme-overflow.swap`, 8 GiB) alongside the previously-documented zram-only swap. Not referenced anywhere else in this repo that this review found. Not necessarily wrong, but worth a one-line note somewhere durable so it isn't mistaken for drift later. (`docs/PI5-BOOT-CONFIG.md`, `docs/HARDWARE_GUIDANCE.md`)
5. A new, unrelated `.disabled` quadlet (`amtrak-tracker.container.disabled`) and an archived acarsdec copy (`systemd/retired-20261003/...`) now exist alongside the SDR `.disabled` set; neither affects SDR_SERVICES.md's claims but both were new since the prior revision and are noted for completeness. (`docs/SDR_SERVICES.md`)
