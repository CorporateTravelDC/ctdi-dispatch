#!/bin/bash
# deploy-acars-stack.sh
# Deploys the ACARS/VDL2 stack on the primary Pi.
#
# LIVE services (2 dongles in dual-output mode):
#   adsb1090  → UltraFeeder (unchanged) → ADS-B aggregators + corporatetraveldc-poller polls airplanes.live
#   vdl20130  → dumpvdl2 → airframes.io + acars-watcher UDP
#   acars-watcher → consumes dumpvdl2 UDP + airframes.io REST → ntfy on watchlist matches
#
# DISABLED (installed but not started — enable as hardware arrives):
#   corporatetraveldc-acarsdec.container.disabled  (future ACARS dongle)
#   corporatetraveldc-dumphfdl.container.disabled  (future HFDL dongle)
#   corporatetraveldc-ais.container.disabled        (future AIS dongle)
#
# Prerequisites:
#   a) rtl-eeprom-reserialize.sh run to rename acars0130 → vdl20130
#   b) Both dongles connected (adsb1090, vdl20130)
#   c) udev rules in place (see rtl-eeprom-reserialize.sh output)
set -euo pipefail

SRC_DIR="/opt/corporatetraveldc/src/acars_watcher"
QUADLET_DIR="${HOME}/.config/containers/systemd"

echo "=== [1/6] Verify UltraFeeder is still running ==="
if systemctl --user is-active ultrafeeder &>/dev/null; then
    echo "  UltraFeeder is active — leaving it running (ADS-B dual-output is already live)"
else
    echo "  UltraFeeder not running — you may want to start it separately:"
    echo "    systemctl --user start ultrafeeder"
fi

echo ""
echo "=== [2/6] Create shared pod network ==="
cat > "${QUADLET_DIR}/corporatetraveldc.network" <<'NETEOF'
[Network]
Label=project=corporatetraveldc
NETEOF
echo "  corporatetraveldc.network Quadlet written"

echo ""
echo "=== [3/6] Build acars-watcher image ==="
mkdir -p "${SRC_DIR}"
cp acars_watcher.py "${SRC_DIR}/"
cp Containerfile.acars-watcher "${SRC_DIR}/Containerfile"
cd "${SRC_DIR}"
podman build -t localhost/corporatetraveldc-acars-watcher:latest .
echo "  Image built: localhost/corporatetraveldc-acars-watcher:latest"
cd -

echo ""
echo "=== [4/6] Install Quadlets ==="
# Live containers
cp corporatetraveldc-acars-watcher.container "${QUADLET_DIR}/"
cp corporatetraveldc-dumpvdl2.container      "${QUADLET_DIR}/"

# Disabled (future hardware) — copied as .disabled so systemd ignores them
cp corporatetraveldc-acarsdec.container.disabled "${QUADLET_DIR}/"
cp corporatetraveldc-dumphfdl.container.disabled "${QUADLET_DIR}/"
cp corporatetraveldc-ais.container.disabled      "${QUADLET_DIR}/"

echo "  Active Quadlets:   acars-watcher, dumpvdl2"
echo "  Disabled Quadlets: acarsdec, dumphfdl, ais"

echo ""
echo "=== [5/6] Reload systemd ==="
systemctl --user daemon-reload

echo ""
echo "=== [6/6] Enable and start live services ==="
# Watcher first — UDP port must be ready before decoders send to it
systemctl --user enable --now corporatetraveldc-acars-watcher.service
echo "  acars-watcher: started"
sleep 3
systemctl --user enable --now corporatetraveldc-dumpvdl2.service
echo "  dumpvdl2:      started"

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Live service status:"
echo "  systemctl --user status corporatetraveldc-acars-watcher"
echo "  systemctl --user status corporatetraveldc-dumpvdl2"
echo "  systemctl --user status ultrafeeder"
echo ""
echo "Logs:"
echo "  journalctl --user -u corporatetraveldc-acars-watcher -f"
echo "  journalctl --user -u corporatetraveldc-dumpvdl2 -f"
echo ""
echo "Verify local VDL2 messages reaching watcher:"
echo "  journalctl --user -u corporatetraveldc-acars-watcher | grep LOCAL"
echo ""
echo "When future hardware arrives, enable a disabled service:"
echo "  cd ${QUADLET_DIR}"
echo "  cp corporatetraveldc-acarsdec.container.disabled corporatetraveldc-acarsdec.container"
echo "  systemctl --user daemon-reload && systemctl --user enable --now corporatetraveldc-acarsdec"
echo ""
echo "NOTE: Set AIRFRAMES_API_KEY in /etc/corporatetraveldc/dispatch-secrets.env once obtained."
echo "      Set STATION in dumpvdl2 Quadlet to match your feeder ID before the 7-day timer starts."
