#!/usr/bin/env bash
# Compatibility entry point for the existing daily timer.
exec bash "$(dirname "$0")/platform-drift-daily.sh" "$@"
