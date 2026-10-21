"""tests/scripts/test_demo_quadlets_isolation.py -- 2026-10-16: no public-demo
quadlet mounts the production Postgres socket, and the two demo app
containers declare DISPATCH_DEMO_ISOLATION=1.

Verified live 2026-10-16 12:13Z: corporatetraveldc-demo-api and
corporatetraveldc-runner-demo both mounted `corporatetraveldc-pgsql-sock`
(the production instance's socket; one instance, one db, one superuser
role) while loading dispatch.env's production DISPATCH_PG_DB/USER.

The two trusted-side PRODUCERS (the recorder corporatetraveldc-demo and
corporatetraveldc-second-brain-demo-archiver-daily) write the private
staging archive demo_snapshots to Postgres and serve nothing publicly; they
keep the socket for now and are the documented follow-up (a demo-owned
Postgres instance and role, operator decision) -- listed explicitly so a
new demo-named quadlet cannot quietly join them.
"""
from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUADLETS = REPO / ".config/containers/systemd"
PG_SOCK_VOLUME = "corporatetraveldc-pgsql-sock"
TRUSTED_SIDE_PRODUCERS = {
    "corporatetraveldc-demo",                                # recorder (writes demo_snapshots)
    "corporatetraveldc-second-brain-demo-archiver-daily",    # archiver (reads/writes demo_snapshots)
}
DEMO_APP_CONTAINERS = {"corporatetraveldc-demo-api", "corporatetraveldc-runner-demo"}


def _active_lines(p: Path) -> list[str]:
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]


class DemoQuadletsIsolation(unittest.TestCase):
    def test_demo_quadlets_exist(self):
        names = {p.stem for p in QUADLETS.glob("*demo*.container")}
        self.assertTrue(DEMO_APP_CONTAINERS <= names, names)
        self.assertTrue(TRUSTED_SIDE_PRODUCERS <= names, names)

    def test_no_public_demo_quadlet_mounts_the_production_pg_socket(self):
        for p in sorted(QUADLETS.glob("*demo*.container")):
            if p.stem in TRUSTED_SIDE_PRODUCERS:
                continue
            with self.subTest(unit=p.name):
                mounts = [l for l in _active_lines(p) if l.startswith("Volume=") and PG_SOCK_VOLUME in l]
                self.assertEqual(mounts, [], f"{p.name} mounts the production Postgres socket")

    def test_no_public_demo_quadlet_mounts_the_live_state_dir(self):
        """F6 (2026-08-14): the public demo has no filesystem path to
        /var/lib/corporatetraveldc. runner-demo mounts a demo-only sibling
        AT that container path, which is the fix, not a regression."""
        for p in sorted(QUADLETS.glob("*demo*.container")):
            if p.stem in TRUSTED_SIDE_PRODUCERS:
                continue
            with self.subTest(unit=p.name):
                for l in _active_lines(p):
                    if l.startswith("Volume="):
                        host = l[len("Volume="):].split(":", 1)[0]
                        self.assertFalse(host == "/var/lib/corporatetraveldc" or host.startswith("/var/lib/corporatetraveldc/"),
                                         f"{p.name}: {l}")

    def test_demo_app_containers_declare_isolation(self):
        for name in sorted(DEMO_APP_CONTAINERS):
            p = QUADLETS / f"{name}.container"
            with self.subTest(unit=p.name):
                self.assertIn("Environment=DISPATCH_DEMO_ISOLATION=1", _active_lines(p))

    def test_example_env_files_carry_the_switches(self):
        dispatch = (REPO / "config/dispatch.env.example").read_text()
        self.assertIn("DISPATCH_DEMO_ISOLATION", dispatch)
        # never active in the shared file: it would make production refuse itself
        self.assertNotIn("\nDISPATCH_DEMO_ISOLATION=1", dispatch)
        demo = (REPO / "config/demo-secrets.env.example").read_text()
        # ~~assertIn DISPATCH_DB_BACKEND=sqlite~~ -- 2026-10-20: the sqlite pin is
        # gone (platform SQLite retired); the quadlets set postgres themselves.
        self.assertNotIn("\nDISPATCH_DB_BACKEND=sqlite", demo)
        for name in sorted(DEMO_APP_CONTAINERS):
            self.assertIn("Environment=DISPATCH_DB_BACKEND=postgres", _active_lines(QUADLETS / f"{name}.container"))


if __name__ == "__main__":
    unittest.main()
