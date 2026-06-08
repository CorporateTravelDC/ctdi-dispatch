# Raspberry Pi 5 — NVMe Boot Configuration Reference

## `/boot/firmware/config.txt` — Required NVMe flags

These must be present on every new SD card / image before attempting NVMe boot.

```ini
# Enable PCIe / NVMe interface
dtparam=nvme

# PCIe Gen 3 (unofficial but stable on Pi 5 — ~2x throughput vs Gen 2)
# Remove this line to fall back to Gen 2 if stability issues arise
dtparam=pciex1_gen=3
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
Recover installed instance
---

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
