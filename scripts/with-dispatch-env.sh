#!/usr/bin/env bash
# scripts/with-dispatch-env.sh -- run a command with dispatch.env +
# dispatch-secrets.env loaded VERBATIM, the way podman --env-file does.
#
#   scripts/with-dispatch-env.sh python3 - <<'EOF' ... EOF
#   scripts/with-dispatch-env.sh python3 src/second_brain/remember.py --stdin ...
#
# Why this exists (2026-10-03): `set -a; . /etc/corporatetraveldc/
# dispatch-secrets.env; set +a` is NOT a safe way to load these files.
# The values are deliberately unquoted (see dispatch-secrets.env.template
# and scripts/check-env-quoting.sh -- podman's --env-file keeps quotes as
# literal characters, so quoting is never allowed), and bash word-splits
# an unquoted `KEY=a b` into an assignment plus a COMMAND named `b`. For a
# secret containing a space that means bash prints
# "bash: <fragment-of-the-secret>: command not found" to stderr -- which
# is exactly how a 9-character fragment of a live credential ended up in
# an agent transcript today. Same hazard for any value with ; & | < > ( )
# ` $ or backslash.
#
# The parsing lives in scripts/lib/with_dispatch_env.py (a file, not a
# heredoc, so the wrapped command keeps its own stdin). It reads every
# line literally after the first "=", skips blank/comment lines, and
# execs the command with that environment; later files override earlier
# ones, matching Quadlet's EnvironmentFile= order. PYTHONPATH=src is added
# when run from the repo. No value is ever echoed or logged.
# Override the file list with WITH_DISPATCH_ENV_FILES="a.env:b.env".
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/lib/with_dispatch_env.py" "$@"
