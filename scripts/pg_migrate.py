#!/usr/bin/env python3
"""Apply src/common/pg_schema/*.sql migrations to the corporatetraveldc-pgsql
Postgres instance, in filename order, tracked in a schema_migrations table.

Idempotent and safe to run repeatedly: each migration's own DDL is
IF-NOT-EXISTS-shaped (see docs/POSTGRES_MIGRATION.md §4), and this runner
additionally skips any filename already recorded in schema_migrations
(matched by sha256 of its current bytes -- a mismatch means the applied
file was edited since it ran, and is a hard error rather than a silent
re-apply).

Phase 1 only: this creates the schema, nothing else. It never touches
SQLite, never copies a row, never flips DISPATCH_DB_BACKEND. Safe to point
at the live corporatetraveldc-pgsql instance (Phase 0, currently unused by
every service) or at a scratch database/schema for testing -- see
--database/--schema below.

Usage:
    scripts/pg_migrate.py                 # apply to DISPATCH_PG_DB, public schema
    scripts/pg_migrate.py --database corporatetraveldc_test
    scripts/pg_migrate.py --schema pg_phase1_test
    scripts/pg_migrate.py --dry-run       # print what would run, do nothing
    scripts/pg_migrate.py --status        # list applied vs. pending, exit
    scripts/pg_migrate.py --instance demo # the DEMO instance (see below)

--instance demo (2026-10-16, demo/production database split): applies
src/common/pg_schema_demo/*.sql to the demo Postgres instance
(corporatetraveldc-pgsql-demo) through db_backend._demo_pg_conninfo() --
DEMO_PG_HOST/PORT/DB/USER/PASSWORD, never DISPATCH_PG_*. The connecting role
must be demo_owner (DDL runs as the instance's owner; refused otherwise), and
the password is DEMO_PG_PASSWORD or, when that is unset, POSTGRES_PASSWORD
from /etc/corporatetraveldc/pgsql-demo-secrets.env (the instance's own
bootstrap password IS demo_owner's). The two instances never share a
migration directory, a tracking table or a credential.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
SCHEMA_DIR = SRC / "common" / "pg_schema"
DEMO_SCHEMA_DIR = SRC / "common" / "pg_schema_demo"
DEMO_SECRETS_FILE = Path("/etc/corporatetraveldc/pgsql-demo-secrets.env")

sys.path.insert(0, str(SRC))

from common import config, db_backend  # noqa: E402

INSTANCES = ("production", "demo")


def schema_dir(instance: str) -> Path:
    return DEMO_SCHEMA_DIR if instance == "demo" else SCHEMA_DIR


def migration_files(instance: str = "production") -> list[Path]:
    return sorted(schema_dir(instance).glob("*.sql"), key=lambda p: p.name)


def read_env_value(path: Path, key: str) -> str:
    """One KEY from a podman-style env file, verbatim (no quote stripping,
    no shell sourcing -- scripts/with-dispatch-env.sh's rules). Never
    printed."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line[len(key) + 1:]
    except OSError:
        pass
    return ""


def prepare_demo_env() -> str | None:
    """Pin the demo instance's host-side identity before conninfo is built:
    DEMO_PG_USER must be demo_owner, and DEMO_PG_PASSWORD falls back to the
    instance's bootstrap password file. Returns an error string or None."""
    user = config.get("DEMO_PG_USER", db_backend.DEMO_PG_OWNER)
    if user != db_backend.DEMO_PG_OWNER:
        return (f"--instance demo runs DDL as {db_backend.DEMO_PG_OWNER!r} only "
                f"(DEMO_PG_USER={user!r})")
    import os
    os.environ.setdefault("DEMO_PG_USER", db_backend.DEMO_PG_OWNER)
    if not config.get("DEMO_PG_PASSWORD", ""):
        pw = read_env_value(DEMO_SECRETS_FILE, "POSTGRES_PASSWORD")
        if pw:
            os.environ["DEMO_PG_PASSWORD"] = pw
    return None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_conninfo(database: str | None, instance: str = "production") -> str:
    if instance == "demo":
        conninfo = db_backend._demo_pg_conninfo()  # noqa: SLF001 -- intentional reuse
    else:
        conninfo = db_backend._pg_conninfo()  # noqa: SLF001 -- intentional reuse
    if database:
        # Replace the dbname= token rather than reconstructing the whole
        # string, so host-fallback/password logic stays identical to the
        # app's own connection path.
        parts = conninfo.split(" ")
        parts = [p for p in parts if not p.startswith("dbname=")]
        parts.append(f"dbname={database}")
        conninfo = " ".join(parts)
    return conninfo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", default=None, help="Override DISPATCH_PG_DB (e.g. a scratch test database).")
    ap.add_argument("--schema", default="public", help="Postgres schema to apply into (default: public).")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be applied, change nothing.")
    ap.add_argument("--status", action="store_true", help="List applied/pending migrations and exit.")
    ap.add_argument("--instance", choices=INSTANCES, default="production",
                    help="Which Postgres instance: production (DISPATCH_PG_*, pg_schema/) or demo "
                         "(DEMO_PG_*, pg_schema_demo/; as demo_owner). Default: production.")
    args = ap.parse_args()

    import psycopg

    if args.instance == "demo":
        err = prepare_demo_env()
        if err:
            print(f"REFUSING: {err}", file=sys.stderr)
            return 2
    conninfo = build_conninfo(args.database, args.instance)
    files = migration_files(args.instance)
    if not files:
        print(f"No migration files found in {schema_dir(args.instance)}", file=sys.stderr)
        return 1

    with psycopg.connect(conninfo, autocommit=False) as conn:
        with conn.cursor() as cur:
            if args.schema != "public":
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{args.schema}"')
                cur.execute(f'SET search_path TO "{args.schema}"')
            conn.commit()

        # Bootstrap: 0001 creates schema_migrations itself. Applied through
        # the same code path as every other file below (no special-casing).
        applied: dict[str, str] = {}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass(%s)",
                (f"{args.schema}.schema_migrations",),
            )
            exists = cur.fetchone()[0] is not None
            if exists:
                cur.execute("SELECT filename, checksum FROM schema_migrations")
                applied = {row[0]: row[1] for row in cur.fetchall()}
        conn.commit()

        pending = []
        for f in files:
            checksum = sha256_of(f)
            if f.name in applied:
                if applied[f.name] != checksum:
                    print(
                        f"REFUSING: {f.name} was already applied with a "
                        f"different checksum (recorded {applied[f.name][:12]}, "
                        f"now {checksum[:12]}) -- the file changed after it "
                        "ran. Fix by hand or add a new numbered migration "
                        "instead of editing an applied one.",
                        file=sys.stderr,
                    )
                    return 2
                continue
            pending.append((f, checksum))

        if args.status:
            default_db = (config.get("DEMO_PG_DB", db_backend.DEMO_PG_DB) if args.instance == "demo"
                          else config.get("DISPATCH_PG_DB", "corporatetraveldc"))
            print(f"{len(applied)} applied, {len(pending)} pending (instance={args.instance}, schema={args.schema}, db={args.database or default_db})")
            for f, _ in pending:
                print(f"  pending: {f.name}")
            return 0

        if not pending:
            print(f"Nothing to do -- all {len(files)} migrations already applied (instance={args.instance}, schema={args.schema}).")
            return 0

        if args.dry_run:
            print(f"Would apply {len(pending)} migration(s):")
            for f, _ in pending:
                print(f"  {f.name}")
            return 0

        for f, checksum in pending:
            sql = f.read_text()
            with conn.cursor() as cur:
                try:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename, checksum) "
                        "VALUES (%s, %s)",
                        (f.name, checksum),
                    )
                except Exception:
                    conn.rollback()
                    print(f"FAILED applying {f.name}", file=sys.stderr)
                    raise
            conn.commit()
            print(f"applied {f.name}")

        print(f"Applied {len(pending)} migration(s) (instance={args.instance}, schema={args.schema}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
