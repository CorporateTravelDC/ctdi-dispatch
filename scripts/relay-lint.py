#!/usr/bin/env python3
"""scripts/relay-lint.py -- lint an operator relay BEFORE it is posted (2026-10-15).

Standing rule (operator, 2026-10-15): any bug that bites once becomes a check
here (or a banned construct) for every relay going forward. Each rule names the
incident that earned it (casebook G22 / D13 / P35).

A relay is one `bash <<'RELAY' ... RELAY` block run from the operator's phone
under `set -euo pipefail`; its STDIN IS THE RELAY TEXT.

  relay-lint.py FILE        (or - for stdin)
  exit 0 clean, 1 findings (each: line, rule, why, fix)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
USER_UNIT_DIRS = [REPO / ".config/systemd/user", REPO / ".config/containers/systemd"]


def user_units() -> set[str]:
    names = set()
    for d in USER_UNIT_DIRS:
        if d.is_dir():
            for p in d.iterdir():
                names.add(p.stem if p.suffix in (".container", ".service", ".timer", ".path", ".slice") else p.name)
    return names


def _code(line: str) -> str:
    """The line without a trailing comment and without quoted strings (rough, shell-ish)."""
    s = re.sub(r"'[^']*'", "''", line)
    s = re.sub(r'"(?:\\.|[^"\\])*"', '""', s)
    return s.split(" #", 1)[0]


RULES = []


def rule(fn):
    RULES.append(fn)
    return fn


@rule
def early_closing_pipe(n, line, code, ctx):
    # 2026-10-14 23:52Z: `systemctl list-timers ... | head -2` -> SIGPIPE, rc=141 under pipefail,
    # killed the resolver relay after its commit (P35).
    if re.search(r"\|\s*(head\b|grep\s+-[A-Za-z]*[qm]\b|grep\s+--quiet|grep\s+--max-count)", code) \
            and "|| true" not in code:
        return ("early-closing pipe under pipefail (SIGPIPE, rc=141)",
                "use `sed -n '1,Np'` (reads everything) or end the display line with `|| true`")


@rule
def stdin_reader(n, line, code, ctx):
    # 2026-10-15: `read -rs -p ... HCURL` inside bash <<'RELAY' read the relay's NEXT LINE as the URL,
    # skipping the validation line; the secret file got relay text and the dead-man check went down.
    if re.search(r"(^|[;&|]\s*|\s)read\s", code) and "</dev/tty" not in line:
        return ("`read` inside a relay reads the relay text (stdin is the heredoc)",
                "read from the terminal: `read -rs -p '...' VAR </dev/tty`")
    if re.search(r"\s-F\s+-(\s|$)|\bcat\s+-(\s|$)|\bpython3?\s+-(\s|$)", code) \
            and "<<" not in line and "|" not in code.split("-F", 1)[0] and "|" not in code.split("python", 1)[0]:
        return ("command reads stdin inside a relay (it would consume the relay text)",
                "give it its own heredoc (`<<'X'`), a pipe, or a file")


@rule
def sudo_on_user_unit(n, line, code, ctx):
    # 2026-10-15: `sudo systemctl disable --now corporatetraveldc-net-failover-watchdog.timer` -- a USER
    # unit; "Unit ... does not exist". Rule: only Tailscale, Pi-hole, Unbound and the watchdog are system-level.
    m = re.search(r"\bsudo\s+systemctl\b(?!\s+--user)[^\n]*", code)
    if m:
        for u in re.findall(r"[A-Za-z0-9@._-]+\.(?:service|timer|path|slice)|[A-Za-z0-9@_-]+(?=\s|$)", m.group(0)):
            base = u.rsplit(".", 1)[0] if "." in u else u
            if base in ctx["user_units"]:
                return (f"`sudo systemctl` on user unit {u}",
                        "user units: `systemctl --user ...` as corporatetraveldc (no sudo)")


@rule
def secret_on_argv(n, line, code, ctx):
    # 2026-10-05 argv-token sweep: /proc/<pid>/cmdline is world-readable here.
    if re.search(r"Authorization:\s*Bearer\s+\$", line) and "@/dev/fd" not in line:
        return ("bearer token on a command line (world-readable in /proc)",
                "pass headers via a private fd (`-H @/dev/fd/N`) or a curl config on stdin")
    if re.search(r"--password[= ]\$|--passphrase[= ]\$|https://hc-ping\.com/[0-9a-f]{8}-", line):
        return ("secret value or secret URL in the relay text", "read it with `read -rs ... </dev/tty` into a 0600 file")


@rule
def git_add_intent_to_add(n, line, code, ctx):
    # 2026-10-14: `git add -N -A` + `git diff` silently dropped staged deletions (a rename was lost).
    if re.search(r"\bgit\b[^\n]*\badd\s+(-[A-Za-z]*N|--intent-to-add)", code):
        return ("`git add -N` + diff drops deletions", "stage with `git add -A`, then `git diff --cached --binary`")


@rule
def banned(n, line, code, ctx):
    # standing rules: never kill screen sessions (2026-10-08); no passphrase-caching agents (2026-10-08).
    if re.search(r"\bscreen\s+-(X|S\s+\S+\s+-X)\s+quit\b|\bpkill\b[^\n]*\bscreen\b|\bkillall\s+screen\b", code):
        return ("kills a screen session (the operator's terminal may be inside it)", "stop the process, not the session")
    if re.search(r"\bssh-add\b|\beval\s+\"?\$\(ssh-agent", code):
        return ("ssh-agent / ssh-add (no passphrase-caching agents, ever)", "remove it; passphrases are typed per step")
    if re.search(r"\bsed\s+-i\b[^\n]*/etc/corporatetraveldc", code):
        return ("`sed -i` in /etc/corporatetraveldc", "edit with a reviewed python step that keeps a backup")
    if re.search(r"\bsource\s+/etc/corporatetraveldc/|\.\s+/etc/corporatetraveldc/", code):
        return ("sources an /etc/corporatetraveldc env file (podman env-file, not bash-safe; leaks values)",
                "read one key with grep, or use scripts/with-dispatch-env.sh")


TWIN_INSTALL = re.compile(r"(install\s[^\n]*\.config/(containers/)?systemd/|skills-sync\.sh\s+apply|\.git/hooks/)")


def twin_after_sign(lines: list[str]) -> list[tuple[int, str, str]]:
    """2026-10-15: a relay that changes a tracked live twin (quadlet, user unit, git hook, skill) must
    install it BEFORE sign-manifest: the signing step's drift check compares live against tracked, so
    an install after signing fails the sign (caught in review of the ADSBHub relay)."""
    sign = next((n for n, l in enumerate(lines, 1) if "sign-manifest" in l and not l.lstrip().startswith("#")), None)
    if sign is None:
        return []
    return [(n, "live-twin install after sign-manifest (the sign's drift check will fail)",
             "move the install (and daemon-reload) above the sign step")
            for n, l in enumerate(lines, 1) if n > sign and TWIN_INSTALL.search(l)]


UNIT_INSTALL = re.compile(r"install\s[^\n]*\.config/(containers/)?systemd/[^\n]*\.(service|timer|container|path)\b")
REBASELINE = re.compile(r"REBASELINE=1\s[^\n]*stack-refresh\.sh\s[^\n]*--tripwire")


def unit_install_without_rebaseline(lines: list[str]) -> list[tuple[int, str, str]]:
    """2026-10-16: the mid-week stack tripwire fingerprints the user unit set; every relay that
    installed a new unit since 10-12 changed it, and the tripwire failed on 10-16 07:21Z, which then
    blocked the next relay at the drift gate. A relay that installs a unit must re-record the audit
    baselines (REBASELINE=1 scripts/stack-refresh.sh --tripwire --dry-run --now) after the commit."""
    installs = [n for n, l in enumerate(lines, 1) if UNIT_INSTALL.search(l) and not l.lstrip().startswith("#")]
    if installs and not any(REBASELINE.search(l) for l in lines):
        return [(installs[0], "installs a systemd unit without re-recording the stack-refresh audit baselines",
                 "add after the commit: REBASELINE=1 bash scripts/stack-refresh.sh --tripwire --dry-run --now")]
    return []


DEMO_QUADLETS = ("corporatetraveldc-demo-api", "corporatetraveldc-runner-demo")
DEMO_QUADLET_FORBIDDEN = ("corporatetraveldc-pgsql-sock", "/etc/corporatetraveldc/dispatch.env",
                          "dispatch-secrets.env", "/etc/corporatetraveldc/svc/")
QUADLET_DIR = REPO / ".config/containers/systemd"
DEMO_QUADLET_RE = re.compile(r"corporatetraveldc-(demo-api|runner-demo)\.container")


def demo_quadlet_isolation(lines: list[str]) -> list[tuple[int, str, str]]:
    """2026-10-16 (demo/production database split; the regression found that day: demo-api and
    runner-demo had been given the production Postgres socket and dispatch.env on 2026-09-20). A relay
    line that installs a demo quadlet must not be installing one that carries the production socket
    volume, dispatch.env, dispatch-secrets.env or the scoped svc/ files -- checked against the TRACKED
    quadlet the line installs -- and no relay line may append such a line to a demo quadlet."""
    out = []
    for n, l in enumerate(lines, 1):
        if l.lstrip().startswith("#"):
            continue
        m = DEMO_QUADLET_RE.search(l)
        if not m:
            continue
        hit = next((f for f in DEMO_QUADLET_FORBIDDEN if f in l), None) \
            or (re.search(r"pgsql-sock|dispatch\.env|dispatch-secrets|/svc/", l.replace("\\/", "/")) or [None])[0]
        if hit:
            out.append((n, f"relay line gives demo quadlet {m.group(0)} production access ({hit})",
                        "the public demo never mounts the production socket or loads dispatch.env / secrets"))
            continue
        if re.search(r"\binstall\b", l):
            q = QUADLET_DIR / m.group(0)
            if q.is_file():
                active = [x for x in q.read_text().splitlines() if x.strip() and not x.lstrip().startswith("#")]
                bad = [f for f in DEMO_QUADLET_FORBIDDEN if any(f in x for x in active)
                       and not (f == "/etc/corporatetraveldc/dispatch.env" and not any("DISPATCH_ROLE=demo" in x for x in active))]
                if bad:
                    out.append((n, f"installs demo quadlet {m.group(0)} which carries production access ({', '.join(bad)})",
                                "remove the production socket volume / env files from the tracked quadlet first"))
    return out


@rule
def podman_reads_stdin(n, line, code, ctx):
    # 2026-10-17: `podman healthcheck run ... >/dev/null` inside bash <<'RELAY' read the rest of the relay
    # from stdin; bash hit EOF, exited 0, and the trap reported "finished OK" with steps 3-5 never run.
    if re.search(r"\bpodman\s+(exec|run|healthcheck\s+run|start\s+-a|attach)\b", code) \
            and "</dev/null" not in line and "<<" not in line and "|" not in code.split("podman", 1)[0]:
        return ("podman reads stdin inside a relay (it would consume the relay text)",
                "end the command with `</dev/null`")


HEAVY = re.compile(r"\bpytest\b|serialized-rollout\.sh|scrub-demo-source\.py\s[^\n]*--(backfill|refresh)|\bsystemd-run\b|relay-preflight\.sh")


def heavy_without_load_wait(lines: list[str]) -> list[tuple[int, str, str]]:
    # 2026-10-17/18: relay work stacked on a 20-30 load baseline tripped the LOCKDOWN (load1 >= 40)
    # twice; operator: "make the relays load aware". Every heavy step waits for headroom first.
    out = []
    prev = ""          # last code line of the previous command
    before = ""        # the line before the current command's first line
    cont = False
    for n, l in enumerate(lines, 1):
        code = _code(l)
        if not cont:
            before = prev
        if HEAVY.search(code) and "relay-wait-load" not in code and "relay-wait-load" not in before:
            out.append((n, "heavy step without a load wait (stacked load trips the LOCKDOWN at load1 >= 40)",
                        "put `scripts/relay-wait-load.sh` on the line before it"))
        if code.strip():
            cont = code.rstrip().endswith("\\")
            if not cont:
                prev = code
    return out


def completion_sentinel(lines: list[str]) -> list[tuple[int, str, str]]:
    # 2026-10-17: a relay whose stdin was consumed ends at EOF with rc=0; the trap said OK. The trap may
    # report success only when the relay's last line ran.
    body = [l for l in lines if l.strip()]
    if len(body) < 3:
        return []
    out = []
    if body[-1].strip() == "RELAY" and body[-2].strip() != "RELAY_DONE=1":
        out.append((len(lines), "relay must end with `RELAY_DONE=1` (stdin eaten -> EOF -> rc 0 looks like success)",
                    "add `RELAY_DONE=1` as the last line before RELAY"))
    for n, l in enumerate(lines, 1):
        if l.lstrip().startswith("trap ") and "OK" in l and "RELAY_DONE" not in l:
            out.append((n, "trap reports OK without checking RELAY_DONE",
                        "report OK only when rc==0 AND RELAY_DONE==1"))
    return out


def lint(text: str) -> list[tuple[int, str, str]]:
    out = []
    lines = text.splitlines()
    out += twin_after_sign(lines)
    out += unit_install_without_rebaseline(lines)
    out += demo_quadlet_isolation(lines)
    out += completion_sentinel(lines)
    out += heavy_without_load_wait(lines)
    first = next((l for l in lines if l.strip()), "")
    if not re.match(r"\s*bash\s+<<'RELAY'\s*$", first):
        out.append((1, "relay must start with `bash <<'RELAY'` (quoted delimiter: nothing expands at paste)",
                    "wrap it: bash <<'RELAY' ... RELAY"))
    if not any(re.match(r"\s*set -euo pipefail\s*$", l) for l in lines[:4]):
        out.append((2, "relay must `set -euo pipefail` in its first lines", "add it right after the opening line"))
    ctx = {"user_units": user_units()}
    nested = None
    for n, line in enumerate(lines, 1):
        if nested:                                   # inside a nested heredoc body (python, commit message)
            if line.strip() == nested:
                nested = None
            continue
        m = re.search(r"<<-?\s*'?([A-Za-z_]+)'?", line)
        if m and n > 1:
            if not re.search(r"<<-?\s*'", line):
                out.append((n, f"unquoted nested heredoc <<{m.group(1)} (expands $vars at run time)",
                            f"quote it: <<'{m.group(1)}'"))
            nested = m.group(1)
        code = _code(line)
        for r in RULES:
            hit = r(n, line, code, ctx)
            if hit:
                out.append((n, hit[0], hit[1]))
    return out


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = sys.stdin.read() if not args or args[0] == "-" else Path(args[0]).read_text()
    if "```" in text:                                # a relay copied from chat, fences included
        blocks = re.findall(r"```(?:bash)?\n(.*?)```", text, re.S)
        text = blocks[0] if blocks else text
    found = lint(text)
    for n, why, fix in found:
        print(f"line {n}: {why}\n    fix: {fix}")
    print(f"relay-lint: {'clean' if not found else f'{len(found)} finding(s)'}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
