"""
scripts/pg_migrate.py, run against the real corporatetraveldc-pgsql
container -- but always into its own throwaway Postgres SCHEMA inside the
scratch database (corporatetraveldc_pgphase1_test), created and dropped
by this test, so it never collides with the persistent copy of the
scratch DB other tests/manual runs use, and never touches `public` there
either. Skips (not fails) if postgres is unreachable.

Covers the two things the Phase 1 handoff explicitly needs verified:
  - every migration file applies cleanly, in order, against a database
    that has never seen them;
  - running the whole set a second time is a clean no-op (the
    schema_migrations checksum-tracked skip path, not just DDL happening
    to be idempotent).
"""
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from common import config, db_backend  # noqa: E402

SCRATCH_DB = "corporatetraveldc_pgphase1_test"


def _postgres_reachable() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        conninfo = db_backend._pg_conninfo().replace(  # noqa: SLF001
            f"dbname={config.get('DISPATCH_PG_DB', 'corporatetraveldc')}",
            f"dbname={SCRATCH_DB}",
        )
        with psycopg.connect(conninfo, connect_timeout=3) as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(),
    reason=f"corporatetraveldc-pgsql unreachable or scratch database {SCRATCH_DB!r} does not exist",
)


@pytest.fixture
def scratch_schema():
    import psycopg

    # Postgres reserves the "pg_" prefix for system schemas.
    schema = f"phase1_migtest_{uuid.uuid4().hex[:8]}"
    conninfo = db_backend._pg_conninfo().replace(  # noqa: SLF001
        f"dbname={config.get('DISPATCH_PG_DB', 'corporatetraveldc')}",
        f"dbname={SCRATCH_DB}",
    )
    with psycopg.connect(conninfo, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    yield schema
    with psycopg.connect(conninfo, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "pg_migrate.py"),
         "--database", SCRATCH_DB, *args],
        capture_output=True, text=True, timeout=60,
    )


def test_migrations_apply_cleanly_to_an_empty_schema(scratch_schema):
    result = _run("--schema", scratch_schema)
    assert result.returncode == 0, result.stderr
    # 2026-09-19: real count from an empty schema is 55 (0001-0055) -- the
    # literal "48" this assertion previously checked was already stale
    # before tonight (0049-0051 existed live, unreflected here); 0052-0055
    # are the 11 reference tables + the second-brain vault index
    # (docs/POSTGRES_MIGRATION.md sec2's JOIN exception).
    assert "Applied 55 migration(s)" in result.stdout, result.stdout


def test_migrations_are_idempotent_on_second_run(scratch_schema):
    first = _run("--schema", scratch_schema)
    assert first.returncode == 0, first.stderr

    second = _run("--schema", scratch_schema)
    assert second.returncode == 0, second.stderr
    assert "Nothing to do -- all" in second.stdout, second.stdout
    assert "already applied" in second.stdout


def test_status_flag_reports_pending_then_none(scratch_schema):
    before = _run("--schema", scratch_schema, "--status")
    assert before.returncode == 0, before.stderr
    assert "0 applied" in before.stdout

    applied = _run("--schema", scratch_schema)
    assert applied.returncode == 0, applied.stderr

    after = _run("--schema", scratch_schema, "--status")
    assert after.returncode == 0, after.stderr
    assert "0 pending" in after.stdout


# 2026-09-19: the second-brain vault index tables (pg_schema/0055) are a
# deliberately separate concern from the dispatch write path (PG_TABLES) --
# same Postgres instance, different logical grouping (docs/POSTGRES_MIGRATION.md
# sec2 / db_backend.py's REFERENCE_TABLES comment). Listed explicitly here
# rather than imported from anywhere, since there is no equivalent to
# PG_TABLES for them -- src/second_brain/semantic/compile.py's own _TABLES
# only covers the semantic_* subset, not vault_documents/vault_links/entities.
_SECOND_BRAIN_TABLES = frozenset({
    "vault_documents", "vault_notes_fulltext", "entities", "vault_links",
    "semantic_note_chronology", "semantic_meta", "semantic_facets",
    "semantic_concepts", "semantic_labels", "semantic_relations",
    "semantic_agents", "semantic_metrics", "semantic_note_concepts",
    "semantic_unmapped_tags", "semantic_note_derivations",
})


def test_all_76_pg_bound_and_second_brain_tables_created(scratch_schema):
    import psycopg
    from psycopg.rows import dict_row

    result = _run("--schema", scratch_schema)
    assert result.returncode == 0, result.stderr

    conninfo = db_backend._pg_conninfo().replace(  # noqa: SLF001
        f"dbname={config.get('DISPATCH_PG_DB', 'corporatetraveldc')}",
        f"dbname={SCRATCH_DB}",
    )
    with psycopg.connect(conninfo, row_factory=dict_row) as c:
        rows = c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_type='BASE TABLE'",
            (scratch_schema,),
        ).fetchall()
    tables = {r["table_name"] for r in rows} - {"schema_migrations"}
    assert tables == db_backend.PG_TABLES | _SECOND_BRAIN_TABLES
