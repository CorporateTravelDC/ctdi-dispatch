"""
common.llama_pool -- report-tier port claiming for the llama.cpp migration
(Ollama -> raw llama-server, see personas.py).

Architecture (revised 2026-08-27 -- see below for why):
  - Port 8093 "hot":    permanent, always resident, launched by its own
                         systemd unit (corporatetraveldc-llama-hot.service).
  - Port 8094 "chat":   permanent, always resident, same story
                         (corporatetraveldc-llama-chat.service).
  - REPORT_PORTS: a small FIXED set of permanent, always-resident ports
    (corporatetraveldc-llama-report-N.service, N=1..len(REPORT_PORTS)),
    claimed exclusively for one request via a per-port flock.

2026-08-30 amendment: report-1 (REPORT_PORTS[0], port 8095) is now
ON-DEMAND, not permanently resident -- it was shelved 2026-08-27 after two
near-OOM incidents running alongside hot+chat, which silently broke every
persona with num_ctx > chat's -c 4096. The consumer skills' own quadlets
now start/stop it at the HOST level (ExecStartPre / guarded ExecStopPost,
scripts/llama-report-ondemand.sh) -- quadlet hooks run on the host, so the
container/host privilege boundary documented below doesn't apply to them.
common/llm.py's ollama_post_with_retry() routes to it by the calling
persona's declared num_ctx; claim_port()'s flock path is currently unused
(report-1 is single-slot, -np 1 -- llama-server queues concurrent
requests itself).

Originally designed as an ELASTIC pool (ports 8095-9005, spin up on demand,
5-min idle self-timeout) -- abandoned same day, before ever shipping,
because it doesn't fit this deployment's actual topology: every caller of
this module runs inside a poller/skill podman CONTAINER, which cannot
subprocess.Popen a new process onto the HOST (different PID namespace,
different filesystem -- /usr/local/lib/ollama/llama-server doesn't even
exist inside the container). Discovered live: the first real ops-brief
test through the elastic version failed with FileNotFoundError trying to
spawn llama-server from inside the poller container. Every llama-server
process this module talks to is therefore host-managed by systemd, exactly
like hot/chat -- this module's only job (when imported from inside a
container) is claiming an already-running port over HTTP + a shared flock
file under /var/lib/corporatetraveldc (bind-mounted into every container),
never spawning anything itself.

This still mirrors common/ollama_lock.py's flock-based, crash-safe design
(a killed claimant's fd closes and the flock releases itself -- no
stale-lock cleanup) rather than a central counter, which would desync if a
claimant died mid-request.

2026-09-06 (operator directive, supersedes everything above): ONE
llama-server, ONE model resident 24/7, TWO slots (-np 2), hard-capped at
two cores by the unit's CPUQuota=200% -- nothing ever adds a core, a slot
or a server. corporatetraveldc-llama.service, port LLAMA_PORT (8093).
HOT_PORT / CHAT_PORT / REPORT_PORTS all resolve to that one port so no
caller changed. Slot discipline is enforced HERE, not by the server:
  - "hot" / "chat" personas post immediately (fast lane; the second slot
    is always available to them because the rule below leaves it free).
  - every other persona is a LONG RUNNER and must hold long_runner_lock()
    for the duration of its request: one long runner in flight, ever,
    across every container on the box (the lock file lives under the
    bind-mounted STATE_DIR). A second long runner WAITS -- it is never
    refused, never times out, never kills the one ahead of it. The same
    file is taken by `flock` in each scheduled consumer's quadlet Exec=
    so two long-runner UNITS cannot even start together (belt and
    suspenders; a container's flock and this module's flock are separate
    open-file-descriptions, so the wrapper's lock does not block its own
    child -- see UNIT_LOCK_PATH vs LOCK_PATH).
Nothing automated cancels an in-flight generation: only the operator, or
Claude through the sudo-approval gate, stops a run.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import pathlib
import time

log = logging.getLogger(__name__)

HOST = os.getenv("LLAMA_POOL_HOST", "100.x.x.x")
LLAMA_PORT = int(os.getenv("LLAMA_PORT", "8093"))
# Legacy names kept so runner/main.py's Dispatch Drawer streaming and any
# other importer keep working unchanged -- they are all the same server now.
HOT_PORT = LLAMA_PORT
CHAT_PORT = LLAMA_PORT
REPORT_PORTS = [LLAMA_PORT]

STATE_DIR = pathlib.Path("/var/lib/corporatetraveldc/llama-pool")
# In-process lock: one long-runner GENERATION at a time, box-wide.
LOCK_PATH = STATE_DIR / "long-runner.lock"
# Unit-level lock: one long-runner CONTAINER at a time (quadlet Exec= flock).
UNIT_LOCK_PATH = STATE_DIR / "long-runner-unit.lock"
LOCK_WAIT_LOG_EVERY_S = float(os.getenv("LLAMA_LOCK_WAIT_LOG_EVERY_S", "60"))


@contextlib.contextmanager
def long_runner_lock(persona_key: str):
    """Hold the box-wide long-runner lock for one generation. Blocks --
    indefinitely, by design -- until the long runner ahead of us finishes;
    logs once a minute while waiting so a stuck queue is visible in the
    journal. Crash-safe: a dead holder's fd closes and the flock releases
    itself."""
    _ensure_dir()
    fd = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT)
    try:
        t0 = time.monotonic()
        next_log = t0 + LOCK_WAIT_LOG_EVERY_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                if now >= next_log:
                    log.info("llama-pool: %s waiting %.0fs for the long-runner slot (another report is in flight; never preempted)",
                             persona_key, now - t0)
                    next_log = now + LOCK_WAIT_LOG_EVERY_S
                time.sleep(2.0)
        waited = time.monotonic() - t0
        if waited >= 5.0:
            log.info("llama-pool: %s acquired the long-runner slot after %.0fs", persona_key, waited)
        try:
            yield LLAMA_PORT
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class PoolBusyError(TimeoutError):
    """Raised when every report-tier port is currently claimed. Callers
    should treat this exactly like common.ollama_lock.OllamaBusyError --
    fall through to the skill's existing deterministic fallback."""


def _ensure_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _lock_path(port: int) -> pathlib.Path:
    return STATE_DIR / f"{port}.lock"


@contextlib.contextmanager
def claim_port(persona_key: str):
    """Yield a claimed report-tier port (from REPORT_PORTS) for the
    duration of one inference call. Raises PoolBusyError immediately
    (never queues -- report callers already defer to their next scheduled
    cycle on busy, matching common.ollama_lock's report-priority contract)
    if every port is currently held by another claimant."""
    _ensure_dir()
    for port in REPORT_PORTS:
        fd = os.open(str(_lock_path(port)), os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            continue
        try:
            yield port
            return
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    raise PoolBusyError(
        f"llama-pool: all {len(REPORT_PORTS)} report-tier ports currently claimed"
    )
