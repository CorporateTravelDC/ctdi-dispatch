#!/usr/bin/env bash
# Thin wrapper around scripts/pg_migrate.py -- loads the same env files the
# app itself reads (so DISPATCH_PG_* / DISPATCH_PG_PASSWORD are set without
# having to re-export them by hand) and then just execs the Python runner
# with whatever args were passed through.
#
# Usage: scripts/pg-migrate.sh [--database DB] [--schema SCHEMA] [--dry-run] [--status]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for f in /etc/corporatetraveldc/dispatch.env /etc/corporatetraveldc/dispatch-secrets.env; do
    if [ -r "$f" ]; then
        set -a
        # shellcheck disable=SC1090
        source <(grep -v '^\s*#' "$f" | grep '=')
        set +a
    fi
done

exec python3 "$REPO_ROOT/scripts/pg_migrate.py" "$@"
