#!/usr/bin/env python3
"""scripts/pg-ro-role.py -- the read-only Postgres role for agents (2026-10-26).

Creates or re-asserts `dispatch_ro` in the production database: LOGIN, no
superuser / createdb / createrole / replication / bypassrls, no inherited
roles, 4 connections, USAGE on schema public and SELECT on every table and
view there EXCEPT those matched by config/postgres/dispatch-ro-deny.txt. Every
other privilege the role holds on schema public is revoked first, so the run
is idempotent and a removed grant really goes. A table created later is not
readable until this runs again (fail closed).

Password: DISPATCH_PG_RO_USER / DISPATCH_PG_RO_PASSWORD in
/etc/corporatetraveldc/dispatch-secrets.env, generated on first run (or with
--rotate) and never printed. The database receives only a SCRAM-SHA-256
verifier computed here, so the password is in no command line, no SQL text
and no server log. Agents get the pair through the agent secrets subset
(both names are already on secrets-allowlist-agent.txt): re-run plan step 3
as root afterwards.

    pg-ro-role.py            apply, then check
    pg-ro-role.py --check    check only (no writes): role attributes, grants vs
                             the deny list, no write privilege anywhere, and a
                             real password login over 127.0.0.1
    pg-ro-role.py --rotate   new password, then apply and check

Run as the operator, without sudo (the database is the operator's rootless
container). Prints counts and table names, never a value.
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import hmac
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENY_FILE = ROOT / "config" / "postgres" / "dispatch-ro-deny.txt"
SECRETS = Path(os.environ.get("CTDC_SECRETS_FILE", "/etc/corporatetraveldc/dispatch-secrets.env"))
CONTAINER = "corporatetraveldc-pgsql"
ROLE = "dispatch_ro"
USER_KEY, PASS_KEY = "DISPATCH_PG_RO_USER", "DISPATCH_PG_RO_PASSWORD"
SCRAM_ITERATIONS = 4096
TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


# ---------------------------------------------------------------- pure helpers

def scram_verifier(password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """The SCRAM-SHA-256 verifier Postgres stores (RFC 5802/7677). ASCII passwords only
    (SASLprep is the identity for them); ours come from secrets.token_urlsafe."""
    if not password.isascii():
        raise ValueError("password must be ASCII")
    salt = salt if salt is not None else os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def load_deny(path: Path = DENY_FILE) -> list[str]:
    pats = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            if not re.fullmatch(r"[a-z0-9_*?]+", line):
                raise ValueError(f"bad deny pattern {line!r}")
            pats.append(line)
    if not pats:
        raise ValueError("deny list is empty -- refusing (it would grant every table)")
    return pats


def split_tables(tables: list[str], deny: list[str]) -> tuple[list[str], list[str]]:
    allowed, denied = [], []
    for t in sorted(set(tables)):
        (denied if any(fnmatch.fnmatchcase(t, p) for p in deny) else allowed).append(t)
    return allowed, denied


def apply_sql(verifier: str, allowed: list[str]) -> str:
    for t in allowed:
        if not TABLE_RE.match(t):
            raise ValueError(f"unexpected table name {t!r}")
    if not re.fullmatch(r"SCRAM-SHA-256\$\d+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+", verifier):
        raise ValueError("not a SCRAM verifier")
    grants = "\n".join(f'GRANT SELECT ON TABLE public."{t}" TO {ROLE};' for t in allowed)
    return f"""\\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN CREATE ROLE {ROLE} LOGIN; END IF;
END $$;
ALTER ROLE {ROLE} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
  CONNECTION LIMIT 4 PASSWORD '{verifier}';
ALTER ROLE {ROLE} SET default_transaction_read_only = on;
ALTER ROLE {ROLE} SET statement_timeout = '30s';
ALTER ROLE {ROLE} SET idle_in_transaction_session_timeout = '60s';
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT roleid::regrole AS g FROM pg_auth_members WHERE member = '{ROLE}'::regrole LOOP
    EXECUTE format('REVOKE %s FROM {ROLE}', r.g);
  END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE};
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE};
REVOKE ALL ON SCHEMA public FROM {ROLE};
DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO {ROLE}', current_database()); END $$;
GRANT USAGE ON SCHEMA public TO {ROLE};
{grants}
COMMIT;
"""


CHECK_SQL = f"""SELECT 'attr', rolsuper::text||','||rolcreatedb::text||','||rolcreaterole::text||','||rolreplication::text||','||rolbypassrls::text||','||rolinherit::text||','||rolcanlogin::text FROM pg_roles WHERE rolname = '{ROLE}';
SELECT 'member', roleid::regrole::text FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname = '{ROLE}');
SELECT 'select', c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f') AND has_table_privilege('{ROLE}', c.oid, 'SELECT');
SELECT 'write', c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','p','v','m','f') AND (has_table_privilege('{ROLE}', c.oid, 'INSERT') OR has_table_privilege('{ROLE}', c.oid, 'UPDATE') OR has_table_privilege('{ROLE}', c.oid, 'DELETE') OR has_table_privilege('{ROLE}', c.oid, 'TRUNCATE'));
SELECT 'create', has_schema_privilege('{ROLE}', 'public', 'CREATE')::text;
"""

TABLES_SQL = ("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
              "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f') ORDER BY 1;")


# ---------------------------------------------------------------- I/O

def psql(sql: str) -> str:
    """Run SQL as the database owner inside the container; SQL goes on stdin, never argv."""
    p = subprocess.run(
        ["podman", "exec", "-i", CONTAINER, "sh", "-c",
         'exec psql -X -q -At -F "|" -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -'],
        input=sql, capture_output=True, text=True, timeout=120)
    if p.returncode:
        # psql errors can echo statement text; ours never contains a password, only a verifier
        raise SystemExit(f"psql failed (rc {p.returncode}): {p.stderr.strip()[:300]}")
    return p.stdout


def read_env_keys(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[m.group(1)] = v
    return out


def set_env_keys(path: Path, updates: dict[str, str]) -> None:
    """Replace or append KEY=value lines in place (the operator cannot create files in
    /etc/corporatetraveldc, root:corporatetraveldc 0750, so no temp-file rename); the
    file keeps its inode, owner and mode."""
    text = path.read_text()
    lines = text.splitlines()
    done = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0]
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            done.add(k)
    if not (set(updates) - done):
        new = "\n".join(lines) + "\n"
        with open(path, "r+") as f:
            f.write(new)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
        return
    with open(path, "a") as f:  # append-only when every key is new: nothing existing is rewritten
        if done:
            raise RuntimeError("mixed replace and append; run --rotate again")
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write("".join(f"{k}={v}\n" for k, v in updates.items()))
        f.flush()
        os.fsync(f.fileno())


def login_test(password: str) -> str:
    try:
        import psycopg
    except ImportError:
        return "skipped (psycopg not importable)"
    port = read_env_keys(Path("/etc/corporatetraveldc/dispatch.env")).get("DISPATCH_PG_PORT", "5432")
    db = psql("SELECT current_database();").strip()
    try:
        with psycopg.connect(host="127.0.0.1", port=port, dbname=db, user=ROLE, password=password,
                             connect_timeout=10) as c:
            ro = c.execute("SHOW default_transaction_read_only").fetchone()[0]
            return f"ok (password login over 127.0.0.1, default_transaction_read_only={ro})"
    except Exception as e:  # noqa: BLE001
        return f"FAILED ({type(e).__name__})"


def check(deny: list[str], password: str | None) -> int:
    fails = 0
    rows = [r.split("|", 1) for r in psql(CHECK_SQL).splitlines() if "|" in r]
    attr = [v for k, v in rows if k == "attr"]
    if not attr:
        print(f"FAIL: role {ROLE} does not exist")
        return 1
    want = "false,false,false,false,false,false,true"
    print(("ok" if attr[0] == want else "FAIL") + f": attributes super,createdb,createrole,replication,bypassrls,inherit,login = {attr[0]}")
    fails += attr[0] != want
    members = [v for k, v in rows if k == "member"]
    print(("FAIL" if members else "ok") + f": member of {members or 'no role'}")
    fails += bool(members)
    granted = sorted(v for k, v in rows if k == "select")
    leaked = [t for t in granted if any(fnmatch.fnmatchcase(t, p) for p in deny)]
    print(("FAIL" if leaked else "ok") + f": SELECT on {len(granted)} relation(s); denied ones readable: {leaked or 'none'}")
    fails += bool(leaked)
    writes = sorted(v for k, v in rows if k == "write")
    print(("FAIL" if writes else "ok") + f": write privilege on {writes or 'nothing'}")
    fails += bool(writes)
    create = [v for k, v in rows if k == "create"]
    print(("FAIL" if create != ["false"] else "ok") + f": CREATE on schema public = {create}")
    fails += create != ["false"]
    all_tables = psql(TABLES_SQL).split()
    _, denied = split_tables(all_tables, deny)
    missing = sorted(set(all_tables) - set(granted) - set(denied))
    if missing:
        print(f"note: {len(missing)} table(s) neither granted nor denied (created since the last run; re-run to grant): {missing}")
    if password:
        res = login_test(password)
        print(("FAIL" if res.startswith("FAILED") else "ok") + f": login {res}")
        fails += res.startswith("FAILED")
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="read-only Postgres role for agents")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--rotate", action="store_true")
    a = ap.parse_args(argv)
    deny = load_deny()
    env = read_env_keys(SECRETS)
    password = env.get(PASS_KEY) or None

    if a.check:
        return check(deny, password)

    updates = {}
    if env.get(USER_KEY) != ROLE:
        updates[USER_KEY] = ROLE
    if a.rotate or not password:
        password = secrets.token_urlsafe(32)
        updates[PASS_KEY] = password
    if updates:
        set_env_keys(SECRETS, updates)
        print(f"secrets: set {', '.join(sorted(updates))} in {SECRETS} (values not shown)")

    allowed, denied = split_tables(psql(TABLES_SQL).split(), deny)
    psql(apply_sql(scram_verifier(password), allowed))
    print(f"applied: {ROLE} may SELECT {len(allowed)} relation(s); {len(denied)} denied by {DENY_FILE.relative_to(ROOT)}")
    rc = check(deny, password)
    print("next (root): sudo scripts/agent-segmentation/plan.sh --execute --i-am-the-operator --only=3"
          "   # copies the pair into the agent secrets subset")
    return rc


if __name__ == "__main__":
    sys.exit(main())
