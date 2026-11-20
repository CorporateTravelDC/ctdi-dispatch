"""2026-11-19: direct backup (ctdi-backup-direct.sh) -- stage, encrypt, upload, verify, delete; USB as offload only.

Everything external is stubbed: podman (pg_dump/psql/unshare/volume), gpg, rclone, tar, findmnt, lsblk,
mountpoint, ip. The fake remote is a directory. No real database, network, key or disk device is touched.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/backup/ctdi-backup-direct.sh"
RESTORE = REPO / "scripts/backup/backup-restore-test-direct.sh"
FPR = "A" * 40


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


# ---- pure helpers --------------------------------------------------------------------------------------

def test_retention_keeps_7_daily_4_weekly_3_monthly_and_ignores_foreign_names():
    r = _load("dr", "scripts/backup/direct_retention.py")
    # 120 nightly runs back from 2026-11-19, plus names that are NOT ours
    runs = [time.strftime("%Y%m%dT034000Z", time.gmtime(time.mktime((2026, 11, 19, 12, 0, 0, 0, 0, 0)) - d * 86400))
            for d in range(120)]
    drop = r.plan(runs + ["borg/", "data", "README.txt", "2026-11-01/"], 7, 4, 3)
    keep = set(runs) - set(drop)
    assert runs[0] in keep and all(x in keep for x in runs[:7])          # newest 7 days
    assert 10 <= len(keep) <= 14                                          # 7 daily + up to 4 weekly + 3 monthly, overlapping
    assert not any(n in drop for n in ("borg/", "data", "README.txt", "2026-11-01/"))
    assert all(d in runs for d in drop)


def test_retention_never_drops_the_newest_and_handles_empty():
    r = _load("dr2", "scripts/backup/direct_retention.py")
    assert r.plan([]) == []
    assert r.plan(["20261119T034000Z"], 0, 0, 0) == []


def test_stream_tap_records_bytes_and_hashes(tmp_path):
    data = os.urandom(3 * (1 << 20) + 17)
    meta = tmp_path / "m.json"
    out = subprocess.run([sys.executable, str(REPO / "scripts/backup/stream_tap.py"), str(meta)], input=data,
                         capture_output=True, check=True).stdout
    assert out == data
    m = json.loads(meta.read_text())
    import hashlib
    assert m == {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest()}
    f = tmp_path / "f"; f.write_bytes(data)
    again = subprocess.run([sys.executable, str(REPO / "scripts/backup/stream_tap.py"), "--file", str(f)],
                           capture_output=True, text=True, check=True).stdout
    same = subprocess.run([sys.executable, str(REPO / "scripts/backup/stream_tap.py"), "--same", again, meta.read_text()])
    assert same.returncode == 0
    f.write_bytes(data[:-1] + b"x")
    other = subprocess.run([sys.executable, str(REPO / "scripts/backup/stream_tap.py"), "--file", str(f)],
                           capture_output=True, text=True, check=True).stdout
    assert subprocess.run([sys.executable, str(REPO / "scripts/backup/stream_tap.py"), "--same", other, meta.read_text()]).returncode == 1


def _e(kind, hours_ago, **data):
    return {"kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours_ago * 3600)), "data": data}


def test_freshness_direct_mode_needs_a_complete_run_and_a_fresh_restore_test(monkeypatch):
    m = _load("bf_direct", "scripts/backup/backup_freshness.py")
    m.ENV = Path("/nonexistent/dispatch.env")
    monkeypatch.setenv("BACKUP_MODE", "direct")
    monkeypatch.setenv("BACKUP_TARGET_DIRECT", "proton:ctdi-backup-direct")
    monkeypatch.setenv("BACKUP_TARGET", "")
    assert m.check([_e("direct-backup", 3, ok=True), _e("direct-restore-test", 30, ok=True)]) == []
    p = m.check([_e("direct-backup", 3, ok=False), _e("direct-backup", 50, ok=True), _e("direct-restore-test", 30, ok=True)])
    assert any("no complete direct backup" in x for x in p)      # a PARTIAL run does not count
    p = m.check([_e("direct-backup", 3, ok=True), _e("direct-restore-test", 300, ok=True)])
    assert any("direct restore test" in x for x in p)
    # Borg-mode checks are not applied in direct mode
    assert not any("off-box sync" in x or x.startswith("no backup") for x in m.check([_e("direct-backup", 1, ok=True), _e("direct-restore-test", 1, ok=True)]))


# ---- the script, end to end, with stubs ----------------------------------------------------------------

STUB = r'''#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
me = os.path.basename(sys.argv[0]); a = sys.argv[1:]
REM = Path(os.environ["FAKE_REMOTE"]); LOG = Path(os.environ["STUB_LOG"])
with LOG.open("a") as f: f.write(json.dumps([me] + a) + "\n")
def rpath(spec): return REM / spec.split(":", 1)[1]
def rclone(args):
    while args and args[0].startswith("--config"): args = args[2:] if args[0] == "--config" else args[1:]
    cmd, rest = args[0], args[1:]
    pos = [x for x in rest if not x.startswith("-")]
    if cmd == "copy":
        src, dst = pos[0], rpath(pos[1]); dst.mkdir(parents=True, exist_ok=True); shutil.copy(src, dst / Path(src).name)
        if os.environ.get("FAIL_UPLOAD"): (dst / Path(src).name).write_bytes(b"corrupt")
    elif cmd == "rcat":
        p = rpath(pos[0]); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(sys.stdin.buffer.read())
    elif cmd == "lsjson":
        p = rpath(pos[0]); print(json.dumps([{"Name": p.name, "Size": p.stat().st_size}] if p.exists() else []))
    elif cmd == "lsf":
        p = rpath(pos[0]); [print(d.name + "/") for d in sorted(p.iterdir()) if d.is_dir()] if p.exists() else None
    elif cmd == "purge":
        shutil.rmtree(rpath(pos[0]))
    elif cmd == "cat":
        sys.stdout.buffer.write(rpath(pos[-1]).read_bytes()[: (int(rest[rest.index("--head") + 1]) if "--head" in rest else None)])
if me == "podman":
    if a[0] == "unshare":
        if a[1] == "rclone": rclone(a[2:])
        elif a[1] == "tar": sys.stdout.write("VOLUMES-TAR\n")
        sys.exit(0)
    if a[0] == "volume": sys.exit(1)
    if a[0] == "exec":
        sh = a[-1]
        if "pg_database_size" in sh:
            print(os.environ.get("FAKE_NC_BYTES", "1000000") if "nextcloud" in a[2] else os.environ.get("FAKE_DB_BYTES", "1000000"))
        elif "pg_total_relation_size" in sh: print("1000")
        elif "json_build_object" in sh: print('{"faa_ladd_aircraft": 5, "faa_ladd_removals": 1}')
        elif "pg_dump" in sh: sys.stdout.write("PGDUMP " + sh + "\n")
        sys.exit(0)
elif me == "gpg":
    if "--import" in a: sys.exit(0)
    if "--list-keys" in a:
        caps = os.environ.get("FAKE_CAPS", "scESC")
        print(f"pub:u:255:22:{'B'*16}:1:::u:::{caps}:"); print("fpr:::::::::" + "A"*40 + ":")
        # default: a normal key (primary sign/cert, encryption subkey); FAKE_CAPS alone = no encryption subkey
        if os.environ.get("FAKE_SUB_E") or not os.environ.get("FAKE_CAPS"): print(f"sub:u:255:18:{'C'*16}:1::::::e:")
        sys.exit(0)
    if "--encrypt" in a: sys.stdout.buffer.write(b"GPG[" + sys.stdin.buffer.read() + b"]"); sys.exit(0)
elif me == "tar": sys.stdout.write("STATE-TAR\n")
elif me == "mountpoint": sys.exit(0 if os.environ.get("FAKE_USB_MOUNTED") else 1)
elif me == "findmnt":
    if "OPTIONS" in a: print(os.environ.get("FAKE_USB_OPTS", "rw,noatime"))
    else: print("/dev/sdz1[/stage]")
elif me == "lsblk": print("sdz")
elif me == "ip": print(f"default via 10.x.x.x dev {os.environ.get('FAKE_IFACE', 'wld0')} metric 600")
'''


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"; bin_.mkdir()
    stub = bin_ / "_stub"; stub.write_text(STUB); stub.chmod(0o755)
    for n in ("podman", "gpg", "rclone", "tar", "mountpoint", "findmnt", "lsblk", "ip"):
        (bin_ / n).symlink_to(stub)
    guard = tmp_path / "guard.py"; guard.write_text("print('{}')\n")
    home = tmp_path / "home"; (home / ".local/state/ctdc-backup").mkdir(parents=True)
    pub = tmp_path / "pub.asc"; pub.write_text("not a real key")
    sysfs = tmp_path / "sys"; (sysfs / "block/sdz/device").mkdir(parents=True)
    (sysfs / "block/sdz/device/state").write_text("running\n")
    usb = tmp_path / "usb"; usb.mkdir()
    e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", HOME=str(home), FAKE_REMOTE=str(tmp_path / "remote"),
             STUB_LOG=str(tmp_path / "stub.log"), CTDC_ENV_FILE=str(tmp_path / "none.env"),
             BACKUP_LEDGER=str(home / ".local/state/ctdc-backup/ledger.jsonl"),
             BACKUP_TARGET_DIRECT="fake:direct", BACKUP_TARGET="fake:ctdi-backup",
             BACKUP_DIRECT_GPG_RECIPIENT=FPR, BACKUP_DIRECT_GPG_PUBKEY=str(pub),
             BACKUP_DIRECT_STAGE=str(usb / "stage-direct"), BACKUP_DIRECT_STAGE_MOUNT=str(usb),
             BACKUP_DIRECT_NVME_STAGE=str(tmp_path / "nvme-stage"), BACKUP_MIN_FREE_GB="0",
             CTDC_SYSFS=str(sysfs), CTDC_TEST_RATE_GUARD=str(guard), CTDC_TEST_ANY_BLOCKDEV="1",
             CTDC_HEAVY_LOCK_HELD="test", NET_METERED_IFACES="enu1", HEAVY_LOCK_DIR=str(tmp_path / "hl"))
    (tmp_path / "remote").mkdir()
    return e, tmp_path


def run(e, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True, timeout=120)


def ledger(e):
    p = Path(e["BACKUP_LEDGER"])
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def calls(tmp):
    return [json.loads(l) for l in (tmp / "stub.log").read_text().splitlines()]


def test_full_run_stages_on_nvme_when_usb_absent_uploads_verifies_and_leaves_nothing(env):
    e, tmp = env
    r = run(e)
    assert r.returncode == 0, r.stdout + r.stderr
    runs = list((tmp / "remote/direct").iterdir())
    assert len(runs) == 1
    names = sorted(p.name for p in runs[0].iterdir())
    assert names == ["MANIFEST.json", "dispatch.pgdump.zst.gpg", "nextcloud.pgdump.zst.gpg", "state.tar.zst.gpg", "volumes.tar.zst.gpg"]
    assert "stage: nvme" in r.stdout
    assert not any((tmp / "nvme-stage").rglob("*.gpg"))                       # nothing left staged
    es = ledger(e)
    objs = [x for x in es if x["kind"] == "direct-object"]
    assert len(objs) == 4 and all(o["data"]["meta"]["bytes"] > 0 for o in objs)
    db = [x for x in es if x["kind"] == "direct-backup"][-1]
    assert db["data"]["ok"] is True and db["data"]["stage"] == "nvme"
    stamp = [x for x in es if x["kind"] == "cui-stamp"][-1]
    assert stamp["data"]["off_box"] is False and stamp["data"]["mode"] == "direct"


def test_ladd_data_never_enters_the_off_box_dump(env):
    e, tmp = env
    assert run(e).returncode == 0
    dumps = [c[-1] for c in calls(tmp) if c[0] == "podman" and c[1] == "exec" and "pg_dump" in c[-1]]
    dispatch = [d for d in dumps if "exclude-table-data" in d]
    assert dispatch and all("--exclude-table-data=faa_ladd_aircraft" in d and "--exclude-table-data=faa_ladd_removals" in d for d in dispatch)
    assert all("-Z 0" in d for d in dumps)                                      # one compression pass: zstd
    obj = next((tmp / "remote/direct").iterdir()) / "dispatch.pgdump.zst.gpg"
    assert obj.read_bytes().startswith(b"GPG[")                                  # encrypted object


def test_usb_used_only_when_device_running_rw_and_probe_passes(env):
    e, tmp = env
    e = dict(e, FAKE_USB_MOUNTED="1")
    r = run(e); assert r.returncode == 0, r.stderr
    assert "stage: usb" in r.stdout
    (tmp / "sys/block/sdz/device/state").write_text("offline\n")                # the 2026-11-19 failure
    r = run(e); assert r.returncode == 0, r.stderr
    assert "stage: nvme" in r.stdout
    (tmp / "sys/block/sdz/device/state").write_text("running\n")
    r = run(dict(e, FAKE_USB_OPTS="ro,noatime")); assert r.returncode == 0
    assert "stage: nvme" in r.stdout


def test_dispatch_skipped_with_alert_when_nothing_can_stage_and_too_big_to_stream(env):
    e, tmp = env
    e = dict(e, BACKUP_MIN_FREE_GB="100000", FAKE_DB_BYTES=str(10 * 2**30), BACKUP_DIRECT_STREAM_MAX_GB="2")
    r = run(e)
    assert r.returncode == 1 and "PARTIAL" in r.stdout + r.stderr
    assert "stage: stream" in r.stdout and "SKIPPED dispatch.pgdump.zst.gpg" in r.stdout
    run_dir = next((tmp / "remote/direct").iterdir())
    assert not (run_dir / "dispatch.pgdump.zst.gpg").exists()
    assert (run_dir / "nextcloud.pgdump.zst.gpg").exists()                     # small objects still streamed
    db = [x for x in ledger(e) if x["kind"] == "direct-backup"][-1]
    assert db["data"]["ok"] is False


def test_no_upload_on_the_metered_uplink(env):
    e, tmp = env
    r = run(dict(e, FAKE_IFACE="enu1"))
    assert r.returncode == 1
    assert not any((tmp / "remote/direct").rglob("*.gpg"))
    assert not any(c[0] == "podman" and c[1:3] == ["unshare", "rclone"] and "copy" in c for c in calls(tmp))


def test_refuses_without_a_usable_encryption_key_or_with_overlapping_targets(env):
    e, tmp = env
    r = run(dict(e, FAKE_CAPS="scSC"))                                           # sign/cert only, no e
    assert r.returncode == 1 and "no usable encryption" in r.stdout + r.stderr
    r = run(dict(e, BACKUP_DIRECT_GPG_RECIPIENT=""))
    assert r.returncode == 1 and "fingerprint" in r.stdout + r.stderr
    r = run(dict(e, BACKUP_TARGET_DIRECT="fake:ctdi-backup/direct"))
    assert r.returncode == 1 and "overlaps" in r.stdout + r.stderr
    assert not any((tmp / "remote").rglob("*.gpg"))                             # nothing uploaded by a refusal
    assert run(dict(e, FAKE_CAPS="scSC", FAKE_SUB_E="1")).returncode == 0       # an encryption subkey is enough


def test_corrupt_remote_copy_fails_the_run(env):
    e, tmp = env
    r = run(dict(e, FAIL_UPLOAD="1"))
    assert r.returncode == 1
    assert not any(x["kind"] == "direct-object" for x in ledger(e))
    assert not any((tmp / "nvme-stage").rglob("*.gpg"))


def test_retention_prunes_only_its_own_runs_under_its_own_prefix(env):
    e, tmp = env
    d = tmp / "remote/direct"
    for i in range(40):
        (d / time.strftime("%Y%m%dT034000Z", time.gmtime(time.time() - (i + 1) * 86400))).mkdir(parents=True)
    (d / "notes").mkdir(); (tmp / "remote/ctdi-backup").mkdir(); (tmp / "remote/ctdi-backup/borg-file").write_text("x")
    assert run(e).returncode == 0
    left = sorted(p.name for p in d.iterdir())
    assert "notes" in left and len([n for n in left if n[0].isdigit()]) <= 15
    assert (tmp / "remote/ctdi-backup/borg-file").exists()                     # the Borg mirror is never touched


def test_restore_test_checks_remote_hashes_against_the_ledger(env):
    e, tmp = env
    assert run(e).returncode == 0
    r = subprocess.run(["bash", str(RESTORE)], env=dict(e, FAKE_LISTPKT="1"), capture_output=True, text=True, timeout=60)
    # the stub gpg prints no packet list, so the recipient check must fail closed -- but only AFTER the hash checks
    assert "sha256 matches the ledger" in r.stdout
    assert r.returncode == 1 and "not encrypted to the configured recipient" in r.stdout
    obj = next(d for d in (tmp / "remote/direct").iterdir()) / "state.tar.zst.gpg"
    obj.write_bytes(b"tampered")
    r = subprocess.run(["bash", str(RESTORE)], env=e, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "does not match the write-time ledger hash" in r.stdout
