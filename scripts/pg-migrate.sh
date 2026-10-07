#!/usr/bin/env bash
# Thin wrapper around scripts/pg_migrate.py -- loads the same env files the
# app itself reads (so DISPATCH_PG_* / DISPATCH_PG_PASSWORD are set without
# having to re-export them by hand) and then just execs the Python runner
# with whatever args were passed through.
#
# Usage: scripts/pg-migrate.sh [--database DB] [--schema SCHEMA] [--dry-run] [--status]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 2026-10-03: was `set -a; source <(grep ... "$f")`. Shell-sourcing these
# files is unsafe: values are deliberately unquoted (podman --env-file
# semantics, see scripts/check-env-quoting.sh) and bash word-splits /
# interprets metacharacters in them, so a value like `KEY=ab;cd` runs `cd`
# as a command and prints a fragment of the secret to stderr. Load them
# verbatim through the shared loader instead; it also sets PYTHONPATH=src.
exec "$REPO_ROOT/scripts/with-dispatch-env.sh" python3 "$REPO_ROOT/scripts/pg_migrate.py" "$@"
