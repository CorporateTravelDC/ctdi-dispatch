#!/usr/bin/env python3
"""PreToolUse hook (operator 2026-10-26): no Claude process -- the main session, any subagent
or workflow agent -- may WRITE an aircraft registration. 2026-10-26: sweep agents pasted live
query rows (11 LADD-listed N-numbers) into their registers and returned results despite the
prompt rule; a rule the model can lose in a long run is not a control. Installed root-owned
under /usr/local/libexec/ctdc and wired from /etc/claude-code/managed-settings.json for
Write, Edit, MultiEdit, NotebookEdit and StructuredOutput. Exit 2 blocks the call.

What it refuses: a US N-number shape (the scrub's rule, 4+ characters) anywhere in the content
written. Every registration counts, listed or not: agents cannot see the LADD list,
so they must not write any. Examples use Q marks (scripts/demo-env, the scrub). Exempt: test
fixtures and the leak-guard sources, whose job is to name the shapes. Fail closed on input
it cannot read.
"""
import json
import re
import sys

# The public scrub's own rule (scripts/scrub-public-tree.py N_REGISTRATION_RE, at least 4 characters), so the
# two can never disagree; N0... never exists and stays free for illustrations.
N_NUMBER = re.compile(rb"(?<![A-Za-z0-9_-])N[1-9][0-9]{0,4}[A-Z]{0,2}(?![A-Za-z0-9_])")
EXEMPT = re.compile(r"(^|/)(tests?/|fixtures?/|scripts/leak-guard/)")


def main() -> int:
    try:
        data = json.load(sys.stdin)
        tool_input = data.get("tool_input") or {}
    except Exception:
        print("ctdc no-registration hook: unreadable tool input -- blocked (fail closed)", file=sys.stderr)
        return 2
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if path and EXEMPT.search(path):
        return 0
    blob = json.dumps(tool_input, ensure_ascii=False).encode()
    if any(len(m.group(0)) >= 4 for m in N_NUMBER.finditer(blob)):
        print("Blocked by operator policy (managed): the content contains an aircraft registration (N-number). "
              "Never write a tail, hex or registration -- cite the command and a COUNT, or use a Q mark.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
