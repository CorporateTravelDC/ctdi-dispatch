Verified against HEAD db64018 and live state on 2026-10-06 18:10Z / 14:10 ET.
Re-checked 2026-10-15 ~01:20Z against main 1491bf5a and the live box (last boot ~~2026-10-06 10:17Z~~ (corrected 2026-10-25: last boot 2026-10-22 07:15:53Z, `uptime -s`)): disk use,
`/tmp`, kernel command line, watchdog and the recovery sequence updated below.

# Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM)

Recovery/reference doc for the boot-layer settings that live outside the
container stack and are not captured by `build-images.sh`.

> This system runs **Fedora Linux 44 (Workstation Edition), aarch64** —
> not Raspberry Pi OS. The firmware config file is **`/boot/config.txt`**
> (Fedora ARM layout, with `os_prefix=/efi/`), not `/boot/firmware/config.txt`.

---

## Current boot layout (re-verified live 2026-10-06)

Re-derived with `cat /etc/os-release`, `uname -a`, `lsblk`, `df -h`, and
`grep -E ' / | /home | /boot ' /proc/mounts`.

| Item | Value |
|---|---|
| OS | Fedora Linux 44 (Workstation Edition), aarch64 — kernel ~~`6.18.50-1.rpi5.fc44.aarch64`~~ (corrected 2026-10-25: `6.18.57-1.rpi5.fc44.aarch64`, `uname -r`; booted 2026-10-22) (**changed** from the 2026-08-23 `6.18.36` baseline — a kernel update has landed since) |
| Boot device | NVMe (`nvme0n1`, 238.5 GB) — no SD card present or used |
| `/boot` | `nvme0n1p1` (488 MB, **vfat**), ~~70%~~ (2026-10-25: 74%) used — contains `config.txt` directly; there is no `/boot/firmware/` |
| Root / home | `nvme0n1p2` (238 GB, **btrfs**) — one partition, two subvolumes: `subvol=/root` → `/`, `subvol=/home` → `/home` (same device shows twice in `df`), ~~now **65% used** (146G/238G) — up from the 48% recorded 2026-08-11; grown with ~2 months of ingest/vault data~~ (corrected 2026-10-15: ~~**83% used**, 188G of 238G, 39G free~~ (superseded 2026-10-25: 80% used, 179G of 238G, 47G free, `df -h /` 21:20Z), per `df -h /`; it was 77% at the 2026-10-12 audit. The local Borg backup repository, ~~27 GB~~ (superseded 2026-10-25: 53 GB, `du -sh ~/.local/state/ctdc-backup/borg` 2026-10-26 ~02:25Z), lives on this volume; see `docs/BACKUP.md`) |
| Swap | `zram0` (8 GiB compressed RAM swap) **plus** a disk-backed swap file `/var/swap/nvme-overflow.swap` (8 GiB, priority 10) on the NVMe. By design (operator, 2026-10-06): an OOM safety margin so zram + swap file = 16 GiB of swap. zram (higher priority) absorbs normal pressure; the NVMe file is only touched once zram is full, so a runaway build or model load pages out instead of tripping the OOM killer. |
| Firmware config | `/boot/config.txt` (uses `os_prefix=/efi/`) |
| `/tmp` | **tmpfs**, 8 GB (~~32%~~ used at the 2026-10-15 check; 7% on 2026-10-25). On 2026-10-14 ~23:15Z it filled to 100% with 6,005 leaked test databases, which broke GPG signature checks in the integrity sweep. Since then `corporatetraveldc-tmp-headroom-guard.timer` (user, every 5 min) keeps at least 10% free, reclaiming only this user's stale test databases, old pytest directories and 24 h-old `mktemp` entries, and sends one ops-health alert if it is still under the floor (`scripts/tmp-headroom-guard.sh`) |
| Kernel command line | `serial-getty@ttyAMA0.service` is masked (2026-10-12; it was in a restart loop). ~~The persisted command line `/boot/efi/cmdline.txt` (unchanged since 2026-07-11) still carries `console=serial0,115200 console=tty1`. The firmware maps serial0 to ttyAMA0, so the running kernel shows `console=ttyAMA0,115200` in `/proc/cmdline` and will keep doing so after a reboot. Removing the kernel console from the UART means editing `serial0,115200` out of `/boot/efi/cmdline.txt` (operator, root); as of 2026-10-15 no file under /boot has changed since 2026-10-11.~~ (corrected 2026-10-25: `/boot/efi/cmdline.txt` now carries only `console=tty1` and `/proc/cmdline` shows `console=tty1`, so the kernel console is off the UART; `/boot` has changed since 2026-10-11 -- ~~484~~ 489 files newer (2026-10-26 02:21Z), the 6.18.57 kernel among them; `cat`, `find /boot -newermt 2026-10-11` 21:20Z.) |

Because the whole system lives on NVMe, "what survives an SD card
failure" doesn't apply. The failure domain is the NVMe drive itself;
recovery means reflashing Fedora ARM to a new drive (or temporary SD) and
restoring the stack per the README's installation section.

---

## `/boot/config.txt` — live contents that matter (re-verified 2026-10-06)

Byte-identical to the 2026-08-23 revision of this doc — no changes since:

```ini
arm_64bit=1
os_prefix=/efi/
start_x=1

# Serial console
dtoverlay=pi3-disable-bt
enable_uart=1

# Audio + hardware watchdog
dtparam=audio=on,watchdog=on

[pi4]
camera_auto_detect=1
display_auto_detect=1
dtoverlay=vc4-kms-v3d
arm_boost=1
disable_overscan=1

[pi5]
camera_auto_detect=1
display_auto_detect=1
dtoverlay=vc4-kms-v3d
disable_overscan=1
[pi5]
dtoverlay=argonone
```

(The `[pi4]` block is inert on this Pi 5 but is part of the real file;
reproduced exactly as the recovery source. The repeated `[pi5]` header and
the leftover `dtoverlay=argonone` line are both real, both harmless.)

### Hardware watchdog — now overridden from the default 14s

**Current (2026-10-15): 30 s.** `systemctl show -p RuntimeWatchdogUSec` → `30s`. The one-shot tune ran on
2026-10-12 00:00Z (and again at 03:34:59Z), found a worst stall of 0.0 s since 2026-10-05 01:15, and wrote
`RuntimeWatchdogSec=30` (the floor) into the live `/etc/systemd/system.conf.d/zz-ctdc-watchdog.conf`.
**Drift:** the tracked copy `config/systemd/system.conf.d/zz-ctdc-watchdog.conf` still says
`RuntimeWatchdogSec=180`, because `scripts/watchdog-tune.sh` rewrites only the live file. Reinstalling the
tracked copy would put 180 s back. `corporatetraveldc-stall-monitor.service` is still enabled and running
after the diagnostic week; whether it stays is an open operator item. The paragraph below is the 2026-10-06
record.

`dtparam=watchdog=on` enables the BCM2835 hardware watchdog at the kernel
level; **the actual timeout is set by systemd**, not by this line. Live
2026-10-06: `systemctl show -p RuntimeWatchdogUSec` → **`3min`** (180s),
via `/etc/systemd/system.conf.d/zz-ctdc-watchdog.conf` (RuntimeWatchdogSec=180),
which overrides the stock `watchdog.conf`'s `RuntimeWatchdogSec=14`. This
~~is a **diagnostic override, not permanent**~~ was a diagnostic override (superseded 2026-10-12 by the 30 s tune above): it was raised after the
2026-10-04 19:53 ET hard reset, in which a >14s scheduling stall (under
load ~20 from concurrent rebuilds/restarts/test runs) tripped the
hardware watchdog with no shutdown sequence logged. `corporatetraveldc-stall-monitor`
is recording the worst scheduling stall all week (live report 2026-10-06:
`max_gap_s: 0.0`, no stalls recorded since the window opened, one
`boot_gaps` entry for the 2026-10-04 reset itself); a one-shot
`corporatetraveldc-watchdog-tune.timer` fires **Sun 2026-10-11 20:00 ET**
and rewrites `zz-ctdc-watchdog.conf` to `ceil(1.2 × worst stall)`, floored
at 30s and capped at 180s — confirmed still scheduled live
(`systemctl list-timers` → next `2026-10-12 00:00:00 UTC`).

### Fan / thermal overlays — current state (unchanged since 2026-08-18)

- `dtoverlay=gpio-fan,temp=65000` remains commented out (removed
  2026-08-10, dead Argon ONE leftover, no physical fan attached).
- `dtoverlay=argonone` is still present under a second `[pi5]` section —
  another Argon ONE leftover, still harmless, no observed effect.
- The real fan is the case PWM fan, **`pwmfan`** — confirmed live
  2026-10-06 still at `/sys/class/hwmon/hwmon3` (current reading: ~2325
  RPM at 62.8°C). `scripts/thermal-sample.sh` and
  `scripts/thermal-ingest-guard.py` resolve the fan by hwmon **name**
  (`pwmfan`), never a fixed index — confirmed in `thermal-sample.sh`.

Current hwmon inventory (re-derived live 2026-10-06, unchanged from
2026-08-23):

```
hwmon0: cpu_thermal   hwmon1: nvme     hwmon2: rp1_adc
hwmon3: pwmfan        hwmon4: rpi_volt
```

---

## NVMe boot flags

`dtparam=nvme` and `dtparam=pciex1_gen=3` are **not present** in the live
`/boot/config.txt` (re-confirmed 2026-10-06) — the system boots from NVMe
without them on this Fedora image. Do not re-add them blindly; they are
Pi-OS-specific guidance only.

## EEPROM bootloader

`rpi-eeprom-config` / `vcgencmd` are **still not installed** on this
Fedora system (re-confirmed live 2026-10-06: `which` finds neither), so
the EEPROM `BOOT_ORDER` cannot be verified from the running OS.
`[UNVERIFIED: exact BOOT_ORDER register value — unchanged from the
2026-08-11 finding; the machine boots from NVMe with no SD card present,
consistent with but not proof of the previously-set 0xf416]`. To verify or
change it, boot a Raspberry Pi OS medium that ships the `rpi-eeprom`
tooling.

---

## Recovery sequence (NVMe failure, Fedora)

> **Status (2026-10-15):** this sequence has never been run end to end. The weekly restore test checks the
> dumps inside the newest archive (hashes, `pg_restore --list`); it does not restore onto new hardware.
> (unverified 2026-10-25: whether the sequence was ever run is history no command on the box can show; the
> restore-test description holds -- `scripts/backup/backup-restore-test.sh:17-41`; last run 2026-10-25
> 05:30Z, `Result=success`, `systemctl --user show` 2026-10-26 02:23Z.)

1. Flash Fedora Workstation/Server for aarch64 (Raspberry Pi) to a new NVMe
   drive or temporary SD card.
2. Restore `/boot/config.txt` from this document (serial console + watchdog
   lines; **omit** the gpio-fan and argonone Argon ONE leftovers).
3. Recreate the `corporatetraveldc` user, rootless Podman + linger, SELinux
   enforcing.
4. Restore the stack: clone the repo to
   `/opt/corporatetraveldc/private/ctdi-dispatch-internal`, repopulate
   `/etc/corporatetraveldc/dispatch.env` and the per-service env under
   `/etc/corporatetraveldc/svc/` (mode 0600) from credential sources,
   `bash build-images.sh`, install the Quadlets from
   `.config/containers/systemd/`, `systemctl --user daemon-reload`, then
   let `corporatetraveldc-stack-boot-stagger.service` /
   `corporatetraveldc-boot-stagger.service` bring the stack up staggered.
   - **Restore the data (added 2026-10-15) before the staggered bring-up:** Install `borgbackup` and the pinned rclone (`docs/BACKUP.md`), put
     the Borg passphrase file back from the operator's offline copy (with `borg key export` output; without both,
     the archives cannot be read), and pull the repository back from Proton Drive (`proton:ctdi-backup`) with a
     fresh rclone login. Extract the newest archive: `pg_restore` the dispatch and Nextcloud dumps into fresh
     Postgres containers before the app stack starts, and restore `/var/lib/corporatetraveldc`,
     `/etc/corporatetraveldc` (secret-bearing), the `nextcloud-html` volume and `/var/lib/vnstat`. Not in the
     archive: the model files (re-download them) and the LADD table data, which never leaves the box (its local-only
     dumps are lost with the drive; obtain the FAA list again and re-import it per `docs/LADD_CUI_HANDLING.md`). Check the private ledger with `backup_ledger.py verify`, then re-enable the backup, freshness and restore-test timers.
5. Reinstall the host-level layers that live outside this repo: Tailscale
   (native `tailscaled.service` — see `docs/HEADLESS_ACCESS.md`), Pi-hole +
   Unbound (`pihole-unbound-selinux-internal` repo), nginx vhosts
   (`nginx/conf.d/` in this repo is the reference copy), cloudflared,
   Nextcloud, and the llama.cpp inference layer — the `llama-server`
   binary plus the **single** `corporatetraveldc-llama.service` (not a
   per-tier set of units; the hot/chat/report-tier split was retired
   2026-09-06 in favor of one resident model, two slots, hard-capped to
   two cores — confirmed live 2026-10-06) and its sibling
   `corporatetraveldc-llama-restart.timer`, currently serving
   Qwen3-4B-Instruct-2507 (q4_0) (swapped from phi3-mini 2026-09-21; see
   `docs/MODEL_EVALUATION_2026-09-21.md`). Since 2026-10-07 the unit's
   `ExecStartPre` runs `sha256sum --strict --check config/llama/artifacts.sha256`,
   so the llama-server binary, its libraries (under `/usr/local/lib/ollama/`)
   and the model file must match the pinned digests or the service will not
   start. Re-installed artifacts must be the same versions, or the pin file
   updated and signed. Related units: `corporatetraveldc-llama-council.timer` (system) and
   `corporatetraveldc-claude-weekly-llama-fallback.timer` (user). Ollama was retired at the
   2026-08-27 cutover; `build-models.sh` is now only a Modelfile↔personas.py
   verifier, not a build step.

## Findings for the operator (carried from live checks, not fixed here)

- (resolved 2026-10-06) The 8 GiB NVMe swap file is a deliberate OOM safety margin; see the Swap row above.

---

---

## Superseded (kept for the record)

Text removed or replaced by the 2026-10-06 verification pass against the live system, kept in its original wording for the chronological record. It is **not** current. The evidence for each correction is in `docs/docs-refresh-2026-10-06/CHANGES-ops.md`.


### Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM)

~~**Rewritten 2026-08-11 against the live system.** Recovery/reference doc for the boot-layer settings that live outside the container stack and are not captured by `build-images.sh`.~~

> ~~**Important correction from the previous revision:** this system runs **Fedora Linux 44 (Workstation Edition), aarch64** — *not* Raspberry Pi OS. The firmware config file is **`/boot/config.txt`** (Fedora ARM layout, with `os_prefix=/efi/`), not `/boot/firmware/config.txt`. The earlier revision of this doc assumed Raspberry Pi OS paths throughout.~~

**~~Current boot layout (verified 2026-08-11, re-verified 2026-08-23)~~** *(former heading)*


### Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM) › Current boot layout (verified 2026-08-11, re-verified 2026-08-23)

~~Re-derived with `cat /etc/os-release`, `uname -a`, `lsblk`, `df -h`, and `grep -E ' / | /home | /boot ' /proc/mounts`. Every row below still matches live state exactly.~~

| ~~Item~~ | ~~Value~~ |
|---|---|
| ~~OS~~ | ~~Fedora Linux 44 (Workstation Edition), aarch64 — kernel `6.18.36-1.rpi5.fc44.aarch64`~~ |
| ~~Boot device~~ | ~~NVMe (`nvme0n1`, 238.5 GB) — **no SD card is present or used**~~ |
| ~~`/boot`~~ | ~~`nvme0n1p1` (488 MB, **vfat**) — contains `config.txt` directly; there is no `/boot/firmware/`~~ |
| ~~Root / home~~ | ~~`nvme0n1p2` (238 GB, **btrfs**) — one partition, two subvolumes: `subvol=/root` → `/`, `subvol=/home` → `/home` (so `df` shows the same device twice, at 48% used)~~ |
| ~~Swap~~ | ~~`zram0` (8 GB compressed RAM swap) — no disk swap partition~~ |
| ~~Firmware config~~ | ~~`/boot/config.txt` (uses `os_prefix=/efi/`)~~ |

~~Because the whole system lives on NVMe, the old "what survives an SD card failure" framing no longer applies. The failure domain is the NVMe drive itself; recovery means reflashing Fedora ARM to a new drive (or temporary SD) and restoring the stack per the README's installation section.~~

**~~`/boot/config.txt` — live contents that matter (verified 2026-08-11)~~** *(former heading)*


### Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM) › `/boot/config.txt` — live contents that matter (verified 2026-08-11)

~~Active (non-commented) directives currently in effect:~~

*Superseded block:*
```text superseded
arm_64bit=1
os_prefix=/efi/
start_x=1

# Serial console
dtoverlay=pi3-disable-bt
enable_uart=1

# Audio + hardware watchdog
dtparam=audio=on,watchdog=on

[pi4]
# Automatically load overlays for detected cameras
camera_auto_detect=1

# Automatically load overlays for detected DSI displays
display_auto_detect=1

# Enable DRM VC4 V3D driver
dtoverlay=vc4-kms-v3d

# Allows the default turbo-mode clock to be increased from 1.5GHz to 1.8GHz
# Based on https://www.raspberrypi.com/documentation/computers/config_txt.html#arm_boost-raspberry-pi-4-only
arm_boost=1

disable_overscan=1

[pi5]
# Automatically load overlays for detected cameras
camera_auto_detect=1

# Automatically load overlays for detected DSI displays
display_auto_detect=1

# Enable DRM VC4 V3D driver
dtoverlay=vc4-kms-v3d

disable_overscan=1
[pi5]
dtoverlay=argonone
```

~~_(Section blocks re-pasted byte-accurate from the live `/boot/config.txt` on 2026-08-23 — an earlier revision of this doc showed the `[pi4]` block's content under a first `[pi5]` heading and omitted `arm_boost=1`. The `[pi4]` block is inert on this Pi 5 but is part of the real file, and this block is the stated recovery source, so it's reproduced exactly.)_~~

**~~Fan / thermal overlays — current state~~** *(former heading)*


### Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM) › `/boot/config.txt` — live contents that matter (verified 2026-08-11) › Fan / thermal overlays — current state

- ~~**`dtoverlay=gpio-fan,temp=65000` is commented out** (removed 2026-08-10):~~

*Superseded block:*
```text superseded
  # REMOVED 2026-08-10: dead GPIO fan overlay, Argon ONE leftover, no physical fan attached
  #dtoverlay=gpio-fan,temp=65000
```

~~This was a leftover from the retired Argon ONE case (see `docs/HARDWARE_GUIDANCE.md` — that case is a documented hard-pass for this workload). The overlay created a phantom `gpio_fan` hwmon device with no physical fan attached. The box rebooted **2026-08-18 21:19** and the phantom device is gone, as predicted.~~

- ~~**`dtoverlay=argonone` is still present** under a second `[pi5]` section — another Argon ONE leftover. It survived the 2026-08-18 reboot, is still in `/boot/config.txt`, and is still harmless (no observed effect).~~

- ~~The real fan is the case PWM fan, visible as **`pwmfan`** (`/sys/class/hwmon/hwmon3` post-reboot; it was `hwmon4` before). The reboot did renumber hwmon devices exactly as anticipated — and nothing broke, precisely because `scripts/thermal-sample.sh` and `scripts/thermal-ingest-guard.py` resolve the fan **by hwmon name (`pwmfan`), never by a fixed hwmon index**. Keep it that way.~~

~~Current hwmon inventory (re-derived live 2026-08-23, post-reboot):~~


### Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM) › NVMe boot flags

~~The previous revision documented `dtparam=nvme` and `dtparam=pciex1_gen=3` as required additions. **Neither line is present in the live `/boot/config.txt`** — the system boots from NVMe without them on this Fedora image (Fedora's kernel/firmware handles PCIe enumeration without the Pi-OS-style dtparams). Do not re-add them blindly on this deployment; treat them as Pi-OS-specific guidance only.~~


### Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM) › EEPROM bootloader

~~`rpi-eeprom-config` / `vcgencmd` are **not installed** on this Fedora system, so the EEPROM `BOOT_ORDER` cannot be verified from the running OS. The previous revision recorded `BOOT_ORDER=0xf416` (NVMe first, SD fallback) as having been set before the original SD→NVMe migration; the machine does in fact boot from NVMe with no SD card present, which is consistent with that value, but the exact register value is **unverified as of 2026-08-11**. To verify or change it, boot a Raspberry Pi OS medium that ships the `rpi-eeprom` tooling.~~


### Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM) › Recovery sequence (NVMe failure, Fedora)

1. ~~Flash Fedora Workstation/Server for aarch64 (Raspberry Pi) to a new NVMe drive or temporary SD card.~~
2. ~~Restore `/boot/config.txt` from this document (serial console + watchdog lines; **omit** the gpio-fan and argonone Argon ONE leftovers).~~
3. ~~Recreate the `corporatetraveldc` user, rootless Podman + linger, SELinux enforcing.~~
4. ~~Restore the stack: clone the repo to `/opt/corporatetraveldc/private/ctdi-dispatch-internal`, repopulate `/etc/corporatetraveldc/dispatch.env` and `dispatch-secrets.env` (mode 0600) from credential sources, `bash build-images.sh`, install the Quadlets from `.config/containers/systemd/`, `systemctl --user daemon-reload`, then let `corporatetraveldc-stack-boot-stagger.service` / `corporatetraveldc-boot-stagger.service` bring the stack up staggered.~~
5. ~~Reinstall the host-level layers that live outside this repo: Tailscale (native `tailscaled.service` — see `docs/HEADLESS_ACCESS.md`), Pi-hole + Unbound (`pihole-unbound-selinux-internal` repo), nginx vhosts (`nginx/conf.d/` in this repo is the reference copy), cloudflared, Nextcloud, and the llama.cpp inference layer — the `llama-server` binary + shared phi3-mini GGUF plus the `corporatetraveldc-llama-*` systemd user units from `.config/systemd/user/` (Ollama was retired at the 2026-08-27 cutover; `build-models.sh` is now only a Modelfile↔personas.py verifier, not a build step).~~
