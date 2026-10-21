"""tests/common/test_no_platform_sqlite.py -- 2026-10-20 guard for the operator
decision of 2026-10-19: the only things on the box allowed to touch SQLite are
Pi-hole, vnstat and ACARS Hub. Every platform SQLite file is destroyed;
production and the demo run on Postgres only.

Held here (tree scans, no database, no socket):
  1. No module under src/ imports sqlite3 except the ACARS Hub reader
     (common/acars.py, ACARS Hub's own messages.db, read-only) and the three
     files that still carry the test-only SQLite code half (common/db.py,
     common/db_swim.py, second_brain/semantic/compile.py).
  2. sqlite3.connect() appears only in the ACARS Hub reader and in functions
     that check db_backend.sqlite_allowed_for_tests() first.
  3. Nothing outside tests/ sets CTDC_ALLOW_SQLITE_FOR_TESTS.
  4. backend() defaults to postgres and refuses sqlite without the flag;
     config.db_path() has no default; db_backend.ref_conn() stays deleted.
  5. No quadlet mounts a *-demo-source / *-demo-state dir, and no tracked
     quadlet or env file pins DISPATCH_DB_BACKEND=sqlite.

The remaining code half (SQLite SCHEMA + init_db_v* twins in common/db.py,
init_db_swim_* in common/db_swim.py, the SQLite test fixtures) is listed for
a post-freeze batch in docs/POSTGRES_MIGRATION.md §3.7; when it lands, shrink
ALLOWED_SQLITE_IMPORTS to the ACARS Hub reader alone.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

ACARS_HUB_READER = "common/acars.py"
ALLOWED_SQLITE_IMPORTS = {
    ACARS_HUB_READER,                       # ACARS Hub's own UI store, read-only (keep)
    "common/db.py",                         # test-flag path + SQLite twin migrators (post-freeze removal)
    "common/db_swim.py",                    # init_db_swim_* twins; no connect of its own
    "second_brain/semantic/compile.py",     # explicit test db_path, refused without the flag
}
FLAG = "CTDC_ALLOW_SQLITE_FOR_TESTS"


def _src_modules():
    for p in sorted(SRC.rglob("*.py")):
        if "retired-" in str(p) or "__pycache__" in p.parts:
            continue
        yield p.relative_to(SRC).as_posix(), p


def _imports_sqlite(tree: ast.AST) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "sqlite3" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "sqlite3":
            return True
    return False


def _connect_calls(tree: ast.AST):
    """(enclosing function node or None, call node) for every sqlite3.connect(...)."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "sqlite3"):
            fn = parents.get(n)
            while fn is not None and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = parents.get(fn)
            yield fn, n


def test_only_allowlisted_src_modules_import_sqlite3():
    offenders = [rel for rel, p in _src_modules() if _imports_sqlite(ast.parse(p.read_text(), str(p)))
                 and rel not in ALLOWED_SQLITE_IMPORTS]
    assert offenders == [], (
        f"{offenders} import sqlite3: platform SQLite is retired (operator 2026-10-19); only the "
        "ACARS Hub reader and the test-flag path may open SQLite")


def test_sqlite_connect_only_in_acars_hub_reader_or_behind_the_test_flag():
    seen = []
    for rel, p in _src_modules():
        for fn, call in _connect_calls(ast.parse(p.read_text(), str(p))):
            seen.append(rel)
            if rel == ACARS_HUB_READER:
                continue
            assert fn is not None, f"{rel}:{call.lineno}: module-level sqlite3.connect"
            body = ast.unparse(fn)
            assert "sqlite_allowed_for_tests" in body, (
                f"{rel}:{call.lineno} ({fn.name}): sqlite3.connect without the "
                f"db_backend.sqlite_allowed_for_tests() refusal")
    assert ACARS_HUB_READER in seen, "the ACARS Hub reader moved -- update this guard"
    assert set(seen) <= ALLOWED_SQLITE_IMPORTS


def test_acars_hub_reader_is_read_only():
    src = (SRC / ACARS_HUB_READER).read_text()
    assert "mode=ro" in src


def _sets_flag_py(tree: ast.AST) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == FLAG:
                    return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("setdefault", "setenv", "putenv", "update"):
            if any(isinstance(a, ast.Constant) and a.value == FLAG for a in n.args):
                return True
            if any(k.arg == FLAG for k in n.keywords):
                return True
        if isinstance(n, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == FLAG for k in n.keys):
            return True
    return False


_SHELL_SET = re.compile(rf"(^|[\s;])(export\s+)?{FLAG}=|Environment=.*{FLAG}|^\s*ENV\s+{FLAG}|-e\s+{FLAG}")


def test_nothing_outside_tests_sets_the_test_flag():
    offenders = []
    roots = [REPO / d for d in ("src", "scripts", "config", ".config", "systemd", "containers")]
    files = [p for r in roots if r.exists() for p in r.rglob("*") if p.is_file()]
    files += [p for p in REPO.glob("Containerfile*")]
    for p in files:
        if "retired-" in str(p) or "__pycache__" in p.parts:
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if FLAG not in text:
            continue
        if p.suffix == ".py":
            if _sets_flag_py(ast.parse(text, str(p))):
                offenders.append(str(p.relative_to(REPO)))
            continue
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if _SHELL_SET.search(line):
                offenders.append(f"{p.relative_to(REPO)}: {line.strip()}")
    assert offenders == [], offenders


def test_conftest_is_where_the_flag_is_set():
    assert _sets_flag_py(ast.parse((REPO / "tests/conftest.py").read_text()))


# -- backend() / db_path() / ref_conn() ---------------------------------------------

@pytest.fixture
def real_backend(monkeypatch):
    """tests/conftest.py pins db_backend.backend to a lambda; load the module's
    own function from source so the real default is what gets tested."""
    from common import db_backend
    import importlib.util
    spec = importlib.util.spec_from_file_location("db_backend_fresh", SRC / "common/db_backend.py")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return fresh, db_backend


def test_backend_defaults_to_postgres(real_backend, monkeypatch):
    fresh, _ = real_backend
    monkeypatch.delenv("DISPATCH_DB_BACKEND", raising=False)
    monkeypatch.delenv(FLAG, raising=False)
    assert fresh.backend() == "postgres"
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "  ")
    assert fresh.backend() == "postgres"
    monkeypatch.setenv("DISPATCH_DB_BACKEND", " Postgres ")
    assert fresh.backend() == "postgres"


def test_backend_refuses_sqlite_without_the_test_flag(real_backend, monkeypatch):
    fresh, _ = real_backend
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv("DISPATCH_DB_BACKEND", "sqlite")
    with pytest.raises(fresh.SqliteRetired, match="retired"):
        fresh.backend()
    for wrong in ("0", "true", "yes", ""):
        monkeypatch.setenv(FLAG, wrong)
        with pytest.raises(fresh.SqliteRetired):
            fresh.backend()
    monkeypatch.setenv(FLAG, "1")
    assert fresh.backend() == "sqlite"


def test_backend_refuses_unknown_values(real_backend, monkeypatch):
    fresh, _ = real_backend
    monkeypatch.setenv(FLAG, "1")
    for bad in ("sqlite3", "mysql", "pg"):
        monkeypatch.setenv("DISPATCH_DB_BACKEND", bad)
        with pytest.raises(fresh.SqliteRetired, match="not one of"):
            fresh.backend()


def test_db_open_connection_refuses_without_the_flag(monkeypatch, tmp_path):
    from common import db, db_backend
    monkeypatch.delenv(FLAG, raising=False)
    target = tmp_path / "would-be-created.db"
    monkeypatch.setattr(db, "_db_path", lambda: target)
    db.close_thread_connection()
    with pytest.raises(db_backend.SqliteRetired):
        db._open_connection()  # noqa: SLF001
    assert not target.exists(), "no SQLite file is created when refused"


def test_semantic_compile_test_path_refuses_without_the_flag(monkeypatch, tmp_path):
    from second_brain.semantic import compile as sc
    from common import db_backend
    monkeypatch.delenv(FLAG, raising=False)
    target = tmp_path / "idx.db"
    with pytest.raises(db_backend.SqliteRetired):
        with sc._connect(str(target)):  # noqa: SLF001
            pass
    assert not target.exists()


def test_db_path_has_no_default(monkeypatch):
    from common import config
    monkeypatch.delenv("DISPATCH_DB", raising=False)
    with pytest.raises(RuntimeError, match="no default"):
        config.db_path()
    monkeypatch.setenv("DISPATCH_DB", "/tmp/explicit.db")
    assert config.db_path() == "/tmp/explicit.db"
    src = (SRC / "common/config.py").read_text()
    assert '"/var/lib/corporatetraveldc/corporatetraveldc.db")' not in src


def test_ref_conn_stays_deleted():
    tree = ast.parse((SRC / "common/db_backend.py").read_text())
    defs = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert not defs & {"ref_conn", "_open_ref_connection", "close_ref_thread_connection",
                       "_guard_cross_engine", "_RefGuardedConnection"}
    assert not _imports_sqlite(tree), "db_backend no longer needs sqlite3 at all"


# -- quadlets and env files ----------------------------------------------------------

def _active(p: Path) -> list[str]:
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]


def _quadlets():
    for d in (REPO / ".config/containers/systemd", REPO / "systemd"):
        if d.exists():
            for p in sorted(d.rglob("*.container")):
                if "retired-" not in str(p):
                    yield p


def test_no_quadlet_mounts_a_demo_source_or_demo_state_dir():
    found = []
    for p in _quadlets():
        for l in _active(p):
            if l.startswith("Volume=") and re.search(r"-demo-(source|state)\b", l.split(":", 1)[0]):
                found.append(f"{p.name}: {l}")
    assert found == [], found


def test_no_tracked_unit_or_env_file_pins_sqlite():
    found = []
    files = list(_quadlets()) + sorted((REPO / "config").glob("*.env*"))
    for p in files:
        for l in _active(p):
            if re.fullmatch(r"(Environment=)?DISPATCH_DB_BACKEND=sqlite", l):
                found.append(f"{p.relative_to(REPO)}: {l}")
    assert found == [], found


def test_dispatch_env_files_carry_no_sqlite_path():
    for name in ("config/dispatch.env", "config/dispatch.env.example"):
        active = _active(REPO / name)
        assert not any(l.startswith("DISPATCH_DB=") for l in active), name
        assert "DISPATCH_DB_BACKEND=postgres" in active or name.endswith(".example"), name
