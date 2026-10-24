"""scripts/safe-pg-image-update.sh: the canary sample load must exclude
generated columns (2026-10-22: vault_notes_fulltext.search_vector, a
GENERATED ALWAYS ... STORED tsvector, made `\\copy t FROM STDIN` fail with
"extra data after last expected column" -> 0 rows -> image update skipped).
Static checks only -- never touches a database.
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "safe-pg-image-update.sh"
SRC = SCRIPT.read_text()


def test_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_column_list_excludes_generated_columns():
    assert "attgenerated = ''" in SRC
    assert "NOT attisdropped" in SRC


def test_no_select_star_sample_export():
    assert not re.search(r"\\copy \(SELECT \* FROM", SRC)
    assert "\\copy (SELECT ${cols} FROM ${t} LIMIT ${SAMPLE_LIMIT}) TO STDOUT" in SRC


def test_copy_in_uses_same_explicit_column_list():
    assert "\\copy ${t} (${cols}) FROM STDIN" in SRC
    assert not re.search(r"\\copy \$\{t\} FROM STDIN", SRC)


def test_row_count_validation_still_strict():
    assert 'if [[ "${live_count}" != "${canary_count}" ]]' in SRC


def test_fk_owning_tables_load_with_ri_off_on_canary_only():
    # independent per-table LIMIT samples break FKs (child rows point at
    # parents outside the parent's sample; self-refs point outside their own)
    assert "k.contype = 'f'" in SRC
    assert 'load_opts="-c session_replication_role=replica"' in SRC
    assert '[[ "${fk_children}" == *" ${t} "* ]]' in SRC
    # replica mode only ever goes to the canary session, never the live one
    for line in SRC.splitlines():
        if "PGOPTIONS" in line and "podman exec" in line:
            assert "CANARY_CONTAINER" in line and "LIVE_CONTAINER" not in line


def test_log_scan_matches_severity_tag_not_bare_word():
    # bare "ERROR" (case-insensitive) false-positived on a LOG: duration line
    # quoting the audit_log column egress_last_error
    assert 'grep -E "(WARNING|ERROR|FATAL|PANIC): "' in SRC
    assert 'grep -iE "WARNING|ERROR|FATAL"' not in SRC
    pat = re.compile(r"(WARNING|ERROR|FATAL|PANIC): ")
    assert not pat.search("psql LOG:  duration: 581 ms  statement: COPY  audit_log ( id,egress_last_error ) FROM STDIN")
    assert pat.search('psql ERROR:  insert or update on table "x" violates foreign key constraint "x_fkey"')


# 2026-10-23 (operator): the semantic / knowledge-graph compiles hold
# AccessExclusive locks for their whole transaction; the live schema dump sat
# in a lock wait 10-20 min. Bounded wait before the test + a dump lock timeout.
def test_live_schema_dump_has_lock_wait_timeout():
    dump = [l for l in SRC.splitlines() if "pg_dump --schema-only" in l and "podman exec" in l]
    assert dump and all('--lock-wait-timeout="${PG_DUMP_LOCK_WAIT}"' in l for l in dump)
    assert ': "${PG_DUMP_LOCK_WAIT:=120s}"' in SRC


def test_compile_wait_is_bounded_and_runs_before_the_canary():
    assert "corporatetraveldc-semantic-compile-daily.service" in SRC
    assert "corporatetraveldc-knowledge-graph-compile.service" in SRC
    assert ': "${COMPILE_WAIT_MAX:=1800}"' in SRC
    assert 'systemctl --user is-active --quiet "${u}"' in SRC
    body = SRC[SRC.index("cmd_test() {"):]
    assert body.index("wait_for_compiles") < body.index('podman run "${run_args[@]}"')
    assert body.index("wait_for_compiles") < body.index("pg_dump --schema-only")
    assert "|| die \"RESULT: FAIL -- a compile unit is still active" in body


def _run_wait(tmp_path, active_polls: int, max_wait: int):
    """Run wait_for_compiles with a fake systemctl that reports the semantic
    compile active for the first `active_polls` checks."""
    fn = re.search(r"^wait_for_compiles\(\) \{.*?^\}$", SRC, re.S | re.M).group(0)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "n"
    counter.write_text("0")
    (bindir / "systemctl").write_text(
        "#!/usr/bin/env bash\n"
        f'case "$*" in *semantic-compile*) n=$(cat "{counter}"); echo $((n+1)) > "{counter}";'
        f' [[ $n -lt {active_polls} ]] && exit 0 || exit 3;; *) exit 3;; esac\n')
    (bindir / "systemctl").chmod(0o755)
    (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "sleep").chmod(0o755)
    script = (
        'log() { echo "[safe-pg-image-update] $*"; }\n'
        'COMPILE_UNITS="corporatetraveldc-semantic-compile-daily.service corporatetraveldc-knowledge-graph-compile.service"\n'
        f"COMPILE_WAIT_MAX={max_wait}; COMPILE_WAIT_POLL=30\n{fn}\nwait_for_compiles\n")
    import os
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)


def test_wait_returns_immediately_when_idle(tmp_path):
    r = _run_wait(tmp_path, active_polls=0, max_wait=1800)
    assert r.returncode == 0 and "waiting" not in r.stdout


def test_wait_logs_and_proceeds_once_compile_finishes(tmp_path):
    r = _run_wait(tmp_path, active_polls=3, max_wait=1800)
    assert r.returncode == 0
    assert r.stdout.count("waiting for active compile unit(s)") == 3
    assert "corporatetraveldc-semantic-compile-daily.service" in r.stdout
    assert "finished after 90s" in r.stdout


def test_wait_fails_at_the_bound_instead_of_hanging(tmp_path):
    r = _run_wait(tmp_path, active_polls=10_000, max_wait=120)
    assert r.returncode == 1
    assert "still active after 120s" in r.stdout
