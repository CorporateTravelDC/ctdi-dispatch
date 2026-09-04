#!/bin/bash
# rtl-eeprom-reserialize.sh
# Reserialize the ACARS dongle (currently "acars0130") to "vdl20130"
# for use as the VDL Mode 2 decoder on the primary Pi.
#
# Final dongle roles after this script:
#   adsb1090  → unchanged, continues as UltraFeeder (ADS-B at 1090 MHz)
#   vdl20130  → (was acars0130) dumpvdl2 decoder (VDL2 at ~136 MHz)
#
# Run on the Pi as root (or with sudo).
# Requires rtl-sdr tools: sudo dnf install rtl-sdr   # Fedora
#
# IMPORTANT: Run with ONLY the target dongle connected if possible,
# or identify the correct device index from the inventory step below.
set -euo pipefail

echo "=== RTL-SDR dongle inventory ==="
rtl_test -t 2>&1 | grep -E "Found|Serial|Index" || true
echo ""

echo "=== Step 1: Confirm target dongle ==="
echo "Looking for device with serial 'acars0130' ..."
echo ""

# If only the acars dongle is connected, -d 0 is correct.
# If both dongles are connected, find the acars0130 index in the inventory above.
DEVICE_INDEX=${1:-0}
echo "Using device index: ${DEVICE_INDEX}"
echo "Current EEPROM info:"
rtl_eeprom -d "${DEVICE_INDEX}" 2>&1 | head -30
echo ""

read -p "Is this the 'acars0130' dongle you want to reserialize to 'vdl20130'? [y/N] " confirm
if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
    echo "Aborted."
    exit 1
fi

echo ""
echo "=== Step 2: Reserialize to 'vdl20130' ==="
rtl_eeprom -d "${DEVICE_INDEX}" -s vdl20130

echo ""
echo "=== Step 3: Verify ==="
echo "New EEPROM contents (confirm 'vdl20130'):"
rtl_eeprom -d "${DEVICE_INDEX}" 2>&1 | grep -i serial

echo ""
echo "=== Done ==="
echo "Power-cycle the Pi (or unplug/replug ONLY this dongle) for the new serial to take effect."
echo "The adsb1090 dongle is untouched."
echo ""
echo "After reboot — set up udev rules so containers address dongles by serial:"
cat <<'UDEV'

# /etc/udev/rules.d/99-rtlsdr.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="adsb1090",  SYMLINK+="adsb1090"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{serial}=="vdl20130",  SYMLINK+="vdl20130"

# After writing the file:
# sudo udevadm control --reload-rules && sudo udevadm trigger
UDEV
