#!/usr/bin/env bash
# Compatibility entry point; repo CLAUDE.md is retired.
exec bash "$(dirname "$0")/check-platform-drift.sh" "$@"
