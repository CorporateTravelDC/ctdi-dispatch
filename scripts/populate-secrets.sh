#!/bin/bash
# populate-secrets.sh
# Reads ~/.secrets/{name}.{token,key} files and writes values into
# /etc/corporatetraveldc/dispatch-secrets.env.
#
# Convention (enforced):
#   {service}.token  = feeder sharing/auth key for that service's feeder daemon
#   {service}.key    = REST API key for that same service
# Run as corporatetraveldc after adding or rotating any credential.
# Safe to re-run -- overwrites only the keys it knows about.
#
# ~~read_secret/set_env in shell: `sudo sed -i "s|^K=.*|K=<value>|"` and
# `echo K=<value> | sudo tee -a`~~ (rewritten 2026-10-19, operator, Gate-1b:
# the value sat on a command line, visible in the process table, and sed -i ran
# under sudo on /etc/corporatetraveldc). The work is now done in
# scripts/lib/populate_secrets.py: values are read inside Python from the files,
# never on a command line, never printed; no sudo, no sed; a temp file + rename
# when the directory is writable, otherwise the file is rewritten in place (the
# directory is root:corporatetraveldc 0750, the file corporatetraveldc 0600);
# a value with a control or escape character is refused, naming the file.
# Both AeroAPI names (FLIGHTAWARE_API_KEY, FLIGHTAWARE_AEROAPI_KEY) come from
# flightaware.key; both Jumpseat names (JUMPSEAT_API_KEY,
# ACARSDRAMA_JUMPSEAT_TOKEN) from jumpseat.token.
#
# Usage:
#   bash populate-secrets.sh [--dry-run] [--target FILE]
#   (--dry-run prints names and lengths only)
set -euo pipefail
exec python3 "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/populate_secrets.py" "$@"
