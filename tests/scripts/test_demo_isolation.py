"""tests/scripts/test_demo_isolation.py -- 2026-10-16 demo/production database
split, relay 1: the tracked-file guards.

Operator rule (hard): the public demo is fed ONLY by a scrub from production
into a logically and physically separate database. These tests hold the
boundary in the tracked tree: quadlet mounts and env files, the production
hba rejects (before the catch-alls), the demo instance's own quadlet, the
backup exclusion, the boot order and rollout list, the sweep wiring, the env
examples, and scripts/demo-isolation-check.sh's logic against a fake podman.
Nothing here opens a socket or reads a secret value.
"""
from __future__ import annotations

import os
import stat
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUADLETS = REPO / ".config/containers/systemd"
USER_UNITS = REPO / ".config/systemd/user"
PROD_SOCK = "corporatetraveldc-pgsql-sock"
PROD_DATA = "corporatetraveldc-pgsql-data"
DEMO_SOCK = "corporatetraveldc-pgsql-demo-sock"
DEMO_DATA = "corporatetraveldc-pgsql-demo-data"
DEMO_APP = {"corporatetraveldc-demo-api", "corporatetraveldc-runner-demo"}
FORBIDDEN_ALWAYS = (PROD_SOCK, "dispatch-secrets.env", "/etc/corporatetraveldc/svc/")
PROD_ENV = "/etc/corporatetraveldc/dispatch.env"


def active(p: Path) -> list[str]:
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]


def demo_quadlets() -> list[Path]:
    out = []
    for p in sorted(QUADLETS.glob("*.container")):
        lines = active(p)
        if p.stem in DEMO_APP or "Environment=DISPATCH_ROLE=demo" in lines:
            out.append(p)
    return out


class DemoQuadlets(unittest.TestCase):
    def test_the_two_demo_app_quadlets_are_found(self):
        self.assertTrue(DEMO_APP <= {p.stem for p in demo_quadlets()})

    def test_no_demo_quadlet_has_production_access(self):
        """No production socket volume, no dispatch-secrets.env, no scoped
        svc/ file, no /var/lib/corporatetraveldc mount source. dispatch.env is
        forbidden once the quadlet declares DISPATCH_ROLE=demo (relay 2 moves
        both to config/demo.env); ~~until then the two named quadlets still load
        it legitimately -- relay 1 changes nothing demo-facing~~ (2026-10-19:
        relay 2 flipped both, so the clause below now binds them)."""
        for p in demo_quadlets():
            lines = active(p)
            with self.subTest(unit=p.name):
                for f in FORBIDDEN_ALWAYS:
                    self.assertFalse(any(f in l for l in lines), f"{p.name} carries {f}")
                for l in lines:
                    if l.startswith("Volume="):
                        src = l[len("Volume="):].split(":", 1)[0]
                        self.assertFalse(src == "/var/lib/corporatetraveldc" or src.startswith("/var/lib/corporatetraveldc/"),
                                         f"{p.name}: {l}")
                if "Environment=DISPATCH_ROLE=demo" in lines or any("/etc/corporatetraveldc/demo.env" in l for l in lines):
                    self.assertFalse(any(PROD_ENV in l for l in lines), f"{p.name}: demo role but still loads dispatch.env")

    def test_relay2_flip_both_demo_quadlets(self):
        """2026-10-19 relay 2: both demo app quadlets are demo-role processes on
        the demo instance's own socket volume, load demo-secrets.env then
        demo.env LAST, and never dispatch.env. ~~DEMO_SOURCE and
        DISPATCH_DB_BACKEND are NOT Environment= lines (podman -e beats
        --env-file, so the demo.env rollback switch would be dead)~~ --
        2026-10-20: the sqlite rollback is retired, so the backend (both) and
        the source (demo-api) ARE signed Environment= lines, postgres only."""
        for name in sorted(DEMO_APP):
            lines = active(QUADLETS / f"{name}.container")
            with self.subTest(unit=name):
                self.assertIn("Environment=DISPATCH_ROLE=demo", lines)
                self.assertIn("Environment=DISPATCH_DEMO_ISOLATION=1", lines)
                self.assertIn(f"Volume={DEMO_SOCK}:/var/run/postgresql-demo", lines)
                envfiles = [l for l in lines if l.startswith("EnvironmentFile=")]
                self.assertEqual(envfiles, ["EnvironmentFile=/etc/corporatetraveldc/demo-secrets.env",
                                            "EnvironmentFile=/etc/corporatetraveldc/demo.env"])
                self.assertFalse(any("dispatch.env" in l or "dispatch-secrets" in l for l in lines))
                self.assertFalse(any(l.startswith(("Environment=DEMO_PG_", "Environment=DISPATCH_PG_")) for l in lines))
                self.assertEqual([l for l in lines if l.startswith("Environment=DISPATCH_DB_BACKEND")],
                                 ["Environment=DISPATCH_DB_BACKEND=postgres"])
                self.assertFalse(any(l.startswith("Environment=DEMO_SOURCE") and l != "Environment=DEMO_SOURCE=postgres"
                                     for l in lines))
                vols = [l[len("Volume="):].split(":", 1)[0] for l in lines if l.startswith("Volume=")]
                self.assertFalse(any(v.startswith("/etc/") for v in vols), vols)
                self.assertTrue(any("corporatetraveldc-pgsql-demo.service" in l for l in lines if l.startswith("After=")))
        api = active(QUADLETS / "corporatetraveldc-demo-api.container")
        self.assertIn("Environment=DEMO_SOURCE=postgres", api)
        # ~~the rollback path: the sovereign file and demo_access.db stay mounted
        # until relay 4~~ -- 2026-10-20 (operator decision 2026-10-19, platform
        # SQLite retired): both sovereign mounts must be ABSENT, and demo-api
        # mounts no host directory at all (only the demo socket volume).
        for name in sorted(DEMO_APP):
            lines = active(QUADLETS / f"{name}.container")
            with self.subTest(unit=name, check="no sovereign mounts"):
                self.assertFalse(any("corporatetraveldc-demo-source" in l or "corporatetraveldc-demo-state" in l
                                     for l in lines if l.startswith("Volume=")), name)
        self.assertEqual([l for l in api if l.startswith("Volume=")],
                         [f"Volume={DEMO_SOCK}:/var/run/postgresql-demo"])

    def test_no_non_demo_unit_mounts_the_demo_socket(self):
        """The recorder (corporatetraveldc-demo) and every other production
        unit stay off the demo instance: the only writer is the host-side
        scrub, which is not a container."""
        demo = {p.stem for p in demo_quadlets()} | {"corporatetraveldc-pgsql-demo"}
        for p in sorted(QUADLETS.glob("*.container")):
            if p.stem in demo:
                continue
            with self.subTest(unit=p.name):
                self.assertFalse(any(DEMO_SOCK in l for l in active(p)), f"{p.name} mounts {DEMO_SOCK}")

    def test_recorder_is_not_a_demo_unit(self):
        rec = active(QUADLETS / "corporatetraveldc-demo.container")
        self.assertNotIn("Environment=DISPATCH_ROLE=demo", rec)
        self.assertFalse(any(DEMO_SOCK in l for l in rec))


class DemoInstanceQuadlet(unittest.TestCase):
    Q = QUADLETS / "corporatetraveldc-pgsql-demo.container"
    P = QUADLETS / "corporatetraveldc-pgsql.container"

    def test_exists_with_its_own_volumes_and_identity(self):
        lines = active(self.Q)
        self.assertIn("Environment=POSTGRES_DB=corporatetraveldc_demo", lines)
        self.assertIn("Environment=POSTGRES_USER=demo_owner", lines)
        self.assertIn("EnvironmentFile=/etc/corporatetraveldc/pgsql-demo-secrets.env", lines)
        self.assertIn(f"Volume={DEMO_DATA}:/var/lib/postgresql/data", lines)
        self.assertIn(f"Volume={DEMO_SOCK}:/var/run/postgresql", lines)
        self.assertIn("PublishPort=127.0.0.1:5433:5432", lines)
        self.assertTrue(any(l.startswith("Memory=512m") for l in lines))
        self.assertIn("ContainerName=corporatetraveldc-pgsql-demo", lines)
        self.assertTrue(any("config/postgresql-demo.conf:" in l for l in lines))
        self.assertTrue(any("config/pg_hba-demo.conf:" in l for l in lines))

    def test_never_touches_the_production_volumes_or_secrets(self):
        text = "\n".join(active(self.Q))
        for bad in (PROD_SOCK, PROD_DATA, "/etc/corporatetraveldc/pgsql-secrets.env", "dispatch-secrets.env",
                    "/etc/corporatetraveldc/dispatch.env", "POSTGRES_USER=dispatch", "POSTGRES_DB=corporatetraveldc\n"):
            self.assertNotIn(bad, text + "\n", bad)

    def test_same_image_hardening_and_no_wanted_by(self):
        prod, demo = active(self.P), active(self.Q)
        img = [l for l in prod if l.startswith("Image=")]
        self.assertEqual([l for l in demo if l.startswith("Image=")], img, "same image line as the production instance")
        for key in ("Slice=production.slice", "NoNewPrivileges=true", "AutoUpdate=registry", "Network=pasta:--map-gw",
                    "Restart=always", "HealthInterval=30s"):
            self.assertIn(key, demo, key)
        self.assertFalse(any(l.startswith("WantedBy=") for l in demo))
        self.assertIn("HealthCmd=pg_isready -U demo_owner -d corporatetraveldc_demo", demo)

    def test_demo_config_files(self):
        conf = (REPO / "config/postgresql-demo.conf").read_text()
        self.assertIn("shared_buffers = 128MB", conf)
        self.assertIn("max_connections = 30", conf)
        self.assertIn("hba_file = '/etc/postgresql/pg_hba.conf'", conf)
        hba = [l.split() for l in active(REPO / "config/pg_hba-demo.conf")]
        self.assertEqual(hba[0][:3], ["local", "corporatetraveldc_demo", "demo"])
        self.assertEqual(hba[1][:3], ["local", "corporatetraveldc_demo", "demo_owner"])
        self.assertEqual(hba[2][:4], ["host", "corporatetraveldc_demo", "demo_owner", "all"])
        # 2026-10-17: first-boot init creates the database from `postgres` as the owner, on the socket
        self.assertEqual(hba[3], ["local", "postgres", "demo_owner", "scram-sha-256"])
        self.assertEqual(hba[4], ["local", "all", "all", "reject"])
        self.assertEqual(hba[5], ["host", "all", "all", "all", "reject"])
        self.assertTrue(all(r[-1] == "scram-sha-256" for r in hba[:4]))
        self.assertFalse(any("demo" == r[2] and r[0] == "host" for r in hba), "the demo role never over TCP")


class ProductionHba(unittest.TestCase):
    def test_demo_rejects_precede_the_catch_alls(self):
        rows = [l.split() for l in active(REPO / "config/pg_hba.conf")]
        first_catch_all = next(i for i, r in enumerate(rows) if r[1] == "all" and r[2] == "all")
        rejects = [(i, r) for i, r in enumerate(rows) if r[-1] == "reject"]
        self.assertTrue(rejects, "no reject rows")
        self.assertTrue(all(i < first_catch_all for i, _ in rejects), "a reject row sits after a catch-all (never matched)")
        shapes = {tuple(r[:3]) for _, r in rejects}
        self.assertIn(("local", "all", "demo"), shapes)
        self.assertIn(("host", "all", "demo"), shapes)
        self.assertIn(("local", "corporatetraveldc_demo", "all"), shapes)
        self.assertTrue(all(r[-1] == "scram-sha-256" for r in rows if r[-1] != "reject"))


class Wiring(unittest.TestCase):
    def test_boot_order_starts_pgsql_demo_before_demo_api(self):
        text = (REPO / "scripts/stack-boot-ctl.sh").read_text()
        self.assertIn('[pgsql-demo]="${UNIT_PREFIX}pgsql-demo"', text)
        order = text[text.index("ORDER=("):]
        order = order[:order.index("\n)")]
        names = [l.strip().split(":")[0] for l in order.splitlines()[1:] if l.strip() and not l.strip().startswith("#")]
        self.assertIn("pgsql-demo", names)
        self.assertLess(names.index("pgsql-demo"), names.index("demo-api"))
        self.assertLess(names.index("pgsql"), names.index("pgsql-demo"))

    def test_rollout_list_carries_the_demo_instance_as_a_postgres_external(self):
        so = (REPO / "scripts/serialized-rollout.sh").read_text()
        self.assertIn('"corporatetraveldc-pgsql-demo|ext|docker.io/library/postgres:16-alpine|||30"', so)
        # stack-refresh.sh derives its unit list from serialized-rollout.sh (rollout_rows) and keys the
        # canary-only rule on the postgres image, so the row above covers it.
        sr = (REPO / "scripts/stack-refresh.sh").read_text()
        self.assertIn("rollout_rows()", sr)
        self.assertIn('PG_IMAGES=("docker.io/library/postgres:16-alpine")', sr)

    def test_backup_excludes_the_demo_instance_and_says_so(self):
        bk = (REPO / "scripts/backup/ctdi-backup.sh").read_text()
        self.assertIn("BACKUP_EXCLUDED_INSTANCES=(corporatetraveldc-pgsql-demo)", bk)
        self.assertIn("rebuildable", bk.lower())
        self.assertNotIn('pg_dump_in "corporatetraveldc-pgsql-demo"', bk)
        self.assertNotIn("DEMO_PG=", bk)
        doc = (REPO / "docs/BACKUP.md").read_text()
        self.assertIn("corporatetraveldc-pgsql-demo", doc)

    def test_sweep_runs_the_live_check_and_pages_p4(self):
        sweep = (REPO / "scripts/scheduled-integrity-sweep.sh").read_text()
        self.assertIn('scripts/demo-isolation-check.sh" --quiet', sweep)
        i = sweep.index('ntfy_send "DEMO ISOLATION CHECK FAILED"')
        self.assertRegex(sweep[i:i + 400], r'\n\s+4\n')
        self.assertIn("${demo_iso_rc} -eq 0", sweep)
        self.assertIn("DEMO ISOLATION CHECK FAILED", (REPO / "docs/ALERT_REFERENCE.md").read_text())

    def test_refresh_service_runs_the_scrub_with_the_demo_owner_file_only(self):
        svc = active(USER_UNITS / "corporatetraveldc-demo-source-refresh.service")
        self.assertIn("EnvironmentFile=/etc/corporatetraveldc/pgsql-demo-secrets.env", svc)
        self.assertIn("Environment=DEMO_PG_USER=demo_owner", svc)
        self.assertTrue(any(l.startswith("ExecStart=") and l.endswith("scripts/scrub-demo-source.py --refresh --workers 1") for l in svc))
        self.assertFalse(any("pgsql-secrets.env" in l and "pgsql-demo-secrets" not in l for l in svc))
        timer = active(USER_UNITS / "corporatetraveldc-demo-source-refresh.timer")
        self.assertIn("OnCalendar=*-*-* 03:17:00 UTC", timer)   # 2026-10-19 relay 3 (was 04:45 America/New_York)
        self.assertFalse(any(l.startswith(("Requires=", "Wants=")) for l in timer))

    def test_env_examples(self):
        demo = (REPO / "config/demo.env.example").read_text()
        for key in ("DISPATCH_ROLE=demo", "DEMO_PG_HOST=/var/run/postgresql-demo", "DEMO_PG_DB=corporatetraveldc_demo",
                    "DEMO_PG_USER=demo", "DISPATCH_DEMO_ISOLATION=1"):
            self.assertIn("\n" + key, demo, key)
        self.assertNotIn("DISPATCH_PG_PASSWORD", demo)
        self.assertNotIn("\nDISPATCH_PG_", demo)
        # 2026-10-19 relay 2: the demo role's password lives in demo.env; 2026-10-20: the
        # backend is a postgres constant and the DEMO_SOURCE rollback line is gone
        self.assertNotIn("\nDEMO_SOURCE=", demo)
        self.assertNotIn("\nDISPATCH_DB_BACKEND=sqlite", demo)
        for key in ("DEMO_PG_PASSWORD=CHANGE_ME", "DISPATCH_DB_BACKEND=postgres", "LLAMA_BASE_URL=CHANGE_ME",
                    "OLLAMA_CHAT_MODEL=CHANGE_ME"):
            self.assertIn("\n" + key, demo, key)
        for bad in ("\nULTRAFEEDER_URL", "\nACARSHUB_URL", "\nNTFY_TOKEN", "\nPOSTGRES_PASSWORD"):
            self.assertNotIn(bad, demo, bad)
        secrets = (REPO / "config/demo-secrets.env.example").read_text()
        self.assertNotIn("\nDEMO_PG_PASSWORD=", secrets)   # moved to demo.env (relay 2)
        self.assertNotIn("DISPATCH_PG_PASSWORD=", secrets)
        self.assertNotIn("\nDISPATCH_DB_BACKEND", secrets)   # ~~harmless default under demo.env~~ removed 2026-10-20
        pg = (REPO / "config/pgsql-demo-secrets.env.example").read_text()
        keys = [l.split("=", 1)[0] for l in pg.splitlines() if l and not l.startswith("#")]
        self.assertEqual(keys, ["POSTGRES_PASSWORD"])
        self.assertIn("differ from the production", pg)
        for f in ("demo.env.example", "demo-secrets.env.example", "pgsql-demo-secrets.env.example"):
            for l in (REPO / "config" / f).read_text().splitlines():
                if l and not l.startswith("#") and "=" in l:
                    self.assertNotIn('"', l.split("=", 1)[1]); self.assertNotIn("'", l.split("=", 1)[1])


# -- scripts/demo-isolation-check.sh against a fake podman ----------------------

FAKE_PODMAN = r'''#!/usr/bin/env bash
# fake podman for tests: answers ps / inspect / exec from the FAKE_* env.
cmd="$1"; shift
case "$cmd" in
  ps) printf '%s\n' $FAKE_RUNNING ;;
  inspect)
    name="$1"; fmt="$3"
    base="${name#systemd-}"
    if [[ "$fmt" == *Mounts* ]]; then
      var="FAKE_MOUNTS_${base//-/_}"; printf '%b' "${!var:-}"
    else
      var="FAKE_ENV_${base//-/_}"; printf '%b' "${!var:-}"
    fi ;;
  exec)
    # exec -i <container> sh -c '<script>' sh "<sql>"
    container="$2"; sql="${@: -1}"
    case "$sql" in
      *"/etc/corporatetraveldc/dispatch"*) [[ -n "${FAKE_ETC_VISIBLE:-}" ]] && exit 1; exit 0 ;;
      *"0002_demo_grants"*) echo "${FAKE_DEMO_0002-1}" ;;
      *role_table_grants*) printf '%s\n' ${FAKE_DEMO_GRANTS:-} ;;
      *rolbypassrls*) echo "${FAKE_DEMO_ATTRS:-false,false,false}" ;;
      *pg_stat_activity*) echo "${FAKE_PROD_SESSIONS:-0}" ;;
      *"rolname IN ('demo', 'demo_owner')"*) echo "${FAKE_PROD_DEMO_ROLES:-0}" ;;
      *rolsuper*) echo "${FAKE_DEMO_SUPERS:-}" ;;
      *"rolname = 'dispatch'"*) echo "${FAKE_DEMO_DISPATCH:-0}" ;;
      *pg_tables*) printf '%s\n' ${FAKE_DEMO_TABLES:-} ;;
      *) echo "" ;;
    esac ;;
  *) exit 0 ;;
esac
'''


def run_check(tmp_path: Path, **env) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True)
    fake = bindir / "podman"; fake.write_text(FAKE_PODMAN); fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    e = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    e.update({k: v for k, v in env.items()})
    return subprocess.run(["bash", str(REPO / "scripts/demo-isolation-check.sh")], capture_output=True, text=True, env=e)


CLEAN = dict(
    FAKE_RUNNING="corporatetraveldc-pgsql corporatetraveldc-pgsql-demo systemd-corporatetraveldc-demo-api systemd-corporatetraveldc-web",
    FAKE_MOUNTS_corporatetraveldc_demo_api="- /var/lib/corporatetraveldc-demo-source /var/lib/corporatetraveldc-demo-source\ncorporatetraveldc-pgsql-demo-sock /home/x/.local/share/containers/storage/volumes/corporatetraveldc-pgsql-demo-sock/_data /var/run/postgresql-demo\n",
    FAKE_ENV_corporatetraveldc_demo_api="DISPATCH_ROLE=demo\nDEMO_PG_USER=demo\nDEMO_PG_PASSWORD=not-a-real-value\n",
    FAKE_MOUNTS_corporatetraveldc_web="corporatetraveldc-pgsql-sock /x /var/run/postgresql\n- /var/lib/corporatetraveldc /var/lib/corporatetraveldc\n",
    FAKE_ENV_corporatetraveldc_web="DISPATCH_PG_PASSWORD=not-a-real-value\n",
    FAKE_PROD_SESSIONS="0", FAKE_PROD_DEMO_ROLES="0", FAKE_DEMO_SUPERS="", FAKE_DEMO_DISPATCH="0",
    FAKE_DEMO_TABLES="snapshots brief_archive meta demo_profiles chat_messages scrub_ledger schema_migrations",
    # 2026-10-19: the audited grant set after 0002 (unsorted on purpose: the check sorts)
    FAKE_DEMO_GRANTS="snapshots:SELECT meta:SELECT brief_archive:SELECT scrub_ledger:SELECT demo_profiles:SELECT "
                     "demo_profiles:INSERT demo_profiles:UPDATE chat_messages:SELECT chat_messages:INSERT chat_messages:DELETE",
)


class LiveCheckLogic(unittest.TestCase):
    def test_clean_system_passes_and_prints_no_values(self):
        r = run_check(Path(self._tmp()), **CLEAN)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("clean", r.stdout)
        self.assertNotIn("not-a-real-value", r.stdout + r.stderr)

    def test_demo_container_with_production_socket_fails(self):
        env = dict(CLEAN, FAKE_MOUNTS_corporatetraveldc_demo_api="corporatetraveldc-pgsql-sock /x /var/run/postgresql\n")
        r = run_check(Path(self._tmp()), **env)
        self.assertEqual(r.returncode, 1); self.assertIn("production Postgres socket", r.stdout)

    def test_demo_container_with_live_tree_or_svc_or_prod_password_fails(self):
        env = dict(CLEAN, FAKE_MOUNTS_corporatetraveldc_demo_api="- /var/lib/corporatetraveldc /var/lib/corporatetraveldc\n- /etc/corporatetraveldc/svc /svc\n",
                   FAKE_ENV_corporatetraveldc_demo_api="DISPATCH_ROLE=demo\nDISPATCH_PG_PASSWORD=not-a-real-value\n")
        r = run_check(Path(self._tmp()), **env)
        self.assertEqual(r.returncode, 1)
        for s in ("live state tree", "scoped secret directory", "DISPATCH_PG_PASSWORD"):
            self.assertIn(s, r.stdout)
        self.assertNotIn("not-a-real-value", r.stdout + r.stderr)

    def test_role_detected_by_env_not_only_by_name(self):
        env = dict(CLEAN, FAKE_RUNNING=CLEAN["FAKE_RUNNING"] + " systemd-corporatetraveldc-other",
                   FAKE_MOUNTS_corporatetraveldc_other="corporatetraveldc-pgsql-sock /x /var/run/postgresql\n",
                   FAKE_ENV_corporatetraveldc_other="DISPATCH_ROLE=demo\n")
        r = run_check(Path(self._tmp()), **env)
        self.assertEqual(r.returncode, 1); self.assertIn("corporatetraveldc-other mounts the production", r.stdout)

    def test_production_instance_findings(self):
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_PROD_SESSIONS="2"))
        self.assertEqual(r.returncode, 1); self.assertIn("demo application name on the PRODUCTION", r.stdout)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_PROD_DEMO_ROLES="1"))
        self.assertEqual(r.returncode, 1); self.assertIn("demo role exists on the PRODUCTION", r.stdout)

    def test_demo_instance_findings_and_not_running_is_info(self):
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_DEMO_SUPERS="postgres"))
        self.assertEqual(r.returncode, 1); self.assertIn("superuser role(s) other than demo_owner", r.stdout)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_DEMO_TABLES=CLEAN["FAKE_DEMO_TABLES"] + " flight_events"))
        self.assertEqual(r.returncode, 1); self.assertIn("outside the demo set: flight_events", r.stdout)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_DEMO_DISPATCH="1"))
        self.assertEqual(r.returncode, 1); self.assertIn("'dispatch' role exists on the demo", r.stdout)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_RUNNING="corporatetraveldc-pgsql systemd-corporatetraveldc-web"))
        self.assertEqual(r.returncode, 0, r.stdout); self.assertIn("not running (relay 1", r.stdout)

    def test_flipped_container_with_production_names_or_files_fails(self):
        """2026-10-19 relay 2: a DISPATCH_ROLE=demo container carrying any
        DISPATCH_PG_* name (dispatch.env loaded), mounting /etc/corporatetraveldc,
        or seeing dispatch.env inside is a finding; names only, never values."""
        env = dict(CLEAN, FAKE_ENV_corporatetraveldc_demo_api="DISPATCH_ROLE=demo\nDISPATCH_PG_DB=x\nDISPATCH_PG_USER=not-a-real-value\n")
        r = run_check(Path(self._tmp()), **env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("DISPATCH_PG_DB DISPATCH_PG_USER", r.stdout)
        self.assertNotIn("not-a-real-value", r.stdout + r.stderr)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_ETC_VISIBLE="1"))
        self.assertEqual(r.returncode, 1); self.assertIn("visible inside the container", r.stdout)
        env = dict(CLEAN, FAKE_MOUNTS_corporatetraveldc_demo_api="- /etc/corporatetraveldc/demo.env /etc/x\n")
        r = run_check(Path(self._tmp()), **env)
        self.assertEqual(r.returncode, 1); self.assertIn("mounts from /etc/corporatetraveldc", r.stdout)
        # an UNflipped container (no DISPATCH_ROLE) is not held to 1b
        env = dict(CLEAN, FAKE_ENV_corporatetraveldc_demo_api="DISPATCH_PG_DB=x\n", FAKE_ETC_VISIBLE="1")
        r = run_check(Path(self._tmp()), **env)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_demo_role_grants_compared_after_0002(self):
        r = run_check(Path(self._tmp()), **CLEAN)
        self.assertEqual(r.returncode, 0, r.stdout); self.assertIn("demo role grants match the audited set", r.stdout)
        extra = dict(CLEAN, FAKE_DEMO_GRANTS=CLEAN["FAKE_DEMO_GRANTS"] + " snapshots:INSERT")
        r = run_check(Path(self._tmp()), **extra)
        self.assertEqual(r.returncode, 1); self.assertIn("grants differ from the audited set", r.stdout)
        missing = dict(CLEAN, FAKE_DEMO_GRANTS="snapshots:SELECT")
        self.assertEqual(run_check(Path(self._tmp()), **missing).returncode, 1)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_DEMO_ATTRS="false,false,true"))
        self.assertEqual(r.returncode, 1); self.assertIn("TEMP", r.stdout)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_DEMO_0002="0", FAKE_DEMO_GRANTS="snapshots:INSERT"))
        self.assertEqual(r.returncode, 0, r.stdout); self.assertIn("not applied yet", r.stdout)
        r = run_check(Path(self._tmp()), **dict(CLEAN, FAKE_DEMO_0002=""))
        self.assertEqual(r.returncode, 1); self.assertIn("could not read schema_migrations", r.stdout)

    def _tmp(self) -> str:
        import tempfile
        return tempfile.mkdtemp()


if __name__ == "__main__":
    unittest.main()
