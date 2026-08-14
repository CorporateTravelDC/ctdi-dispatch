# Raspberry Pi 5 — Boot Configuration Reference (Fedora ARM)

**Rewritten 2026-08-11 against the live system.** Recovery/reference doc for the
boot-layer settings that live outside the container stack and are not captured
by `build-images.sh`.

> **Important correction from the previous revision:** this system runs
> **Fedora Linux 44 (Workstation Edition), aarch64** — *not* Raspberry Pi OS.
> The firmware config file is **`/boot/config.txt`** (Fedora ARM layout, with
> `os_prefix=/efi/`), not `/boot/firmware/config.txt`. The earlier revision of
> this doc assumed Raspberry Pi OS paths throughout.

---

## Current boot layout (verified 2026-08-11)

| Item | Value |
|---|---|
| OS | Fedora Linux 44 (Workstation Edition), aarch64 |
| Boot device | NVMe (`nvme0n1`, 238.5 GB) — **no SD card is present or used** |
| `/boot` | `nvme0n1p1` (488 MB) — contains `config.txt` directly |
| Root / home | `nvme0n1p2` (btrfs, mounted at `/` and `/home`) |
| Swap | `zram0` (8 GB compressed RAM swap) — no disk swap |
| Firmware config | `/boot/config.txt` (uses `os_prefix=/efi/`) |

Because the whole system lives on NVMe, the old "what survives an SD card
failure" framing no longer applies. The failure domain is the NVMe drive
itself; recovery means reflashing Fedora ARM to a new drive (or temporary SD)
and restoring the stack per the README's installation section.

---

## `/boot/config.txt` — live contents that matter (verified 2026-08-11)

Active (non-commented) directives currently in effect:

```ini
arm_64bit=1
os_prefix=/efi/
start_x=1

# Serial console
dtoverlay=pi3-disable-bt
enable_uart=1

# Audio + hardware watchdog
dtparam=audio=on,watchdog=on

[pi5]
camera_auto_detect=1
display_auto_detect=1
dtoverlay=vc4-kms-v3d
disable_overscan=1

[pi5]
dtoverlay=argonone
```

### Fan / thermal overlays — current state

- **`dtoverlay=gpio-fan,temp=65000` is commented out** (removed 2026-08-10):

  ```ini
  # REMOVED 2026-08-10: dead GPIO fan overlay, Argon ONE leftover, no physical fan attached
  #dtoverlay=gpio-fan,temp=65000
  ```

  This was a leftover from the retired Argon ONE case (see
  `docs/HARDWARE_GUIDANCE.md` — that case is a documented hard-pass for this
  workload). The overlay created a phantom `gpio_fan` hwmon device
  (`/sys/class/hwmon/hwmon3` as of this snapshot) with no physical fan
  attached. **A reboot is still pending to clear the phantom device** —
  until then `hwmon3: gpio_fan` still appears in sysfs.

- **`dtoverlay=argonone` is still present** under a second `[pi5]` section —
  another Argon ONE leftover. It has not been observed causing harm, but it is
  a candidate for removal in the same future reboot window as the gpio-fan
  cleanup.

- The real fan is the case PWM fan, visible as **`pwmfan`**
  (`/sys/class/hwmon/hwmon4` at this snapshot). Because removing the dead
  gpio-fan overlay will renumber hwmon devices on the next reboot,
  `scripts/thermal-sample.sh` and `scripts/thermal-ingest-guard.py` resolve
  the fan **by hwmon name (`pwmfan`), never by a fixed hwmon index**.

Current hwmon inventory (2026-08-11, pre-reboot):

```
hwmon0: cpu_thermal   hwmon1: nvme   hwmon2: rp1_adc
hwmon3: gpio_fan (phantom — clears on reboot)
hwmon4: pwmfan        hwmon5: rpi_volt
```

---

## NVMe boot flags

The previous revision documented `dtparam=nvme` and `dtparam=pciex1_gen=3` as
required additions. **Neither line is present in the live `/boot/config.txt`**
— the system boots from NVMe without them on this Fedora image (Fedora's
kernel/firmware handles PCIe enumeration without the Pi-OS-style dtparams).
Do not re-add them blindly on this deployment; treat them as Pi-OS-specific
guidance only.

## EEPROM bootloader

`rpi-eeprom-config` / `vcgencmd` are **not installed** on this Fedora system,
so the EEPROM `BOOT_ORDER` cannot be verified from the running OS. The
previous revision recorded `BOOT_ORDER=0xf416` (NVMe first, SD fallback) as
having been set before the original SD→NVMe migration; the machine does in
fact boot from NVMe with no SD card present, which is consistent with that
value, but the exact register value is **unverified as of 2026-08-11**. To
verify or change it, boot a Raspberry Pi OS medium that ships the
`rpi-eeprom` tooling.

---

## Recovery sequence (NVMe failure, Fedora)

1. Flash Fedora Workstation/Server for aarch64 (Raspberry Pi) to a new NVMe
   drive or temporary SD card.
2. Restore `/boot/config.txt` from this document (serial console + watchdog
   lines; **omit** the gpio-fan and argonone Argon ONE leftovers).
3. Recreate the `corporatetraveldc` user, rootless Podman + linger, SELinux
   enforcing.
4. Restore the stack: clone the repo to
   `/opt/corporatetraveldc/private/ctdi-dispatch-internal`, repopulate
   `/etc/corporatetraveldc/dispatch.env` and `dispatch-secrets.env` (mode
   0600) from credential sources, `bash build-images.sh`, install the
   Quadlets from `.config/containers/systemd/`, `systemctl --user
   daemon-reload`, then let `corporatetraveldc-stack-boot-stagger.service` /
   `corporatetraveldc-boot-stagger.service` bring the stack up staggered.
5. Reinstall the host-level layers that live outside this repo: Tailscale
   (native `tailscaled.service` — see `docs/HEADLESS_ACCESS.md`), Pi-hole +
   Unbound (`pihole-unbound-selinux-internal` repo), nginx vhosts
   (`nginx/conf.d/` in this repo is the reference copy), cloudflared,
   Nextcloud, and Ollama + `build-models.sh`.
