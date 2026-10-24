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
def sqlite_double_quoted_literal(n, line, code, ctx):
    # 2026-10-20: the Pi-hole read relay ran `pihole-FTL sqlite3 ... "... strftime(\\"%s\\",\\"now\\") ..."`:
    # SQLite reads a double-quoted token as a COLUMN name ("no such column: %s"), so two counts failed.
    if re.search(r"\bsqlite3\b", line) and re.search(r'\\"[^"\\]*\\"', line):
        return ("double-quoted text inside SQL passed to sqlite3 (SQLite reads it as a column name)",
                "use single-quoted SQL string literals, e.g. strftime('%s','now','-1 day'), with the SQL in its own heredoc")


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


@rule
def sudo_reads_stdin(n, line, code, ctx):
    # 2026-10-23 (operator): stdin_reader / podman_reads_stdin never covered sudo. On 2026-10-22 a
    # `sudo bash -c '...'` without </dev/null read the rest of the relay script as its stdin; the
    # outer bash then ran leftover fragments (`touch '/public.'`). Every sudo line inside a relay
    # redirects stdin -- unless sudo is fed by a pipe/heredoc, or only validates/drops the ticket
    # (-v / -k / -K run no command).
    if line.lstrip().startswith("#"):
        return None
    # a `$(sudo ...)` inside a double-quoted string runs too (_code blanks quoted text)
    m = re.search(r"(?:^|[;&|(!{]\s*|\s)sudo\b", code) or re.search(r"\$\(\s*sudo\b", line)
    if not m or re.search(r"\bsudo\s+-[vkK]\b", code):
        return None
    if "</dev/null" in line or "<<" in line or re.search(r"\|\s*sudo\b", code):
        return None
    return ("sudo reads stdin inside a relay (2026-10-22: it ate the rest of the relay script)",
            "end the sudo command with `</dev/null` (e.g. sudo bash -c '...' </dev/null)")


@rule
def on_calendar_hardcoded_date(n, line, code, ctx):
    # 2026-10-23 (operator): 2026-10-22 a relay passed `--on-calendar "2026-10-22 23:50 ..."`; by the
    # time it ran the date had passed midnight UTC, so the timer was scheduled in the past and never
    # fired. Compute the date at run time instead.
    if line.lstrip().startswith("#"):
        return None
    if re.search(r"(--on-calendar[=\s]+|OnCalendar=)[\"']?\d{4}-\d{2}-\d{2}", line):
        return ("hard-coded date in --on-calendar / OnCalendar= (a relay run after midnight schedules it in the past)",
                "compute it at run time, e.g. --on-calendar \"$(TZ=America/New_York date -d '23:50' '+%Y-%m-%d %H:%M:00') "
                "America/New_York\" (and check the result is in the future)")
    return None


# 2026-10-18: pg-maintenance.sh's CONCURRENTLY builds read whole tables (~5 GB for flight_events);
# its read-only `status` is not heavy.
# 2026-10-20 (cleanup sweep): the transcript leak scan (scan | redact reads every transcript on the
# box), sign-manifest / verify-manifest (hash the whole tree), scrub-public-tree (rewrites and
# re-verifies the whole tree), render-glossary / render-onboarding (render every doc / pamphlet) and
# demo-sqlite-retire-check (hashes the SQLite files) are heavy too.
# 2026-10-20 (phase D): a gate backfill run (gate-backfill-tdes / -airframes) is heavy; its
# --dry-run / --g-prefix-report is not.
HEAVY = re.compile(r"\bpytest\b|serialized-rollout\.sh|scrub-demo-source\.py\s[^\n]*--(backfill|refresh)|\bsystemd-run\b|relay-preflight\.sh"
                   r"|pg-maintenance\.sh\s+(create-indexes-concurrently|reindex-)"
                   r"|transcript_leak_scan\.py\s+(scan|redact)\b|sign-manifest\.sh|verify-manifest\.sh|scrub-public-tree\.py"
                   r"|render-glossary\.py|render-onboarding\.sh|demo-sqlite-retire-check\.py"
                   r"|gate-backfill-tdes\.py(?![^\n]*--(dry-run|g-prefix-report))|gate-backfill-airframes\.py(?![^\n]*--dry-run)")


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


LEAK_SCAN_MENTION = re.compile(r"leak-guard|(?i:leak[ _-]?scan)")
PUSH_LINE = re.compile(r"\bgit\b[^\n]*\spush\b|push-public\.sh")
REDACT_LINE = re.compile(r"transcript_leak_scan\.py\s+redact\b")


def leak_scan_redact_before_push(lines: list[str]) -> list[tuple[int, str, str]]:
    """2026-10-19 (operator, Gate-1b): a relay that changes the leak-guard patterns (it mentions
    scripts/leak-guard/, or its commit message mentions the leak scan) must redact the on-box
    transcripts with the NEW patterns before anything leaves the box: the first push would
    otherwise go out with the old patterns' blind spot still in the transcripts. Text-level: a
    `git push` (or push-public.sh) line before any `transcript_leak_scan.py redact` line."""
    body = [(n, l) for n, l in enumerate(lines, 1) if not l.lstrip().startswith("#")]
    if not any(LEAK_SCAN_MENTION.search(l) for _n, l in body):
        return []
    push = next((n for n, l in body if PUSH_LINE.search(_code(l))), None)
    if push is None:
        return []
    redact = next((n for n, l in body if REDACT_LINE.search(l)), None)
    if redact is None or redact > push:
        return [(push, "push before `transcript_leak_scan.py redact` in a relay that changes the leak scan",
                 "run `python3 scripts/leak-guard/transcript_leak_scan.py redact` (then scan) before the first push")]
    return []


SECRETISH_VAR = re.compile(r"(?i)(key|token|secret|pass|pat\b|pw|cred|url|auth)")
_READ_ARG_FLAGS = set("ptudnNai")


def _read_vars(line: str) -> tuple[str, list[str]] | None:
    """(flags, variable names) of a `read` command on this line, or None."""
    import shlex
    m = re.search(r"(?:^|[;&|(]\s*|\s)read\s", line)
    if not m:
        return None
    rest = line[m.end():]
    try:
        lex = shlex.shlex(rest, posix=True, punctuation_chars=";&|<>()")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return None
    flags, names, i = "", [], 0
    while i < len(toks):
        t = toks[i]
        if t and t[0] in ";&|<>()":
            break
        if t.startswith("-") and len(t) > 1 and not names:
            flags += t[1:]
            if t[-1] in _READ_ARG_FLAGS:
                i += 1                                # the flag's argument (-p prompt, -t 5 ...)
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
            names.append(t)
        i += 1
    return flags, names


def secret_read_without_control_check(lines: list[str]) -> list[tuple[int, str, str]]:
    """2026-10-19 (operator, Gate-1b): a secret pasted at a `read -rs` / `read -r` hand step can carry an
    ESC sequence, a CR or a TAB into the file it is written to. The value must pass a control-character
    check before its first use: `secret_check VAR` (scripts/lib/secret_input.sh), an explicit
    `[[:cntrl:]]` test on it, or read it with `secret_read VAR` from the same helper instead."""
    out = []
    for n, l in enumerate(lines, 1):
        if l.lstrip().startswith("#"):
            continue
        rv = _read_vars(l)
        if not rv:
            continue
        flags, names = rv
        if "r" not in flags:
            continue
        for v in names:
            if not ("s" in flags or SECRETISH_VAR.search(v)):
                continue
            use = re.compile(r"\$\{?" + re.escape(v) + r"\b")
            checked = False
            same = l[l.find("read"):]
            tail = same.split(v, 1)[1] if v in same else ""
            for k, nxt in [(n, tail)] + list(enumerate(lines[n:], n + 1)):
                if k != n and nxt.lstrip().startswith("#"):
                    continue
                if re.search(r"\bsecret_check\s+" + re.escape(v) + r"\b", nxt) or \
                        ("[[:cntrl:]]" in nxt and (use.search(nxt) or v in nxt)):
                    checked = True
                    break
                if use.search(nxt):
                    break
            if not checked and any(use.search(x) for x in lines[n - 1:]):
                out.append((n, f"secret read into {v} is used without a control-character check",
                            f"`source scripts/lib/secret_input.sh` and run `secret_check {v}` right after the read "
                            f"(or use `secret_read {v} 'Prompt: '`)"))
    return out


PODMAN_BUILD = re.compile(r"\bpodman\s+(image\s+)?build\b")
PROVENANCE_RECORD = re.compile(r"provenance\.py\s+record(?!-)\b")
BUILD_TAG = re.compile(r"(?:\s-t|\s--tag)[=\s]+(\"[^\"]+\"|'[^']+'|\S+)")


def build_without_provenance(lines: list[str]) -> list[tuple[int, str, str]]:
    """2026-10-22 (operator): on 2026-10-21 a hotfix relay hand-built images with `podman build` and
    wrote no provenance receipts; the integrity sweep then failed every 15 min until receipts were
    recorded. A relay line running `podman build` must be followed (later in the relay) by
    `scripts/build/provenance.py record` for that image (matched by its -t tag when it has one).
    Builds through serialized-rollout.sh / stack-refresh.sh record their own receipts and are not
    bare `podman build` lines, so they never match here."""
    out = []
    body = [(n, l) for n, l in enumerate(lines, 1) if not l.lstrip().startswith("#")]
    for i, (n, l) in enumerate(body):
        code = _code(l)
        if not PODMAN_BUILD.search(code) or re.search(r"serialized-rollout\.sh|stack-refresh\.sh", code):
            continue
        m = BUILD_TAG.search(l)
        tag = m.group(1).strip("'\"") if m else None
        later = [x for _k, x in body[i + 1:] if PROVENANCE_RECORD.search(x)]
        if not any(tag is None or tag in x for x in later):
            what = f" for {tag}" if tag else ""
            out.append((n, f"`podman build` without a later `provenance.py record`{what} (no receipt -> the "
                           "integrity sweep fails every 15 min)",
                        "after the build add: python3 scripts/build/provenance.py record --image REF "
                        "--containerfile F --context DIR (or build via serialized-rollout.sh)"))
    return out


OK_TRAP = re.compile(r"^\s*trap\s[^\n]*RELAY_DONE[^\n]*\bOK\b")
# Narrow on purpose: only the printed check results that have actually been reported as "OK" while
# failing. Add a label here when another one bites.
CHECK_RESULT = re.compile(r"\b(echo|printf)\b[^\n]*(provenance FAILED|integrity sweep:)")
CHECK_GATE = re.compile(r"\bexit\s+[1-9]")


def ok_ping_ungated_check(lines: list[str]) -> list[tuple[int, str, str]]:
    """2026-10-23 (operator): on 2026-10-21 a relay's trap sent "finished OK" while its integrity
    sweep was failing: the relay only PRINTED "integrity sweep: failed" / "provenance FAILED images:
    N" and carried on to RELAY_DONE=1. When a relay has an OK-nudge trap gated on RELAY_DONE, every
    printed check result of the kinds in CHECK_RESULT must fail the relay when it is bad: an `exit N`
    (N>0) on the same line or on one of the next two code lines. Heuristic, text-level, narrow."""
    if not any(OK_TRAP.search(l) for l in lines):
        return []
    out = []
    for n, l in enumerate(lines, 1):
        if l.lstrip().startswith("#") or not CHECK_RESULT.search(l):
            continue
        nxt = [x for x in lines[n:] if x.strip() and not x.lstrip().startswith("#")][:2]
        if not any(CHECK_GATE.search(_code(x)) for x in [l] + nxt):
            out.append((n, "check result printed but not enforced -- the OK nudge fires even when it fails "
                           "(2026-10-21: OK while the integrity sweep failed)",
                        "capture it and fail: v=$(...); echo \"... $v (expect 0)\"; [[ $v == 0 ]] || { echo 'check failed'; exit 1; }"))
    return out


# 2026-10-23 (operator): the poller's WatchlistFileWatcher hot-reloads these from the main checkout
# every ~60 s (src/shared/watchlist.py _FILE_MAP); src/poller/hot_reload_hold.py honours HOLD_SENTINEL.
HOT_RELOAD_FILES = re.compile(r"^watchlists/permanent_(flights|trains|vessels|drones)\.json$")
HOLD_SENTINEL = "hold-hot-reload"
GUARD_APPLY = re.compile(r"(\$\{?G\b|-guard\b)[^\n]*\|\s*git\s+apply\b[^\n]*--index")
HOLD_SET = re.compile(r"\b(touch|install)\b[^\n]*" + HOLD_SENTINEL + r"|>\s*\S*" + HOLD_SENTINEL)
HOLD_RELEASE = re.compile(r"\brm\b[^\n]*" + HOLD_SENTINEL)
ROLLOUT_STEP = re.compile(r"serialized-rollout\.sh|systemctl\s+--user\s+restart\b")


def guard_staged_files() -> list[str]:
    """Paths staged in this script's worktree (the guard, when run as $G/scripts/relay-lint.py from
    relay-preflight.sh) -- the batch a relay's `git apply --index` step will apply."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--name-only"],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    return r.stdout.split() if r.returncode == 0 else []


def hot_reload_without_hold(lines: list[str], staged: list[str] | None) -> list[tuple[int, str, str]]:
    """2026-10-23 (operator): 2026-10-20, a relay applied a batch to the main checkout and the
    poller's 60 s hot-reload made 46 new watches live before migration 0082 was applied. A relay that
    applies the guard's batch (`git -C "$G" diff --cached --binary | git apply --index`) when that
    batch touches a hot-reloaded watchlist file (the guard's staged paths, or the relay text naming
    one) must: touch the hold sentinel (/var/lib/corporatetraveldc/hold-hot-reload) BEFORE the apply,
    and `rm -f` it AFTER its last rollout step (serialized-rollout.sh / systemctl --user restart)."""
    body = [(n, l) for n, l in enumerate(lines, 1) if not l.lstrip().startswith("#")]
    apply = next((n for n, l in body if GUARD_APPLY.search(l)), None)
    if apply is None:
        return []
    files = guard_staged_files() if staged is None else staged
    touches = any(HOT_RELOAD_FILES.match(f) for f in files) or any("watchlists/permanent_" in l for _n, l in body)
    if not touches:
        return []
    out = []
    held = next((n for n, l in body if HOLD_SET.search(l)), None)
    if held is None or held > apply:
        out.append((apply, "batch touches a hot-reloaded watchlist file but the relay does not hold the reload "
                           "before applying it (2026-10-20: 46 watches live before migration 0082)",
                    "before the apply: mkdir -p /var/lib/corporatetraveldc && touch /var/lib/corporatetraveldc/"
                    + HOLD_SENTINEL))
    last_rollout = max((n for n, l in body if ROLLOUT_STEP.search(_code(l))), default=apply)
    released = [n for n, l in body if HOLD_RELEASE.search(l) and n > last_rollout]
    if not released:
        out.append((apply, "hot-reload hold is never released after the rollout (the poller ignores it only "
                           "after 6 h)",
                    "after the last rollout/restart step: rm -f /var/lib/corporatetraveldc/" + HOLD_SENTINEL))
    return out


def lint(text: str, staged: list[str] | None = None) -> list[tuple[int, str, str]]:
    out = []
    lines = text.splitlines()
    out += twin_after_sign(lines)
    out += unit_install_without_rebaseline(lines)
    out += demo_quadlet_isolation(lines)
    out += completion_sentinel(lines)
    out += heavy_without_load_wait(lines)
    out += leak_scan_redact_before_push(lines)
    out += secret_read_without_control_check(lines)
    out += build_without_provenance(lines)
    out += ok_ping_ungated_check(lines)
    out += hot_reload_without_hold(lines, staged)
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
