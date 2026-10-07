#!/usr/bin/env python3
"""Exec a command with dispatch env files loaded VERBATIM (podman --env-file
semantics). Companion to scripts/with-dispatch-env.sh -- see that file's
header for why shell-sourcing these files is unsafe (unquoted values with
spaces word-split and a fragment of a secret gets executed as a command).

Parsing rules, matching podman's --env-file:
  * blank lines and lines whose first non-space char is '#' are skipped
  * everything after the first '=' is the value, literally -- no quote
    stripping, no $expansion, no backslash handling, no trimming
  * a leading 'export ' on the key is tolerated
  * later files override earlier ones
stdin/stdout/stderr pass straight through to the command; no value is
ever printed by this loader.
"""
import os
import sys

DEFAULT_FILES = [
    "/etc/corporatetraveldc/dispatch.env",
    "/etc/corporatetraveldc/dispatch-secrets.env",
]


def load(paths, env):
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.rstrip("\r\n")
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                if not key or any(c.isspace() for c in key):
                    continue
                env[key] = value
    return env


def main(argv):
    if not argv:
        print("usage: with_dispatch_env.py <command> [args...]", file=sys.stderr)
        return 64
    files = os.environ.get("WITH_DISPATCH_ENV_FILES")
    if files:
        files = files.split(":")
    elif all(os.access(p, os.R_OK) for p in DEFAULT_FILES):
        files = DEFAULT_FILES
    else:
        # 2026-10-04 16:51: team accounts (ctdc-agents / ctdc-ops) cannot read the
        # production files by design; a unit-started session has no login-profile
        # export, so pick the account's own subset automatically.
        subset = [p for p in ("/etc/ctdc-agent/agent-secrets.env", "/etc/ctdc-ops/ops-secrets.env") if os.access(p, os.R_OK)]
        if not subset:
            print("with-dispatch-env: cannot read the production env files and no team subset is readable", file=sys.stderr)
            return 66
        files = subset
    for p in files:
        if not os.access(p, os.R_OK):
            print(f"with-dispatch-env: cannot read {p}", file=sys.stderr)
            return 66
    env = load(files, dict(os.environ))
    # Make src/ importable for second_brain / common when run from the repo.
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = os.path.join(repo, "src")
    if os.path.isdir(src):
        env["PYTHONPATH"] = src + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.execvpe(argv[0], argv, env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
