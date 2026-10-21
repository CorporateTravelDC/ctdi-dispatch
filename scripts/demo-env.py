#!/usr/bin/env python3
"""scripts/demo-env.py -- /etc/corporatetraveldc/demo.env for the two public-demo
containers (2026-10-19, demo/production database split, relay 2: the flip).

demo.env (example: config/demo.env.example) is the demo containers' whole
non-production environment: the demo identity (DISPATCH_DB_BACKEND, DEMO_PG_*),
the `demo` role's password (DEMO_PG_PASSWORD -- the demo instance's
application role, never a production value) and the two settings runner-demo
used to take from dispatch.env (LLAMA_BASE_URL, OLLAMA_CHAT_MODEL). The
quadlets load it LAST. ~~and the rollback switch (DEMO_SOURCE +
DISPATCH_DB_BACKEND)~~ -- retired 2026-10-20 (operator decision 2026-10-19:
only Pi-hole, vnstat and ACARS Hub touch SQLite; the sovereign file and
demo_access.db are destroyed, so `source sqlite` rolled back to nothing).

Subcommands (none prints a secret value; none puts one on a command line):

  check            names, modes and switch values only; exit 1 on any problem
  seed             copy LLAMA_BASE_URL / OLLAMA_CHAT_MODEL BY NAME from
                   dispatch.env where demo.env still has CHANGE_ME (rerun-safe)
  password-state   prints one word: ok (demo.env value logs in as `demo`),
                   mismatch (a value is there but does not log in), unset
  set-password     converge the role and the file:
                     - demo.env value logs in      -> nothing to do
                     - demo.env value, no login    -> re-apply THAT value
                     - no value                    -> the value in env
                       DEMO_ROLE_NEW_PASSWORD (typed by the operator through
                       `read -rs ... </dev/tty`), or a generated one when that
                       is empty; written to demo.env FIRST (never lost), then
                       applied
                   Applied as a SCRAM-SHA-256 verifier computed here (the
                   plaintext never reaches the server, its logs, or argv):
                   `ALTER ROLE demo PASSWORD 'SCRAM-SHA-256$...'` goes to psql
                   on STDIN inside corporatetraveldc-pgsql-demo, run as
                   demo_owner with the container's own POSTGRES_PASSWORD. The
                   login check passes the value as PGPASSWORD through podman's
                   environment passthrough (`-e PGPASSWORD`, no value on argv).
  ~~source sqlite|postgres~~
                   removed 2026-10-20 with the sqlite rollback. `check` now
                   requires DISPATCH_DB_BACKEND=postgres and refuses any
                   DEMO_SOURCE line other than postgres.

The file is rewritten IN PLACE (open r+, truncate): /etc/corporatetraveldc is
root:corporatetraveldc 0750, so the service user cannot create or rename files
there -- only `sudo install -m 600 -o corporatetraveldc ...` creates demo.env
(hand step), after which this script owns its contents. Mode and owner are
checked, never changed.

Exit: 0 ok, 1 check/login failure, 2 usage or file state.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import pathlib
import re
import secrets
import stat
import subprocess
import sys

DEMO_ENV = pathlib.Path(os.environ.get("DEMO_ENV_FILE", "/etc/corporatetraveldc/demo.env"))
DISPATCH_ENV = pathlib.Path(os.environ.get("DISPATCH_ENV_FILE", "/etc/corporatetraveldc/dispatch.env"))
CONTAINER = os.environ.get("DEMO_PG_CONTAINER", "corporatetraveldc-pgsql-demo")
ROLE = "demo"
DATABASE = "corporatetraveldc_demo"
PLACEHOLDER = "CHANGE_ME"

# 2026-10-20: DEMO_SOURCE is no longer required (the sqlite source is retired;
# demo-api sets DEMO_SOURCE=postgres in its quadlet and defaults to it). A
# leftover line must still say postgres (EXPECTED below).
REQUIRED = ("DISPATCH_ROLE", "DISPATCH_DB_BACKEND", "DISPATCH_DEMO_ISOLATION", "DEMO_PG_HOST", "DEMO_PG_PORT",
            "DEMO_PG_DB", "DEMO_PG_USER", "DEMO_PG_PASSWORD")
EXPECTED = {"DISPATCH_ROLE": "demo", "DISPATCH_DEMO_ISOLATION": "1", "DEMO_PG_HOST": "/var/run/postgresql-demo",
            "DEMO_PG_DB": DATABASE, "DEMO_PG_USER": ROLE,
            "DISPATCH_DB_BACKEND": "postgres", "DEMO_SOURCE": "postgres"}
# Non-secret settings runner-demo used to inherit from dispatch.env; copied by
# NAME only. Nothing else from dispatch.env may ever be copied (a DISPATCH_PG_*
# or a token there is production).
SEED_KEYS = ("LLAMA_BASE_URL", "OLLAMA_CHAT_MODEL")
FORBIDDEN_PREFIXES = ("DISPATCH_PG_",)
FORBIDDEN_KEYS = {"ULTRAFEEDER_URL", "ACARSHUB_URL", "DISPATCH_API_TOKEN", "POSTGRES_PASSWORD"}
# ~~SWITCH = {"sqlite": ..., "postgres": ...}~~ -- the rollback switch is
# retired (2026-10-20).
# The password charset: no whitespace, quote, backslash, '#' or control
# character -- the value goes unquoted into a podman env file and into a libpq
# conninfo string (db_backend._demo_pg_conninfo), and ASCII needs no SASLprep.
PASSWORD_RE = re.compile(r"[A-Za-z0-9._~+/=-]{20,128}")
SCRAM_ITERATIONS = 4096
TAG = "[demo-env]"


class EnvFileError(RuntimeError):
    pass


# -- the file ------------------------------------------------------------------

def _check_file(path: pathlib.Path | None = None) -> None:
    path = path or DEMO_ENV
    try:
        st = path.stat()
    except FileNotFoundError:
        raise EnvFileError(f"{path} does not exist -- hand step: sudo install -m 600 -o corporatetraveldc "
                           f"-g corporatetraveldc config/demo.env.example {path}")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise EnvFileError(f"{path} must be mode 0600 (is {stat.S_IMODE(st.st_mode):o})")
    if st.st_uid != os.getuid():
        raise EnvFileError(f"{path} must be owned by the service user (uid {os.getuid()})")


def read_env(path: pathlib.Path | None = None) -> dict[str, str]:
    path = path or DEMO_ENV
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def write_keys(updates: dict[str, str], path: pathlib.Path | None = None) -> list[str]:
    """Set KEY=value lines in place (replace the first active line, drop
    duplicates, append a missing key). Returns the key NAMES changed. Never
    renames or recreates the file, so mode and owner are untouched."""
    path = path or DEMO_ENV
    _check_file(path)
    for k, v in updates.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or any(c in v for c in "\n\r\"'") or v != v.strip():
            raise EnvFileError(f"refusing to write {k}: name or value shape")
    changed: list[str] = []
    with open(path, "r+", encoding="utf-8") as f:
        lines = f.read().splitlines()
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if (line and not line.startswith("#") and "=" in line) else None
            if key in updates:
                if key in seen:
                    changed.append(key)
                    continue
                seen.add(key)
                new = f"{key}={updates[key]}"
                if new != line:
                    changed.append(key)
                out.append(new)
            else:
                out.append(line)
        for k, v in updates.items():
            if k not in seen:
                out.append(f"{k}={v}")
                changed.append(k)
        if changed:
            f.seek(0)
            f.write("\n".join(out) + "\n")
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
    return sorted(set(changed))


# -- SCRAM ---------------------------------------------------------------------

def scram_verifier(password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """The SCRAM-SHA-256 verifier Postgres stores in pg_authid.rolpassword
    (RFC 5802 / 7677; the format libpq's PQencryptPasswordConn produces).
    ALTER ROLE ... PASSWORD with this string sets it as-is."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def valid_password(pw: str) -> bool:
    return bool(PASSWORD_RE.fullmatch(pw))


# -- podman --------------------------------------------------------------------

def _run(argv: list[str], *, stdin: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin if stdin is not None else None,
                          stdin=None if stdin is not None else subprocess.DEVNULL,
                          capture_output=True, text=True, env=env, timeout=60)


def login_ok(password: str) -> bool:
    """Log in as `demo` over the instance's own socket inside the container
    (the path the demo containers use; hba rejects `demo` over TCP). The
    value travels as PGPASSWORD in podman's environment (`-e PGPASSWORD`
    without a value = passthrough), never on argv."""
    env = {**os.environ, "PGPASSWORD": password}
    r = _run(["podman", "exec", "-e", "PGPASSWORD", CONTAINER, "psql", "-X", "-At", "-U", ROLE, "-d", DATABASE,
              "-c", "SELECT 1"], env=env)
    return r.returncode == 0 and r.stdout.strip() == "1"


def apply_verifier(verifier: str) -> None:
    if not verifier.startswith("SCRAM-SHA-256$") or "'" in verifier:
        raise EnvFileError("not a SCRAM verifier")
    sql = ("SET log_statement = 'none';\n"
           "SET log_min_error_statement = 'panic';\n"
           f"ALTER ROLE {ROLE} PASSWORD '{verifier}';\n")
    r = _run(["podman", "exec", "-i", CONTAINER, "sh", "-c",
              'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" '
              '-d "$POSTGRES_DB" -f -'], stdin=sql)
    if r.returncode != 0:
        # psql's stderr can echo the failing statement; print the class only.
        raise EnvFileError(f"ALTER ROLE {ROLE} failed in {CONTAINER} (psql rc={r.returncode})")


def password_state() -> str:
    pw = read_env().get("DEMO_PG_PASSWORD", "")
    if not pw or pw == PLACEHOLDER:
        return "unset"
    return "ok" if login_ok(pw) else "mismatch"


def set_password() -> int:
    _check_file()
    current = read_env().get("DEMO_PG_PASSWORD", "")
    if current and current != PLACEHOLDER:
        if not valid_password(current):
            print(f"{TAG} DEMO_PG_PASSWORD in {DEMO_ENV} has a shape this script refuses -- fix by hand", file=sys.stderr)
            return 2
        if login_ok(current):
            print(f"{TAG} role {ROLE}: demo.env value already logs in -- nothing to do")
            return 0
        print(f"{TAG} role {ROLE}: demo.env value does not log in -- re-applying it")
        pw = current
    else:
        typed = os.environ.pop("DEMO_ROLE_NEW_PASSWORD", "")
        if typed:
            if not valid_password(typed):
                print(f"{TAG} refused: 20-128 characters from A-Z a-z 0-9 . _ ~ + / = - only", file=sys.stderr)
                return 2
            pw = typed
        else:
            pw = secrets.token_urlsafe(32)
            print(f"{TAG} generated a value (never shown; it lives only in {DEMO_ENV})")
        write_keys({"DEMO_PG_PASSWORD": pw})
        print(f"{TAG} DEMO_PG_PASSWORD written to {DEMO_ENV} (in place, 0600)")
    apply_verifier(scram_verifier(pw))
    if not login_ok(pw):
        print(f"{TAG} role {ROLE}: still cannot log in after ALTER ROLE -- stop", file=sys.stderr)
        return 1
    print(f"{TAG} role {ROLE}: password set (SCRAM verifier on stdin) and login verified over the socket")
    return 0


# -- check / seed ---------------------------------------------------------------

def check() -> list[str]:
    problems: list[str] = []
    try:
        _check_file()
    except EnvFileError as e:
        return [str(e)]
    env = read_env()
    for k in REQUIRED:
        if k not in env:
            problems.append(f"missing {k}")
    for k, v in EXPECTED.items():
        if k in env and env[k] != v:
            problems.append(f"{k} is not {v}")
    for k in env:
        if k.startswith(FORBIDDEN_PREFIXES) or k in FORBIDDEN_KEYS:
            problems.append(f"forbidden key {k} (production)")
    for k, v in env.items():
        if '"' in v or "'" in v:
            problems.append(f"{k} is quoted (podman keeps the quotes)")
    if env.get("DEMO_PG_PASSWORD", PLACEHOLDER) == PLACEHOLDER:
        problems.append("DEMO_PG_PASSWORD not set")
    for k in SEED_KEYS:
        if env.get(k, PLACEHOLDER) == PLACEHOLDER:
            problems.append(f"{k} not seeded")
    # ~~switch half-set check (DEMO_SOURCE + DISPATCH_DB_BACKEND as a pair)~~ --
    # both are now postgres-only through EXPECTED above (2026-10-20).
    return problems


def seed() -> list[str]:
    env = read_env()
    want = [k for k in SEED_KEYS if env.get(k, PLACEHOLDER) == PLACEHOLDER]
    if not want:
        return []
    src = read_env(DISPATCH_ENV)
    updates = {k: src[k] for k in want if src.get(k) and src[k] != PLACEHOLDER}
    missing = sorted(set(want) - set(updates))
    if missing:
        raise EnvFileError(f"dispatch.env has no value for {', '.join(missing)}")
    return write_keys(updates)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "check" and not rest:
            problems = check()
            for p in problems:
                print(f"{TAG} FAIL: {p}")
            if not problems:
                env = read_env()
                print(f"{TAG} ok: {DEMO_ENV} 0600, required keys present, "
                      f"DISPATCH_DB_BACKEND={env['DISPATCH_DB_BACKEND']}")
            return 1 if problems else 0
        if cmd == "seed" and not rest:
            changed = seed()
            print(f"{TAG} seeded: {', '.join(changed) if changed else 'nothing (already set)'}")
            return 0
        if cmd == "password-state" and not rest:
            print(password_state())
            return 0
        if cmd == "set-password" and not rest:
            return set_password()
        if cmd == "source":
            print(f"{TAG} `source` is retired (2026-10-20): the demo runs on Postgres only and "
                  "the sqlite rollback target is destroyed -- nothing changed", file=sys.stderr)
            return 2
    except EnvFileError as e:
        print(f"{TAG} {e}", file=sys.stderr)
        return 2
    print(f"{TAG} usage: check | seed | password-state | set-password", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
