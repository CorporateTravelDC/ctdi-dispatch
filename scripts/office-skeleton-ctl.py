#!/usr/bin/env python3
"""scripts/office-skeleton-ctl.py -- secrets files and database bootstrap for the
office skeletons (Firefly III, Paperless-ngx, Vikunja on corporatetraveldc-pgsql-office;
operator 2026-10-20; TEMPLATE, disabled until after the feature freeze).
docs/deployment-profiles/office-skeletons/README.md, relay steps there.

Subcommands (none prints a secret value; none puts one on a command line):

  check          names, modes and states only, one line per file; exit 1 on any
                 problem (missing file, wrong mode/owner, placeholder left, a
                 forbidden name such as an engine override or a platform
                 setting, a quoted value, PAPERLESS_REDIS not matching the
                 broker password)
  fill           every CHANGE_ME / empty value in the five secrets files is
                 replaced IN PLACE by a generated one (Python `secrets`; hex, so
                 it is safe unquoted in an env file and inside a URL); a real
                 value is never replaced; PAPERLESS_REDIS is derived from
                 VALKEY_PASSWORD. Rerun-safe: a second run changes nothing.
  init-db        src/common/pg_schema_office/0001_office_roles.sql to psql on
                 STDIN inside corporatetraveldc-pgsql-office, as office_owner
                 with the container's own POSTGRES_PASSWORD (idempotent)
  set-passwords  per role (firefly, paperless, vikunja): the value in that
                 service's secrets file logs in -> nothing to do; otherwise a
                 SCRAM-SHA-256 verifier computed here is applied
                 (`ALTER ROLE ... PASSWORD 'SCRAM-SHA-256$...'` on psql's stdin;
                 the plaintext never reaches the server, its logs or argv), then
                 the login is checked over the socket (PGPASSWORD passed through
                 podman's environment, `-e PGPASSWORD` without a value)
  status         container state, and which of the three roles and databases
                 exist (names only)

The files are rewritten IN PLACE (open r+, truncate): /etc/corporatetraveldc is
root:corporatetraveldc 0750, so only `sudo install -m 600 -o corporatetraveldc
-g corporatetraveldc config/<name>.example /etc/corporatetraveldc/<name>` creates
them (hand step); mode and owner are checked, never changed. OFFICE_SECRETS_DIR
overrides the directory (tests only).

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

REPO = pathlib.Path(__file__).resolve().parents[1]
SECRETS_DIR = pathlib.Path(os.environ.get("OFFICE_SECRETS_DIR", "/etc/corporatetraveldc"))
CONTAINER = "corporatetraveldc-pgsql-office"
BROKER_HOST = "corporatetraveldc-valkey-office"
INIT_SQL = REPO / "src/common/pg_schema_office/0001_office_roles.sql"
PLACEHOLDER = "CHANGE_ME"
SCRAM_ITERATIONS = 4096
TAG = "[office-skeleton]"

# file -> {key: kind}; kind decides how `fill` generates a value.
FILES: dict[str, dict[str, str]] = {
    "pgsql-office-secrets.env": {"POSTGRES_PASSWORD": "password"},
    "valkey-office-secrets.env": {"VALKEY_PASSWORD": "password"},
    "firefly-secrets.env": {"APP_KEY": "key32", "DB_PASSWORD": "password", "STATIC_CRON_TOKEN": "key32"},
    "paperless-secrets.env": {"PAPERLESS_SECRET_KEY": "key64", "PAPERLESS_DBPASS": "password",
                              "PAPERLESS_REDIS": "broker_url"},
    "vikunja-secrets.env": {"VIKUNJA_DATABASE_PASSWORD": "password", "VIKUNJA_SERVICE_SECRET": "key64"},
}
# role -> (secrets file, key holding its password)
ROLES: dict[str, tuple[str, str]] = {
    "firefly": ("firefly-secrets.env", "DB_PASSWORD"),
    "paperless": ("paperless-secrets.env", "PAPERLESS_DBPASS"),
    "vikunja": ("vikunja-secrets.env", "VIKUNJA_DATABASE_PASSWORD"),
}
# Never in a secrets file: the engine and connection settings (they are tracked
# Environment= lines in the quadlets; an override here could point an app at
# SQLite or at another instance) and any platform name.
FORBIDDEN_KEYS = {"DB_CONNECTION", "DB_HOST", "DB_DATABASE", "DB_USERNAME", "DB_SOCKET",
                  "PAPERLESS_DBENGINE", "PAPERLESS_DBHOST", "PAPERLESS_DBNAME", "PAPERLESS_DBUSER",
                  "VIKUNJA_DATABASE_TYPE", "VIKUNJA_DATABASE_HOST", "VIKUNJA_DATABASE_PATH",
                  "VIKUNJA_DATABASE_DATABASE", "VIKUNJA_DATABASE_USER", "POSTGRES_USER", "POSTGRES_DB"}
FORBIDDEN_PREFIXES = ("DISPATCH_", "DEMO_", "PGSQL_", "SWIM_")
VALUE_RE = re.compile(r"[A-Za-z0-9._~+/=:@-]{20,256}")


class OfficeError(RuntimeError):
    pass


# -- files ---------------------------------------------------------------------

def path_of(name: str) -> pathlib.Path:
    return SECRETS_DIR / name


def check_file(path: pathlib.Path) -> None:
    try:
        st = path.stat()
    except FileNotFoundError:
        raise OfficeError(f"{path} does not exist -- hand step: sudo install -m 600 -o corporatetraveldc "
                          f"-g corporatetraveldc config/{path.name}.example {path}")
    if not stat.S_ISREG(st.st_mode):
        raise OfficeError(f"{path} is not a regular file")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise OfficeError(f"{path} must be mode 0600 (is {stat.S_IMODE(st.st_mode):o})")
    if st.st_uid != os.getuid():
        raise OfficeError(f"{path} must be owned by the service user (uid {os.getuid()})")


def read_env(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def write_keys(path: pathlib.Path, updates: dict[str, str]) -> list[str]:
    """Set KEY=value lines in place; returns the key NAMES changed. Never renames
    or recreates the file (mode and owner untouched); no symlink is followed."""
    check_file(path)
    for k, v in updates.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or not VALUE_RE.fullmatch(v):
            raise OfficeError(f"refusing to write {k}: name or value shape")
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    changed: list[str] = []
    with os.fdopen(fd, "r+", encoding="utf-8") as f:
        lines = f.read().splitlines()
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if (line and not line.lstrip().startswith("#") and "=" in line) else None
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


def is_placeholder(v: str | None) -> bool:
    return v is None or v.strip() in ("", PLACEHOLDER)


def broker_url(password: str) -> str:
    return f"redis://:{password}@{BROKER_HOST}:6379/0"


def generate(kind: str) -> str:
    if kind == "password":
        return secrets.token_hex(24)          # 48 hex characters
    if kind == "key32":
        return secrets.token_hex(16)          # exactly 32 characters (Laravel APP_KEY, Firefly cron token)
    if kind == "key64":
        return secrets.token_hex(32)
    raise OfficeError(f"no generator for {kind}")


def file_problems(name: str) -> list[str]:
    path = path_of(name)
    try:
        check_file(path)
    except OfficeError as e:
        return [str(e)]
    env = read_env(path)
    probs: list[str] = []
    for k in FILES[name]:
        if k not in env:
            probs.append(f"{name}: {k} missing")
        elif is_placeholder(env[k]):
            probs.append(f"{name}: {k} still a placeholder (run: office-skeleton-ctl.py fill)")
    for k, v in env.items():
        if k in FORBIDDEN_KEYS or k.startswith(FORBIDDEN_PREFIXES):
            probs.append(f"{name}: forbidden name {k} (non-secret settings live in the tracked quadlet)")
        if v[:1] in ("'", '"'):
            probs.append(f"{name}: {k} is quoted (scripts/check-env-quoting.sh)")
    return probs


def check() -> list[str]:
    probs: list[str] = []
    for name in FILES:
        probs += file_problems(name)
    try:
        v = read_env(path_of("valkey-office-secrets.env")).get("VALKEY_PASSWORD")
        r = read_env(path_of("paperless-secrets.env")).get("PAPERLESS_REDIS")
        if not is_placeholder(v) and not is_placeholder(r) and r != broker_url(v):
            probs.append("paperless-secrets.env: PAPERLESS_REDIS does not match VALKEY_PASSWORD (run: fill)")
    except FileNotFoundError:
        pass
    return probs


def fill() -> list[str]:
    """Generate every placeholder; returns 'file:KEY' names changed (never values)."""
    for name in FILES:
        check_file(path_of(name))
    changed: list[str] = []
    for name, keys in FILES.items():
        env = read_env(path_of(name))
        updates = {k: generate(kind) for k, kind in keys.items()
                   if kind != "broker_url" and is_placeholder(env.get(k))}
        if updates:
            changed += [f"{name}:{k}" for k in write_keys(path_of(name), updates)]
    valkey_pw = read_env(path_of("valkey-office-secrets.env"))["VALKEY_PASSWORD"]
    want = broker_url(valkey_pw)
    if read_env(path_of("paperless-secrets.env")).get("PAPERLESS_REDIS") != want:
        changed += [f"paperless-secrets.env:{k}" for k in write_keys(path_of("paperless-secrets.env"),
                                                                      {"PAPERLESS_REDIS": want})]
    return changed


# -- SCRAM (byte twin of scripts/demo-env.py scram_verifier; tests compare) ------

def scram_verifier(password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


# -- podman --------------------------------------------------------------------

def _run(argv: list[str], *, stdin: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                          capture_output=True, text=True, env=env, timeout=120)


OWNER_PSQL = ['podman', 'exec', '-i', CONTAINER, 'sh', '-c',
              'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" '
              '-d "$POSTGRES_DB" -f -']


def container_running() -> bool:
    r = _run(["podman", "container", "inspect", "--format", "{{.State.Running}}", CONTAINER])
    return r.returncode == 0 and r.stdout.strip() == "true"


def owner_sql(sql: str) -> str:
    r = _run(OWNER_PSQL, stdin=sql)
    if r.returncode != 0:
        # psql's stderr can echo the failing statement; print the class only.
        raise OfficeError(f"psql as office_owner failed in {CONTAINER} (rc={r.returncode})")
    return r.stdout


def init_db() -> None:
    if not container_running():
        raise OfficeError(f"{CONTAINER} is not running -- start it first (relay step)")
    owner_sql(INIT_SQL.read_text(encoding="utf-8"))


def login_ok(role: str, password: str) -> bool:
    env = {**os.environ, "PGPASSWORD": password}
    r = _run(["podman", "exec", "-e", "PGPASSWORD", CONTAINER, "psql", "-X", "-At", "-U", role, "-d", role,
              "-c", "SELECT 1"], env=env)
    return r.returncode == 0 and r.stdout.strip() == "1"


def apply_verifier(role: str, verifier: str) -> None:
    if role not in ROLES or not verifier.startswith("SCRAM-SHA-256$") or "'" in verifier:
        raise OfficeError("not a known role or not a SCRAM verifier")
    owner_sql("SET log_statement = 'none';\nSET log_min_error_statement = 'panic';\n"
              f"ALTER ROLE {role} PASSWORD '{verifier}';\n")


def set_passwords() -> int:
    if not container_running():
        raise OfficeError(f"{CONTAINER} is not running -- start it first (relay step)")
    rc = 0
    for role, (fname, key) in ROLES.items():
        pw = read_env(path_of(fname)).get(key)
        if is_placeholder(pw) or not VALUE_RE.fullmatch(pw or ""):
            print(f"{TAG} role {role}: {fname} {key} is unset or a refused shape -- run fill first", file=sys.stderr)
            rc = 2
            continue
        if login_ok(role, pw):
            print(f"{TAG} role {role}: the value in {fname} already logs in -- nothing to do")
            continue
        apply_verifier(role, scram_verifier(pw))
        if login_ok(role, pw):
            print(f"{TAG} role {role}: password set (SCRAM verifier on stdin), login verified over the socket")
        else:
            print(f"{TAG} role {role}: still cannot log in after ALTER ROLE -- stop", file=sys.stderr)
            rc = 1
    return rc


def status() -> list[str]:
    if not container_running():
        return [f"{CONTAINER}: not running"]
    names = ", ".join(f"'{r}'" for r in ROLES)
    out = owner_sql(f"SELECT 'role:' || rolname FROM pg_roles WHERE rolname IN ({names});\n"
                    f"SELECT 'db:' || datname FROM pg_database WHERE datname IN ({names});\n")
    have = set(out.split())
    return [f"{CONTAINER}: running"] + [f"{kind} {r}: {'present' if f'{kind}:{r}' in have else 'absent'}"
                                        for r in ROLES for kind in ("role", "db")]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if len(args) == 1 else None
    try:
        if cmd == "check":
            probs = check()
            for p in probs:
                print(f"{TAG} {p}", file=sys.stderr)
            if not probs:
                print(f"{TAG} ok: {len(FILES)} secrets files, names and modes as expected")
            return 1 if probs else 0
        if cmd == "fill":
            changed = fill()
            print(f"{TAG} fill: {len(changed)} value(s) generated or derived (never shown): "
                  + (", ".join(changed) if changed else "none -- already filled"))
            return 0
        if cmd == "init-db":
            init_db()
            print(f"{TAG} init-db: roles and databases present (idempotent)")
            return 0
        if cmd == "set-passwords":
            return set_passwords()
        if cmd == "status":
            for line in status():
                print(f"{TAG} {line}")
            return 0
    except OfficeError as e:
        print(f"{TAG} {e}", file=sys.stderr)
        return 2
    print(f"{TAG} usage: check | fill | init-db | set-passwords | status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
