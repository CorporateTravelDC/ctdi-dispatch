"""2026-11-01: pg_hba requires scram on the container's local socket, so the owner psql call
must pass POSTGRES_PASSWORD (as the backup and maintenance scripts do); never on argv as a value."""
from pathlib import Path
import importlib.util
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pg_ro_role", REPO / "scripts/pg-ro-role.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_owner_psql_passes_the_container_password_by_reference():
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw.get("input")
        return mock.Mock(returncode=0, stdout="ok\n", stderr="")

    with mock.patch.object(mod.subprocess, "run", fake_run):
        assert mod.psql("SELECT 1;") == "ok\n"
    script = seen["argv"][-1]
    assert 'PGPASSWORD="$POSTGRES_PASSWORD"' in script      # expanded inside the container
    assert seen["argv"][:3] == ["podman", "exec", "-i"]
    assert seen["input"] == "SELECT 1;"                      # SQL on stdin, not argv
