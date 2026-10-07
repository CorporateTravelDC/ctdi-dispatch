#!/command/with-contenv bash
#shellcheck shell=bash

# shellcheck disable=SC1091
source /scripts/common

set -e

VDLM_BIN="/usr/local/bin/dumpvdl2"
# shellcheck disable=SC2001
FREQ_STRING="136650000 136800000 136900000 136925000 136975000 "
VDLM_CMD=()

if [[ -z "${RTL_SERIAL}" ]] && [[ -n "${SERIAL}" ]]; then
    # if RTL_SERIAL not set, use legacy env var SERIAL
    RTL_SERIAL="${SERIAL}"
fi

# Specify device ID
if [ -n "${SOAPYSDR}" ]; then
	VDLM_CMD+=("--soapysdr" "$SOAPYSDR")
    if [[ -n "$SOAPY_DEVICE_SETTINGS" ]]; then
        VDLM_CMD+=("--device-settings" "$SOAPY_DEVICE_SETTINGS")
    fi
elif [[ -n "${RTL_SERIAL}" ]]; then
	VDLM_CMD+=("--rtlsdr" "$RTL_SERIAL")
    if chk_enabled "${BIASTEE}"; then
        VDLM_CMD+=("--bias" "1")
    else
        VDLM_CMD+=("--bias" "0")
    fi
fi

if [ -n "$CENTER_FREQ" ]; then
    VDLM_CMD+=("--centerfreq" "$CENTER_FREQ")
fi

if [ -z "$GAIN" ]; then
    GAIN="40"
fi

if [ -n "$OVERSAMPLE" ]; then
    VDLM_CMD+=("--oversample" "$OVERSAMPLE")
fi

if [ -n "${PPM}" ]; then
	VDLM_CMD+=("--correction" "$PPM")
fi

VDLM_CMD+=("--gain" "$GAIN")

if [[ ${VDLM_FILTER_ENABLE,,} =~ true ]]; then
  VDLM_CMD+=("--msg-filter" "$VDLM_FILTER")
fi

# Send output JSON to vdlm2_server.
VDLM_CMD+=("--station-id=$FEED_ID" "--output" "decoded:json:zmq:mode=server,endpoint=tcp://0.0.0.0:5555")

if [[ -n "$ZMQ_MODE" ]]; then
  if [[ -n "$ZMQ_ENDPOINT" ]]; then
    VDLM_CMD+=("--output" "decoded:json:zmq:mode=${ZMQ_MODE,,},endpoint=${ZMQ_ENDPOINT}")
  fi
fi

if [[ -n "$STATSD_SERVER" ]]; then
  VDLM_CMD+=("--statsd" "$STATSD_SERVER")
fi

# corporatetraveldc 2026-10-03: this file is the upstream image script
# (ghcr.io/sdr-enthusiasts/docker-dumpvdl2) bind-mounted over
# /etc/s6-overlay/scripts/dumpvdl2 with ONE addition -- a direct feed to
# airframes.io from dumpvdl2 itself, no acars_router proxy in between.
# The station page says "TCP 5555", but dumpvdl2 2.7.0 has no TCP output
# transport -- it exits with "Output type 'tcp' is unknown" (confirmed
# live 2026-10-03 21:57 ET; udp/zmq/file are the only transports) -- so
# this is the documented dumpvdl2 direct path, UDP, to the port the page
# gives. AIRFRAMES_DIRECT=false disables it.
if [[ "${AIRFRAMES_DIRECT:-true}" =~ ^[Tt] ]]; then
  VDLM_CMD+=("--output" "decoded:json:udp:address=${AIRFRAMES_HOST:-feed.airframes.io},port=${AIRFRAMES_PORT:-5555}")
fi

# shellcheck disable=SC2206
VDLM_CMD+=($FREQ_STRING)

# shellcheck disable=SC2154
"${s6wrap[@]}" echo "Starting: '$VDLM_BIN" "${VDLM_CMD[*]}'"

if chk_enabled "${QUIET_LOGS}"; then
# shellcheck disable=SC2016
  FILTER_TERMS+=("-e" "\[dumpvdl2\] dumpvdl2 ")
  FILTER_TERMS+=("-e" "Sampling rate set")
  FILTER_TERMS+=("-e" "Found [0-9] device")
  FILTER_TERMS+=("-e" "\[dumpvdl2\]   [0-9]:")
  FILTER_TERMS+=("-e" "\[dumpvdl2\] $")
  FILTER_TERMS+=("-e" "Using device [0-9]:")
  FILTER_TERMS+=("-e" "Found .* tuner")
  FILTER_TERMS+=("-e" "Exact sample rate is:")
  FILTER_TERMS+=("-e" "PLL not locked")
  FILTER_TERMS+=("-e" "Center frequency set")
  FILTER_TERMS+=("-e" "Bandwidth set")
  FILTER_TERMS+=("-e" "Device #[0-9]: gain set to")
  FILTER_TERMS+=("-e" "Device [0-9] bias")
  FILTER_TERMS+=("-e" "Device [0-9] started")
  exec "${s6wrap[@]}" "$VDLM_BIN" "${VDLM_CMD[@]}" > >(grep --line-buffered -v "${FILTER_TERMS[@]}")
else
  # shellcheck disable=SC2016
  exec "${s6wrap[@]}" "$VDLM_BIN" "${VDLM_CMD[@]}"
fi

# if we've ended up here there is a problem!
"${s6wrap[@]}" echo "Exiting with error"
sleep 5
exit 1
