"""Journal readers resume from a cursor and name units exactly (lean-out L2 / F4, 2026-10-19).

swim-session-health ran `journalctl --user -u 'corporatetraveldc-ingest-*' -S <last run>` every
5 min; the glob made journalctl map 2.5-4.5 GB of the 3.8 GB journal per run (~1 TB/day). The
journal readers now use scripts/lib/journal-cursor.sh: the first run reads `-S <since>
--show-cursor` and saves the cursor, every later run reads `--cursor-file` (journalctl refuses
the two together). A fake journalctl records each call and serves queued lines."""
import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEP = "\x1f"

FAKE_JOURNALCTL = r'''#!/bin/bash
# records argv (one call per line, args separated by \x1f) and serves $FAKE_J/lines once
printf '%s\x1f' "$@" >> "$FAKE_J/calls"; echo >> "$FAKE_J/calls"
cf=""; show=0
for a in "$@"; do
  case "$a" in --cursor-file=*) cf="${a#--cursor-file=}" ;; --show-cursor) show=1 ;; esac
done
n=0
if [[ -s "$FAKE_J/lines" ]]; then cat "$FAKE_J/lines"; n=1; : > "$FAKE_J/lines"; fi
if [[ -n "$cf" && $n == 1 ]]; then echo "s=fake-after" > "$cf"; fi
if [[ $show == 1 && $n == 1 ]]; then echo "-- cursor: s=fake-first"; fi
exit 0
'''

FAKE_CURL = r'''#!/bin/bash
printf '%s\x1f' "$@" >> "$FAKE_J/curl"; echo >> "$FAKE_J/curl"; exit 0
'''

FAKE_PODMAN = r'''#!/bin/bash
echo "$1 $2" >> "$FAKE_J/podman"
case "$1" in
  stats) cat "$FAKE_J/stats" 2>/dev/null ;;
  events) : ;;
esac
exit 0
'''


def _bin(tmp_path, **extra):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for name, body in {"journalctl": FAKE_JOURNALCTL, "curl": FAKE_CURL, "podman": FAKE_PODMAN, **extra}.items():
        (b / name).write_text(body)
        (b / name).chmod(0o755)
    j = tmp_path / "j"
    j.mkdir(exist_ok=True)
    return b, j


def _calls(j, name="calls"):
    p = j / name
    if not p.exists():
        return []
    return [line.rstrip(SEP).split(SEP) for line in p.read_text().splitlines() if line]


def _env(tmp_path, b, j, **kw):
    return dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", FAKE_J=str(j), **kw)


# --------------------------------------------------------------- swim-session-health
SWIM = REPO / "scripts/swim-session-health-watch.sh"


def _swim(tmp_path, lines=None, state=None):
    b, j = _bin(tmp_path)
    sd = tmp_path / "state"
    sd.mkdir(exist_ok=True)
    if state is not None:
        (sd / "state.json").write_text(json.dumps(state))
    (j / "lines").write_text("".join(f"{x}\n" for x in (lines or [])))
    env = _env(tmp_path, b, j, SWIM_HEALTH_STATE_DIR=str(sd), SWIM_HEALTH_LOCK_FILE=str(tmp_path / "lock"),
               SWIM_HEALTH_ENV_FILE=str(tmp_path / "none.env"), SWIM_HEALTH_SECRETS_FILE=str(tmp_path / "none2.env"))
    r = subprocess.run(["bash", str(SWIM)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return j, sd, r.stdout


def _fail_line(vpn):
    return f"x: SOLCLIENT_SUBCODE_KEEP_ALIVE_FAILURE on VPN name '{vpn}'"


def test_swim_first_run_exact_units_since_then_cursor(tmp_path):
    j, sd, _ = _swim(tmp_path, lines=["hello"])
    (first,) = _calls(j)
    assert "-S" in first and "--show-cursor" in first and not any(a.startswith("--cursor-file") for a in first)
    units = [first[i + 1] for i, a in enumerate(first) if a == "-u"]
    assert units and all("*" not in u and u.endswith(".service") for u in units)
    assert (sd / "journal.cursor").read_text().strip() == "s=fake-first"
    _swim(tmp_path, lines=["again"])
    second = _calls(j)[1]
    assert f"--cursor-file={sd / 'journal.cursor'}" in second and "-S" not in second


def test_swim_units_match_every_ingest_quadlet(tmp_path):
    j, _, _ = _swim(tmp_path)
    units = {c[i + 1] for c in _calls(j) for i, a in enumerate(c) if a == "-u"}
    quadlets = {p.name.replace(".container", ".service")
                for p in (REPO / ".config/containers/systemd").glob("corporatetraveldc-ingest-*.container")}
    assert units == quadlets


def test_swim_stale_cursor_falls_back_to_since(tmp_path):
    j, sd, _ = _swim(tmp_path, lines=["x"])
    old = time.time() - 3600
    os.utime(sd / "journal.cursor", (old, old))
    _swim(tmp_path, lines=["y"])
    second = _calls(j)[1]
    assert "-S" in second and not any(a.startswith("--cursor-file") for a in second)


def test_swim_same_threshold_and_sustain_alert(tmp_path):
    now = int(time.time())
    # 14 failures: under the 15 threshold -> not degraded
    _, sd, out = _swim(tmp_path, lines=[_fail_line("FDPS")] * 14, state={"last_run_ts": now - 300})
    assert json.loads((sd / "state.json").read_text())["degraded_since"] is None
    # 15 failures, degraded for > 600 s already -> one alert naming the VPN counts
    j, sd, out = _swim(tmp_path, lines=[_fail_line("FDPS")] * 10 + [_fail_line("TFMS")] * 5,
                       state={"last_run_ts": now - 300, "degraded_since": now - 700})
    curl = _calls(j, "curl")
    assert len(curl) == 1 and "Title: SWIM session health: DEGRADED" in curl[0]
    assert "15 keep-alive failures" in curl[0][-2] and "FDPS=10, TFMS=5" in curl[0][-2]


# --------------------------------------------------------------- brief-fallback-monitor
BRIEF = REPO / "scripts/brief-fallback-monitor.sh"


def _brief(tmp_path, lines):
    b, j = _bin(tmp_path)
    (j / "lines").write_text("".join(f"{x}\n" for x in lines))
    sd = tmp_path / "bstate"
    env = _env(tmp_path, b, j, DRY_RUN="1", BRIEF_FALLBACK_STATE_DIR=str(sd))
    r = subprocess.run(["bash", str(BRIEF)], env=env, capture_output=True, text=True, timeout=60)
    return j, sd, r.stdout


def _brief_line(i, fallback):
    kind = "brief generated (deterministic fallback)" if fallback else "brief generated (Ollama/m)"
    return f"Oct 19 0{i}:00:00 host u[1]: INFO:x: {kind}"


def test_brief_history_accumulates_across_cursor_reads(tmp_path):
    # run 1 (fallback read): two LLM runs for ops-brief; ep-advance's read gets nothing
    j, sd, out = _brief(tmp_path, [_brief_line(1, False), _brief_line(2, False)])
    calls = _calls(j)
    assert "-S" in calls[0] and "2 days ago" in calls[0] and "--show-cursor" in calls[0]
    # run 2 (cursor read): a new FALLBACK arrives -> alert computed over the accumulated history
    j, sd, out = _brief(tmp_path, [_brief_line(3, True)])
    second = _calls(j)[2]
    assert any(a.startswith("--cursor-file=") for a in second) and "-S" not in second
    assert "would ntfy" in out and "last 3=[LLM LLM FALLBACK]" in out


def test_brief_double_logged_line_counts_once(tmp_path):
    _, _, out = _brief(tmp_path, [_brief_line(1, True), _brief_line(1, True), _brief_line(2, False)])
    assert "last 2=[FALLBACK LLM]" in out


# --------------------------------------------------------------- container-mem-watch + ingest-restart
MEMW = REPO / "scripts/container-mem-watch.sh"
RESTART = REPO / "scripts/scheduled-ingest-restart.sh"


def _memw(tmp_path, stats):
    b, j = _bin(tmp_path)
    (j / "stats").write_text(stats)
    sd = tmp_path / "mstate"
    snap = tmp_path / "snapshot"
    env = _env(tmp_path, b, j, MEM_WATCH_STATE_DIR=str(sd), MEM_WATCH_LOCK_FILE=str(tmp_path / "mlock"),
               MEM_WATCH_ENV_FILE=str(tmp_path / "n.env"), MEM_WATCH_SECRETS_FILE=str(tmp_path / "n2.env"),
               CONTAINER_STATS_SNAPSHOT=str(snap))
    r = subprocess.run(["bash", str(MEMW)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return j, sd, snap


def test_mem_watch_kernel_read_uses_cursor_and_publishes_snapshot(tmp_path):
    (tmp_path / "j").mkdir()
    (tmp_path / "j" / "lines").write_text("kernel: something\n")
    j, sd, snap = _memw(tmp_path, "systemd-corporatetraveldc-ingest-fdps|42.5%\n")
    first = _calls(j)[0]
    assert "-k" in first and "-S" in first and "--until" in first
    lines = snap.read_text().splitlines()
    assert lines[0].startswith("# epoch ") and lines[1] == "systemd-corporatetraveldc-ingest-fdps|42.5%"
    (j / "lines").write_text("kernel: more\n")
    _memw(tmp_path, "systemd-corporatetraveldc-ingest-fdps|43.0%\n")
    second = _calls(j)[1]
    assert "-k" in second and "--until" in second and "-S" not in second
    assert any(a.startswith("--cursor-file=") for a in second)


def _restart(tmp_path, snapshot_text=None, stats="", snap_age=0):
    b, j = _bin(tmp_path)
    (j / "stats").write_text(stats)
    snap = tmp_path / "snapshot"
    if snapshot_text is not None:
        snap.write_text(f"# epoch {int(time.time()) - snap_age}\n{snapshot_text}")
    env = _env(tmp_path, b, j, CONTAINER_STATS_SNAPSHOT=str(snap),
               INGEST_RESTART_STATE_DIR=str(tmp_path / "rstate"),
               INGEST_RESTART_GUARD_STATE=str(tmp_path / "no-guard.json"))
    r = subprocess.run(["bash", str(RESTART), "--dry-run"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    podman = (j / "podman").read_text().splitlines() if (j / "podman").exists() else []
    return podman, r.stdout


def test_restart_reads_the_fresh_snapshot_and_never_runs_podman(tmp_path):
    podman, out = _restart(tmp_path, "systemd-corporatetraveldc-ingest-fdps|95.0%\nsystemd-corporatetraveldc-ingest-core|10%\n")
    assert podman == []
    assert "corporatetraveldc-ingest-fdps.service: mem at 95.0%" in out and "[DRY-RUN] would run" in out
    assert "ingest-core.service: mem at" not in out


def test_restart_stale_snapshot_runs_podman_stats_once(tmp_path):
    podman, out = _restart(tmp_path, "systemd-corporatetraveldc-ingest-fdps|95.0%\n", snap_age=600,
                           stats="systemd-corporatetraveldc-ingest-fdps|20%\n")
    assert podman == ["stats --no-stream"]
    assert "would run" not in out


def test_restart_without_snapshot_runs_podman_stats_once(tmp_path):
    podman, _ = _restart(tmp_path, None, stats="systemd-corporatetraveldc-ingest-tbfm|91%\n")
    assert podman == ["stats --no-stream"]


# --------------------------------------------------------------- maintenance-dispatch
MAINT = REPO / "scripts/maintenance-dispatch.sh"

FAKE_SYSTEMCTL = r'''#!/bin/bash
echo "$*" >> "$FAKE_J/systemctl"
if [[ "$*" == *is-active* ]]; then cat "$FAKE_J/active"; fi
exit 0
'''


def _maint(tmp_path, active):
    b, j = _bin(tmp_path, systemctl=FAKE_SYSTEMCTL)
    guard = b / "guard"
    guard.write_text("#!/bin/bash\nexit 1\n")          # outside every window
    guard.chmod(0o755)
    (j / "active").write_text("active\n" if active else "inactive\n")
    home = tmp_path / "home"
    (home / ".cache").mkdir(parents=True, exist_ok=True)
    unit = [u for u in (REPO / "scripts/lib/maintenance-queue.txt").read_text().splitlines()
            if u.strip() and not u.startswith("#")][0]
    day = subprocess.run(["bash", "-c", "TZ=America/New_York date -d '5 hours ago' +%F"],
                         capture_output=True, text=True).stdout.strip()
    (home / ".cache/maintenance-queue.json").write_text(json.dumps(
        {"day": day, "jobs": {unit: {"status": "dispatched", "at": int(time.time()) - 600, "attempts": 1}}}))
    (j / "lines").write_text(f"Finished {unit}.service.\n")
    env = _env(tmp_path, b, j, HOME=str(home), MAINT_WINDOW_GUARD=str(guard))
    r = subprocess.run(["bash", str(MAINT)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    st = json.loads((home / ".cache/maintenance-queue.json").read_text())
    return _calls(j), st["jobs"][unit]["status"], unit


def test_maintenance_running_job_is_not_journal_read(tmp_path):
    calls, status, _ = _maint(tmp_path, active=True)
    assert calls == [] and status == "dispatched"


def test_maintenance_finished_job_read_once_exact_unit(tmp_path):
    calls, status, unit = _maint(tmp_path, active=False)
    assert len(calls) == 1 and f"{unit}.service" in calls[0] and "*" not in "".join(calls[0])
    assert status == "done"


# --------------------------------------------------------------- the library itself
def test_jc_read_never_combines_since_and_cursor_file(tmp_path):
    b, j = _bin(tmp_path)
    cf = tmp_path / "c"
    out = tmp_path / "o"
    lib = REPO / "scripts/lib/journal-cursor.sh"
    script = f'. "{lib}"; jc_read "{out}" "{cf}" "-5min" 600 --user -u a.service'
    env = _env(tmp_path, b, j)
    for _ in range(3):
        (j / "lines").write_text("line\n")
        subprocess.run(["bash", "-c", script], env=env, check=True, timeout=30)
        assert "-- cursor:" not in out.read_text()
    for c in _calls(j):
        assert not ("-S" in c and any(a.startswith("--cursor-file") for a in c))
    assert cf.read_text().strip() == "s=fake-after"            # journalctl itself advanced it
