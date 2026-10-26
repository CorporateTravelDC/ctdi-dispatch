#!/usr/bin/env python3
"""PreToolUse hook (operator 2026-10-25): Claude -- the main session AND every
subagent/workflow agent -- may never use the SSH agent, the GPG agent, the
operator's keys, or sign/commit/push. Installed root-owned under
/usr/local/libexec/ctdc and wired from /etc/claude-code/managed-settings.json,
which Claude cannot edit. Exit 2 blocks the tool call; stderr is the reason
Claude sees. Fail closed: unparseable input is blocked too."""
import json
import re
import sys

RULES = [
    (r"\bssh-add\b|\bssh-agent\b", "SSH agent"),
    (r"\bgpg-agent\b|\bgpg-connect-agent\b|\bgpgconf\b|S\.gpg-agent", "GPG agent"),
    (r"\bSSH_AUTH_SOCK\b|\bGNUPGHOME\b|\bGPG_AGENT_INFO\b|\bIdentityAgent\b|\bGIT_SSH_COMMAND\b|\bGIT_SSH\b",
     "agent/key environment override"),
    (r"(^|[\s;&|(/~])\.ssh/|(^|[\s;&|(/~])\.gnupg/|/run/user/\d+/gnupg", "operator key material"),
    (r"\bgpg2?\b[^\n|;&]*\s(--sign|-s|--clear-?sign|--detach-sign|-b|--export-secret-keys|--edit-key)\b", "GPG signing / secret keys"),
    (r"\bgit\b(\s+-[cC]\s+\S+)*\s+(commit|push|tag|merge|rebase|am|cherry-pick|revert)\b", "git commit/push (operator-only)"),
    (r"\bgh\s+(pr\s+merge|repo\s+sync|release\s+create)\b", "remote write (operator-only)"),
    (r"sign-manifest\.sh|push-public\.sh|install-root-copies", "signing / publishing script (operator-only)"),
    (r"(^|[\s;&|(])(ssh|scp|sftp)\s", "outbound SSH"),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
    except Exception:
        print("ctdc no-agent hook: unreadable tool input -- blocked (fail closed)", file=sys.stderr)
        return 2
    for pattern, what in RULES:
        if re.search(pattern, cmd):
            print(f"Blocked by operator policy (managed): Claude may not touch {what}. "
                  "Signing, commits, pushes and key/agent use are the operator's only -- hand the command back instead.",
                  file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
