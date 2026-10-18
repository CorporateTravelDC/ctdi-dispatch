"""scripts/pg-maintenance.sh (2026-10-18, lean-out L1): CONCURRENTLY index
work that a migration cannot do (pg_migrate.py runs each file in one
transaction). Driven against a fake `podman` that records every SQL
statement and answers the catalog reads from files -- never a real database.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "pg-maintenance.sh"

FAKE_PODMAN = r"""#!/usr/bin/env bash
# args: exec -i -e PGOPTIONS=... CONTAINER sh -c '<psql wrapper>' sh "<SQL>"
sql="${@: -1}"
printf '%s\n' "$sql" >> "$FAKE_DIR/sql.log"
printf '%s\n' "$*" | grep -q -- 'PGOPTIONS=-c statement_timeout=0 -c lock_timeout=300s' || { echo "no session opts" >&2; exit 9; }
name="$(printf '%s' "$sql" | grep -o "relname = '[^']*'" | head -1 | cut -d"'" -f2)"
case "$sql" in
  *"_ccnew%"*) cat "$FAKE_DIR/leftovers" 2>/dev/null; exit 0 ;;
  *"indisready"*) cat "$FAKE_DIR/state.$name" 2>/dev/null; exit 0 ;;
  *pg_relation_size*) cat "$FAKE_DIR/size.$name" 2>/dev/null || echo 0; exit 0 ;;
  "CREATE INDEX CONCURRENTLY IF NOT EXISTS "*)
      n="$(printf '%s' "$sql" | awk '{print $7}')"; echo valid > "$FAKE_DIR/state.$n"; echo 5 > "$FAKE_DIR/size.$n" ;;
  "DROP INDEX CONCURRENTLY IF EXISTS public."*)
      n="${sql##*public.}"; rm -f "$FAKE_DIR/state.$n"; : > "$FAKE_DIR/leftovers" ;;
  "REINDEX INDEX CONCURRENTLY public."*)
      n="${sql##*public.}"; echo 30 > "$FAKE_DIR/size.$n" ;;
  *) echo "unexpected SQL" >&2; exit 8 ;;
esac
"""

NAMES = ["idx_nas_programs_active", "idx_flight_ooooi_times_callsign_updated",
         "idx_flight_events_registration_upper", "idx_flight_events_flight_num_origin"]


def _env(tmp_path, **extra):
    fake = tmp_path / "podman"
    fake.write_text(FAKE_PODMAN)
    fake.chmod(0o755)
    return {"PATH": "/usr/bin:/bin", "FAKE_DIR": str(tmp_path), "PG_MAINT_PODMAN": str(fake), **extra}


def _run(tmp_path, *args, **extra):
    return subprocess.run(["bash", str(SCRIPT), *args], env=_env(tmp_path, **extra),
                          capture_output=True, text=True, timeout=60)


def _writes(tmp_path):
    log = tmp_path / "sql.log"
    lines = log.read_text().splitlines() if log.exists() else []
    return [l for l in lines if l.startswith(("CREATE", "DROP", "REINDEX"))]


def test_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_creates_every_missing_index_then_a_rerun_is_a_no_op(tmp_path):
    r = _run(tmp_path, "create-indexes-concurrently")
    assert r.returncode == 0, r.stderr
    w = _writes(tmp_path)
    assert len(w) == len(NAMES)
    assert all(s.startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS ") for s in w)
    assert [s.split()[6] for s in w] == NAMES
    assert any("(upper(registration), updated_at) INCLUDE (airline, flight_num) WHERE registration IS NOT NULL" in s
               for s in w)
    assert any("public.nas_programs (type, facility) WHERE active = 1" in s for s in w)
    (tmp_path / "sql.log").unlink()
    r = _run(tmp_path, "create-indexes-concurrently")
    assert r.returncode == 0 and _writes(tmp_path) == []
    assert r.stdout.count("valid -- skip") == len(NAMES)


def test_invalid_index_is_dropped_and_rebuilt(tmp_path):
    (tmp_path / "state.idx_flight_events_flight_num_origin").write_text("invalid\n")
    r = _run(tmp_path, "create-indexes-concurrently", "idx_flight_events_flight_num_origin")
    assert r.returncode == 0, r.stderr
    assert _writes(tmp_path) == [
        "DROP INDEX CONCURRENTLY IF EXISTS public.idx_flight_events_flight_num_origin",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_flight_events_flight_num_origin "
        "ON public.flight_events (flight_num, origin)",
    ]


def test_unknown_index_name_is_refused(tmp_path):
    r = _run(tmp_path, "create-indexes-concurrently", "idx_anything_else")
    assert r.returncode == 1 and _writes(tmp_path) == []


def test_reindex_runs_once_then_skips(tmp_path):
    (tmp_path / "state.idx_flight_events_updated_at").write_text("valid\n")
    (tmp_path / "size.idx_flight_events_updated_at").write_text("2150\n")
    (tmp_path / "leftovers").write_text("idx_flight_events_updated_at_ccnew\n")
    r = _run(tmp_path, "reindex-flight-events-updated-at")
    assert r.returncode == 0, r.stderr
    assert _writes(tmp_path) == [
        "DROP INDEX CONCURRENTLY IF EXISTS public.idx_flight_events_updated_at_ccnew",
        "REINDEX INDEX CONCURRENTLY public.idx_flight_events_updated_at",
    ]
    assert "2150 MB before" in r.stdout and "30 MB after" in r.stdout
    (tmp_path / "sql.log").unlink()
    r = _run(tmp_path, "reindex-flight-events-updated-at")
    assert r.returncode == 0 and _writes(tmp_path) == [] and "skip" in r.stdout


def test_reindex_refuses_an_invalid_target(tmp_path):
    (tmp_path / "state.idx_flight_events_updated_at").write_text("invalid\n")
    r = _run(tmp_path, "reindex-flight-events-updated-at")
    assert r.returncode == 1 and _writes(tmp_path) == []


def test_dry_run_writes_nothing(tmp_path):
    (tmp_path / "state.idx_flight_events_updated_at").write_text("valid\n")
    (tmp_path / "size.idx_flight_events_updated_at").write_text("2150\n")
    for sub in ("create-indexes-concurrently", "reindex-flight-events-updated-at"):
        r = _run(tmp_path, sub, PG_MAINT_DRY_RUN="1")
        assert r.returncode == 0, r.stderr
        assert "dry run -- not executed" in r.stdout
    assert _writes(tmp_path) == []


def test_status_is_read_only(tmp_path):
    r = _run(tmp_path, "status")
    assert r.returncode == 0, r.stderr
    assert _writes(tmp_path) == []
    assert len(r.stdout.splitlines()) == len(NAMES) + 1


def test_every_podman_exec_closes_stdin():
    """relay-lint's rule for relays, applied to the script itself: a podman
    exec that can read stdin would swallow the rest of a relay."""
    src = SCRIPT.read_text()
    execs = [l for l in src.splitlines() if '"$PODMAN" exec' in l]
    assert execs
    body = src[src.index('psql_one() {'):]
    assert "</dev/null" in body[:body.index("\n}\n")]
