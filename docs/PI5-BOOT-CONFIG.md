# Raspberry Pi 5 — NVMe Boot Configuration Reference

Recovery reference for SD card failure. Documents all boot-layer settings
that live outside the container stack and are not captured by `build-images.sh`.

---

## What survives an SD card failure

| Layer | Where stored | Survives SD failure? |
|---|---|---|
| Bootloader EEPROM config (`BOOT_ORDER`, NVMe priority) | Onboard EEPROM (soldered) | ✅ YES |
| `/boot/firmware/config.txt` (kernel/device tree flags) | SD card | ❌ NO — must reapply |
| Container images | SD card | ❌ NO — rebuild with `build-images.sh` |
| SQLite database | SD card | ❌ NO — runtime state only |
| Secrets (`dispatch-secrets.env`) | SD card | ❌ NO — re-enter from credential sources |

---

## `/boot/firmware/config.txt` — Required NVMe flags

These must be present on every new SD card / image before attempting NVMe boot.

```ini
# Enable PCIe / NVMe interface
dtparam=nvme

# PCIe Gen 3 (unofficial but stable on Pi 5 — ~2x throughput vs Gen 2)
# Remove this line to fall back to Gen 2 if stability issues arise
dtparam=pciex1_gen=3
```

Add these to the `[all]` section (or after the `[pi5]` conditional if one exists).

---

## EEPROM bootloader — Boot order

The EEPROM `BOOT_ORDER` was already updated before the SD card failure and
**does not need to be redone**. For reference, the target value is:

```
BOOT_ORDER=0xf416
```

Decoded right-to-left: `6`=NVMe → `1`=SD card → `4`=USB MSD → `f`=restart loop.
NVMe is tried first; SD card is the fallback.

To verify the current EEPROM config on a running Pi:

```bash
rpi-eeprom-config
```

To update if ever needed:

```bash
sudo -E rpi-eeprom-config --edit
# Set: BOOT_ORDER=0xf416
# Then reboot — change takes effect on next boot
```

---

## Full new-SD-card recovery sequence

1. Flash a fresh Raspberry Pi OS (64-bit) to SD card
2. Boot from SD card
3. Add NVMe flags to `/boot/firmware/config.txt` (see above)
4. Reboot — Pi will enumerate the NVMe drive
5. Use `rpi-imager` or `dd` to clone/flash the OS to the NVMe drive
6. Verify EEPROM `BOOT_ORDER` includes NVMe (see above — likely already set)
7. Remove SD card and reboot — should boot from NVMe
8. Restore dispatch stack:
   ```bash
   cd /opt/corporatetraveldc
   git clone https://github.com/CorporateTravelDC/corporatetraveldc-dispatch.git .
   cp dispatch-secrets.env.example /etc/corporatetraveldc/dispatch-secrets.env
   chmod 0600 /etc/corporatetraveldc/dispatch-secrets.env
   # Populate secrets, then:
   bash build-images.sh
   systemctl --user daemon-reload
   systemctl --user start corporatetraveldc-web corporatetraveldc-poller corporatetraveldc-pusher
   ```

---

## Notes

- The `dispatch-assistant` OS image builder pipeline (libguestfs / virt-customize)
  is **not in this repo**. If it existed on the Pi before the SD failure, it needs
  to be reconstructed. See `docs/PENDING.md` for status.
- Tailscale, Pi-hole, Nextcloud, UltraFeeder, and nginx vhost configs were also
  on the SD card and will need reinstallation on a fresh image.
