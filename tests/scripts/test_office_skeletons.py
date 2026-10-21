"""
tests/scripts/test_office_skeletons.py

(operator 2026-10-20) Office skeletons for the CFO, Executive Assistant and
Chief of Staff profiles: Firefly III, Paperless-ngx, Valkey, optional Vikunja,
on a third Postgres instance (corporatetraveldc-pgsql-office). TEMPLATES,
disabled until after the feature freeze. What these tests hold:

  * every skeleton quadlet: no [Install] section, a pinned tag (no :latest, no
    AutoUpdate), its own /etc/corporatetraveldc/<svc>-secrets.env (never the
    platform env files), a memory cap, Slice=production.slice,
    NoNewPrivileges, the office networks only, tailnet-only publishes;
  * no SQLite anywhere: Paperless PAPERLESS_DBENGINE=postgresql, Vikunja
    VIKUNJA_DATABASE_TYPE=postgres, Firefly DB_CONNECTION=pgsql, set in the
    tracked quadlet and never overridable from a secrets example;
  * absent from every boot / stack / rollout / backup script and every timer;
  * the office instance's hba, config and init SQL; production hba rejects the
    office roles by name;
  * scripts/office-skeleton-ctl.py: fill generates and is rerun-safe, check
    finds the bad states, nothing secret is printed or put on argv;
  * the split profiles, their glossary slices and the crossover composition.
Offline; never touches /etc, podman or the network.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKEL = REPO / ".config/containers/systemd/office-skeletons"
QUADLETS = REPO / ".config/containers/systemd"
UNITS = {
    "corporatetraveldc-pgsql-office": "pgsql-office",
    "corporatetraveldc-valkey-office": "valkey-office",
    "corporatetraveldc-firefly": "firefly",
    "corporatetraveldc-paperless": "paperless",
    "corporatetraveldc-vikunja": "vikunja",
}
BACKEND = {"corporatetraveldc-pgsql-office", "corporatetraveldc-valkey-office"}
TAILNET = "100.x.x.x"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def active(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]


def keys(path: Path, key: str) -> list[str]:
    return [l.split("=", 1)[1] for l in active(path) if l.startswith(key + "=")]


def env_of(path: Path) -> dict[str, str]:
    return dict(v.split("=", 1) for v in keys(path, "Environment"))


# ------------------------------------------------------------------ quadlets
def test_the_skeleton_set_is_exactly_these_files():
    names = sorted(p.name for p in SKEL.iterdir())
    assert names == sorted([f"{u}.container" for u in UNITS] + ["office-db.network", "office-web.network"])


@pytest.mark.parametrize("unit", sorted(UNITS))
def test_no_install_section_and_no_boot_hooks(unit):
    lines = active(SKEL / f"{unit}.container")
    assert "[Install]" not in lines
    assert not any(l.startswith(("WantedBy=", "RequiredBy=", "UpheldBy=", "Alias=")) for l in lines)
    for net in ("office-db.network", "office-web.network"):
        assert "[Install]" not in active(SKEL / net)


@pytest.mark.parametrize("unit", sorted(UNITS))
def test_image_is_pinned_and_never_auto_updates(unit):
    q = SKEL / f"{unit}.container"
    (image,) = keys(q, "Image")
    assert image.startswith("docker.io/") and not image.startswith("localhost/")
    repo_part, _, tag = image.rpartition(":")
    assert repo_part and tag and "/" not in tag, image
    assert tag != "latest" and not tag.startswith(("latest", "develop", "dev", "unstable", "nightly")), image
    assert not keys(q, "AutoUpdate"), "a dormant template never changes underneath"


def test_pins_are_the_verified_tags():
    images = {u: keys(SKEL / f"{u}.container", "Image")[0] for u in UNITS}
    assert images == {
        "corporatetraveldc-pgsql-office": "docker.io/library/postgres:16-alpine",   # the production line (canary path)
        "corporatetraveldc-valkey-office": "docker.io/valkey/valkey:8.1.10-alpine",
        "corporatetraveldc-firefly": "docker.io/fireflyiii/core:version-6.7.9",
        "corporatetraveldc-paperless": "docker.io/paperlessngx/paperless-ngx:3.3.3",
        "corporatetraveldc-vikunja": "docker.io/vikunja/vikunja:2.7.0",
    }


@pytest.mark.parametrize("unit,svc", sorted(UNITS.items()))
def test_own_secrets_file_only(unit, svc):
    files = keys(SKEL / f"{unit}.container", "EnvironmentFile")
    assert files == [f"/etc/corporatetraveldc/{svc}-secrets.env"]
    assert (REPO / f"config/{svc}-secrets.env.example").is_file()
    text = (SKEL / f"{unit}.container").read_text()
    for forbidden in ("dispatch.env", "dispatch-secrets.env", "/svc/", "pgsql-secrets.env", "pgsql-demo-secrets.env",
                      "demo.env", "demo-secrets.env"):
        assert not any(forbidden in f for f in files), forbidden
    assert not re.search(r"^EnvironmentFile=.*dispatch", text, re.M)


@pytest.mark.parametrize("unit", sorted(UNITS))
def test_resource_caps_slice_and_hardening(unit):
    q = SKEL / f"{unit}.container"
    assert re.fullmatch(r"\d+m", keys(q, "Memory")[0])
    assert keys(q, "Slice") == ["production.slice"]
    assert keys(q, "NoNewPrivileges") == ["true"]
    assert keys(q, "CPUQuota"), "a CPU cap on every skeleton unit"
    assert keys(q, "ContainerName") == [unit]


def test_networks_backend_internal_and_publishes_tailnet_only():
    db = active(SKEL / "office-db.network")
    assert "Internal=true" in db
    assert "Internal=true" not in active(SKEL / "office-web.network")
    for unit in UNITS:
        q = SKEL / f"{unit}.container"
        nets = keys(q, "Network")
        ports = keys(q, "PublishPort")
        if unit in BACKEND:
            assert nets == ["office-db.network"], unit
            assert ports == [], f"{unit} is never published"
        else:
            assert nets == ["office-db.network", "office-web.network"], unit
            assert len(ports) == 1 and ports[0].startswith(TAILNET + ":"), unit
    published = [keys(SKEL / f"{u}.container", "PublishPort")[0].split(":")[1] for u in UNITS if u not in BACKEND]
    assert sorted(published) == ["8101", "8102", "8103"]


def test_no_live_quadlet_joins_or_names_the_office_stack():
    for p in sorted(QUADLETS.glob("*.container")):
        text = p.read_text()
        assert "office-db" not in text and "office-web" not in text, p.name
        assert not any(u in text for u in UNITS), p.name
    assert not any(p.stem in UNITS for p in QUADLETS.glob("*.container")), "skeletons live in the subdirectory only"


# ------------------------------------------------------------------ no SQLite
def test_every_app_pins_postgresql_in_the_tracked_quadlet():
    assert env_of(SKEL / "corporatetraveldc-paperless.container")["PAPERLESS_DBENGINE"] == "postgresql"
    assert env_of(SKEL / "corporatetraveldc-vikunja.container")["VIKUNJA_DATABASE_TYPE"] == "postgres"
    assert env_of(SKEL / "corporatetraveldc-firefly.container")["DB_CONNECTION"] == "pgsql"
    for unit, k in (("corporatetraveldc-paperless", "PAPERLESS_DBHOST"), ("corporatetraveldc-vikunja", "VIKUNJA_DATABASE_HOST"),
                    ("corporatetraveldc-firefly", "DB_HOST")):
        assert env_of(SKEL / f"{unit}.container")[k] == "corporatetraveldc-pgsql-office"


@pytest.mark.parametrize("unit", sorted(UNITS))
def test_no_sqlite_setting_anywhere(unit):
    q = SKEL / f"{unit}.container"
    for line in active(q):
        if line.startswith(("Environment=", "Exec=", "Volume=")):
            assert "sqlite" not in line.lower(), line
    assert "VIKUNJA_DATABASE_PATH" not in env_of(q) and "DB_SOCKET" not in env_of(q)


def test_secrets_examples_cannot_override_engine_or_platform():
    ctl = _load("office_skeleton_ctl", "scripts/office-skeleton-ctl.py")
    for name, expected in ctl.FILES.items():
        p = REPO / "config" / f"{name}.example"
        env = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in active(p)}
        assert set(env) == set(expected), name
        assert all(v == "CHANGE_ME" for v in env.values()), f"{name}: placeholders only"
        for k in env:
            assert k not in ctl.FORBIDDEN_KEYS and not k.startswith(ctl.FORBIDDEN_PREFIXES), (name, k)
            assert "SQLITE" not in k.upper()
    # every engine/connection Environment= key in a quadlet is on the forbidden list
    for unit in ("corporatetraveldc-paperless", "corporatetraveldc-vikunja", "corporatetraveldc-firefly"):
        for k in env_of(SKEL / f"{unit}.container"):
            if re.search(r"DBENGINE|DATABASE_TYPE|DB_CONNECTION|DBHOST|DATABASE_HOST|^DB_HOST$", k):
                assert k in ctl.FORBIDDEN_KEYS, k


# ------------------------------------------------------------------ not wired anywhere
STACK_SCRIPTS = ("scripts/stack-boot-ctl.sh", "scripts/serialized-rollout.sh", "scripts/stack-refresh.sh",
                 "scripts/restart-stack.sh", "scripts/lockdown.sh", "scripts/watchdog.sh", "scripts/session-restore.sh",
                 "scripts/ingest-feed-ctl.sh", "scripts/backup/ctdi-backup.sh", "scripts/thermal-ingest-guard.py",
                 "scripts/lib/serial-bringup.sh")
NEEDLES = tuple(UNITS) + ("pgsql-office", "valkey-office", "office-skeletons", "[firefly]", "[paperless]", "[vikunja]")


@pytest.mark.parametrize("rel", STACK_SCRIPTS)
def test_absent_from_boot_stack_rollout_and_backup_scripts(rel):
    p = REPO / rel
    if not p.is_file():
        pytest.skip(f"{rel} not in this tree")
    text = p.read_text()
    assert not any(n in text for n in NEEDLES), rel


def test_no_timer_or_user_unit_names_an_office_unit():
    dirs = [REPO / ".config/systemd/user", REPO / "systemd", REPO / "config/systemd"]
    checked = 0
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in (".timer", ".service", ".path", ".target", ".socket"):
                checked += 1
                text = p.read_text(errors="replace")
                assert not any(n in text for n in NEEDLES), p.relative_to(REPO)
    assert checked > 50, "the unit scan found too few files: broken"


# ------------------------------------------------------------------ the office instance
def test_office_hba_rows():
    rows = [l.split() for l in active(REPO / "config/pg_hba-office.conf")]
    assert rows[-2:] == [["local", "all", "all", "reject"], ["host", "all", "all", "all", "reject"]]
    for role in ("dispatch", "demo", "demo_owner"):
        assert ["local", "all", role, "reject"] in rows and ["host", "all", role, "all", "reject"] in rows
    allow = [r for r in rows if r[-1] != "reject"]
    assert all(r[-1] == "scram-sha-256" for r in allow)
    for r in allow:
        db, user = r[1], r[2]
        if user == "office_owner":
            assert r[0] == "local" and db in ("office", "postgres"), "the owner never over TCP"
        else:
            assert user in ("firefly", "paperless", "vikunja") and db == user, r
            if r[0] == "host":
                assert r[3] == "samenet", r
    assert not any(r[1] == "all" and r[-1] != "reject" for r in rows)


def test_production_hba_rejects_office_roles_before_catch_alls():
    rows = [l.split() for l in active(REPO / "config/pg_hba.conf")]
    first_catch_all = next(i for i, r in enumerate(rows) if r[1] == "all" and r[2] == "all")
    for role in ("office_owner", "firefly", "paperless", "vikunja"):
        for kind in ("local", "host"):
            idx = [i for i, r in enumerate(rows) if r[0] == kind and r[2] == role and r[-1] == "reject"]
            assert idx and idx[0] < first_catch_all, (kind, role)
    assert any(r[1] == "office,firefly,paperless,vikunja" and r[-1] == "reject" for r in rows[:first_catch_all])


def test_office_config():
    conf = (REPO / "config/postgresql-office.conf").read_text()
    assert "hba_file = '/etc/postgresql/pg_hba.conf'" in conf
    assert "synchronous_commit = on" in conf and "fsync = on" in conf, "records of their own: durability kept"
    m = int(re.search(r"^max_connections = (\d+)", conf, re.M).group(1))
    r = int(re.search(r"^superuser_reserved_connections = (\d+)", conf, re.M).group(1))
    sql = (REPO / "src/common/pg_schema_office/0001_office_roles.sql").read_text()
    limits = [int(x) for x in re.findall(r"CONNECTION LIMIT (\d+)", sql)]
    assert len(limits) == 3 and sum(limits) + r <= m
    q = SKEL / "corporatetraveldc-pgsql-office.container"
    assert "config/postgresql-office.conf:/etc/postgresql/postgresql.conf:ro,Z" in q.read_text()
    assert "config/pg_hba-office.conf:/etc/postgresql/pg_hba.conf:ro,Z" in q.read_text()
    assert "Volume=corporatetraveldc-pgsql-office-sock:/var/run/postgresql" in active(q)


def test_init_sql_one_database_per_role_no_password_idempotent():
    sql = (REPO / "src/common/pg_schema_office/0001_office_roles.sql").read_text()
    body = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    assert "PASSWORD" not in body.upper(), "no password in the init SQL"
    flat = re.sub(r" +", " ", body)
    for role in ("firefly", "paperless", "vikunja"):
        assert f"WHERE rolname = '{role}'" in body and f"CREATE ROLE {role} LOGIN;" in body
        assert re.search(rf"ALTER ROLE {role}\s+LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS", body)
        assert f"CREATE DATABASE {role} OWNER {role}" in body
        assert re.search(rf"WHERE NOT EXISTS \(SELECT 1 FROM pg_database WHERE datname = '{role}'\) \\gexec", body)
        assert f"REVOKE ALL ON DATABASE {role} FROM PUBLIC;" in flat
        assert f"GRANT CONNECT, TEMPORARY ON DATABASE {role} TO {role};" in flat
    assert not re.search(r"\b(CREATE|DROP)\s+TABLE\b", body, re.I), "the apps own their schemas"
    assert "SUPERUSER" not in body.replace("NOSUPERUSER", "")


# ------------------------------------------------------------------ the control tool
@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    mod = _load("office_skeleton_ctl_t", "scripts/office-skeleton-ctl.py")
    monkeypatch.setattr(mod, "SECRETS_DIR", tmp_path)
    for name in mod.FILES:
        p = tmp_path / name
        p.write_text((REPO / "config" / f"{name}.example").read_text())
        os.chmod(p, 0o600)
    return mod


def _values(ctl, tmp_path) -> list[str]:
    return [v for name in ctl.FILES for v in ctl.read_env(tmp_path / name).values()]


def test_fill_generates_derives_and_is_rerun_safe(ctl, tmp_path, capsys):
    assert ctl.check(), "placeholders are reported before fill"
    assert ctl.main(["fill"]) == 0
    out = capsys.readouterr()
    vals = _values(ctl, tmp_path)
    assert all(v not in out.out + out.err for v in vals), "fill never prints a value"
    assert ctl.check() == []
    env = ctl.read_env(tmp_path / "firefly-secrets.env")
    assert len(env["APP_KEY"]) == 32 and len(env["STATIC_CRON_TOKEN"]) == 32
    vp = ctl.read_env(tmp_path / "valkey-office-secrets.env")["VALKEY_PASSWORD"]
    assert ctl.read_env(tmp_path / "paperless-secrets.env")["PAPERLESS_REDIS"] == \
        f"redis://:{vp}@corporatetraveldc-valkey-office:6379/0"
    assert len(set(vals)) == len(vals), "every generated value distinct"
    before = {n: (tmp_path / n).read_text() for n in ctl.FILES}
    assert ctl.fill() == []
    assert before == {n: (tmp_path / n).read_text() for n in ctl.FILES}, "a second fill changes nothing"
    for n in ctl.FILES:
        assert oct((tmp_path / n).stat().st_mode & 0o777) == "0o600"


def test_check_finds_mode_forbidden_quoted_and_mismatch(ctl, tmp_path):
    ctl.fill()
    p = tmp_path / "paperless-secrets.env"
    p.write_text(p.read_text() + "PAPERLESS_DBENGINE=sqlite\nDISPATCH_PG_HOST=x\n")
    os.chmod(tmp_path / "vikunja-secrets.env", 0o644)
    probs = " ".join(ctl.check())
    assert "forbidden name PAPERLESS_DBENGINE" in probs and "forbidden name DISPATCH_PG_HOST" in probs
    assert "must be mode 0600" in probs
    ctl.write_keys(tmp_path / "valkey-office-secrets.env", {"VALKEY_PASSWORD": "a" * 48})
    assert "does not match VALKEY_PASSWORD" in " ".join(ctl.check())


def test_write_keys_refuses_bad_shapes(ctl, tmp_path):
    with pytest.raises(ctl.OfficeError):
        ctl.write_keys(tmp_path / "firefly-secrets.env", {"DB_PASSWORD": "has space and 'quote' xxxxxxxxxxxxx"})
    with pytest.raises(ctl.OfficeError):
        ctl.write_keys(tmp_path / "firefly-secrets.env", {"DB_PASSWORD": "short"})


def test_scram_verifier_is_a_byte_twin_of_demo_env(ctl):
    demo = _load("demo_env_t", "scripts/demo-env.py")
    salt = b"\x01" * 16
    assert ctl.scram_verifier("x" * 30, salt) == demo.scram_verifier("x" * 30, salt)


def test_set_passwords_uses_stdin_and_never_argv(ctl, tmp_path, monkeypatch, capsys):
    ctl.fill()
    calls = []
    state = {"applied": set()}

    class R:
        def __init__(self, rc=0, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake_run(argv, *, stdin=None, env=None):
        calls.append((list(argv), stdin, dict(env or {})))
        if argv[:3] == ["podman", "container", "inspect"]:
            return R(0, "true\n")
        if "-U" in argv and argv[argv.index("-U") + 1] in ctl.ROLES:          # login check
            role = argv[argv.index("-U") + 1]
            return R(0, "1\n") if role in state["applied"] else R(2)
        if stdin and "ALTER ROLE" in stdin:
            state["applied"].add(stdin.split("ALTER ROLE ")[1].split()[0])
        return R(0, "")

    monkeypatch.setattr(ctl, "_run", fake_run)
    assert ctl.main(["set-passwords"]) == 0
    secrets_vals = [v for v in _values(ctl, tmp_path)]
    for argv, stdin, env in calls:
        assert not any(v in a for v in secrets_vals for a in argv), "no secret on argv"
        if stdin:
            assert not any(v in stdin for v in secrets_vals), "only verifiers on stdin, never the plaintext"
    alters = [s for _, s, _ in calls if s and "ALTER ROLE" in s]
    assert len(alters) == 3 and all("SCRAM-SHA-256$" in s and "log_statement = 'none'" in s for s in alters)
    logins = [a for a, _, e in calls if "PGPASSWORD" in a]
    assert logins and all(a[a.index("-e") + 1] == "PGPASSWORD" for a in logins), "passthrough, no value"
    out = capsys.readouterr()
    assert not any(v in out.out + out.err for v in secrets_vals)
    # rerun: every role already logs in -> no ALTER ROLE
    calls.clear()
    assert ctl.main(["set-passwords"]) == 0
    assert not [s for _, s, _ in calls if s and "ALTER ROLE" in s]


def test_init_db_sends_the_tracked_sql_on_stdin(ctl, monkeypatch):
    seen = []

    class R:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, *, stdin=None, env=None):
        seen.append((argv, stdin))
        r = R()
        if argv[:3] == ["podman", "container", "inspect"]:
            r.stdout = "true\n"
        return r

    monkeypatch.setattr(ctl, "_run", fake_run)
    assert ctl.main(["init-db"]) == 0
    argv, stdin = seen[-1]
    assert argv == ctl.OWNER_PSQL and "corporatetraveldc-pgsql-office" in argv
    assert stdin == (REPO / "src/common/pg_schema_office/0001_office_roles.sql").read_text()


def test_init_db_refuses_when_container_is_down(ctl, monkeypatch):
    class R:
        returncode, stdout, stderr = 1, "", ""
    monkeypatch.setattr(ctl, "_run", lambda argv, **k: R())
    assert ctl.main(["init-db"]) == 2


# ------------------------------------------------------------------ profiles, glossary, composition
PROFILES = REPO / "docs/deployment-profiles/profiles"


def test_ea_and_cos_are_separate_profiles_and_the_combined_one_is_gone():
    assert not (PROFILES / "executive-assistant-chief-of-staff.json").exists()
    ea = json.loads((PROFILES / "executive-assistant.json").read_text())
    cos = json.loads((PROFILES / "chief-of-staff.json").read_text())
    assert ea["deployment"]["profile"] == "executive-assistant" and cos["deployment"]["profile"] == "chief-of-staff"
    assert "travel_coordination" in ea["modules"]["enabled"] and "travel_coordination" not in cos["modules"]["enabled"]
    assert "decision_register" in cos["modules"]["enabled"] and "decision_register" not in ea["modules"]["enabled"]


@pytest.mark.parametrize("name", ["cfo", "executive-assistant", "chief-of-staff"])
def test_profile_integrations_are_disabled_and_forbid_sqlite(name):
    d = json.loads((PROFILES / f"{name}.json").read_text())
    opt = d["integrations"]["optional"]
    assert opt and all(o["state"] in ("disabled", "installed_disabled") for o in opt)
    assert "sqlite_backend" in d["integrations"]["forbidden"] and "platform_env_files" in d["integrations"]["forbidden"]
    for o in opt:
        if o["kind"] in ("container", "database_instance"):
            assert o["unit"] in UNITS, o
    assert d["security"]["default_deny"] is True and d["security"]["network_exposure"] == "private_overlay"


def test_each_profile_gets_a_real_slice():
    rg = _load("render_glossary_t", "scripts/render-glossary.py")
    data = rg.load(REPO)
    want = {"cfo": "finance", "executive-assistant": "scheduling", "chief-of-staff": "governance"}
    profiles = rg.discover_profiles(REPO)
    for name, scope in want.items():
        s = rg.profile_scopes(data, json.loads(profiles[name].read_text()))
        assert scope in s, name
        sl = json.loads(rg.render_profile(data, name, "json", REPO))
        assert any(e["scope"] == scope for sec in ("acronyms", "platform") for e in sl[sec]), name
    cfo = json.loads(rg.render_profile(data, "cfo", "json", REPO))
    terms = {e["term"] for sec in ("acronyms", "platform") for e in cfo[sec]}
    assert {"GAAP", "Firefly III", "SoD", "variance analysis"} <= terms and "FIDS" not in terms
    cos = json.loads(rg.render_profile(data, "chief-of-staff", "json", REPO))
    assert "aviation" not in cos["scopes"] and "finance" not in cos["scopes"]


def test_crossover_composition_compiles_disabled_with_a_valid_pin():
    sys.path.insert(0, str(REPO / "src"))
    from common import composition as C
    path = REPO / "docs/deployment-profiles/compositions/exec-office-crossover.json"
    comp = C.load(path)
    eff = C.compile(comp)
    assert C.diff(comp["_core"], eff) == {}
    assert comp["state"] == "mapped" and not any(o["enabled"] for o in comp["overlays"])
    assert comp["_core"]["deployment"]["profile"] == "executive-assistant"
    for o in comp["overlays"]:
        assert (path.parent / o["profile"]).resolve().is_file()
    assert {s["profile"] for s in comp["crossover"]["steps"]} == {"executive-assistant", "cfo", "chief-of-staff"}
    assert "synthetic" in comp["crossover"]["data"]
