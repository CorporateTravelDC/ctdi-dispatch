"""tests/demo/test_demo_accessor_boundary.py -- 2026-10-16 demo/production
database split: who may name which Postgres accessor.

  pg_conn        the production pool
  demo_pg_conn   the demo instance's pool

AST scan (imports, attribute access and bare names -- not docstrings) over
src/ and scripts/:
  * src/demo/profiles.py and src/demo/demo_api.py never name pg_conn: the
    public demo has no code path to production;
  * scripts/scrub-demo-source.py is the ONLY file naming both: the one
    bridge, like push-public.sh for the public tree. common.db_backend
    defines both and is the only other file allowed to;
  * src/demo/recorder.py and src/demo/db.py (the trusted-side producers
    that write production's demo_snapshots) never name demo_pg_conn.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACCESSORS = {"pg_conn", "demo_pg_conn"}
DEFINER = REPO / "src/common/db_backend.py"
BRIDGE = REPO / "scripts/scrub-demo-source.py"


def names_used(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found |= {a.name for a in node.names if a.name in ACCESSORS}
        elif isinstance(node, ast.Attribute) and node.attr in ACCESSORS:
            found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in ACCESSORS:
            found.add(node.id)
    return found


def python_files() -> list[Path]:
    out = []
    for base in (REPO / "src", REPO / "scripts"):
        for p in base.rglob("*.py"):
            if "retired" in p.parts or "__pycache__" in p.parts:
                continue
            out.append(p)
    return sorted(out)


def test_public_demo_modules_never_name_the_production_accessor():
    for rel in ("src/demo/profiles.py", "src/demo/demo_api.py"):
        used = names_used(REPO / rel)
        assert "pg_conn" not in used, f"{rel} names pg_conn"


def test_only_the_bridge_and_the_definer_name_both_accessors():
    both = [p for p in python_files() if ACCESSORS <= names_used(p) and p != DEFINER]
    assert both == [BRIDGE], [str(p.relative_to(REPO)) for p in both]
    assert ACCESSORS <= names_used(BRIDGE)


def test_trusted_side_producers_never_name_the_demo_accessor():
    for rel in ("src/demo/recorder.py", "src/demo/db.py"):
        assert "demo_pg_conn" not in names_used(REPO / rel), rel


def test_common_db_names_neither_accessor():
    """common.db.conn() routes through db_backend.role_conn(); the choice of
    pool lives in db_backend, nowhere else."""
    assert not (names_used(REPO / "src/common/db.py") & ACCESSORS)
