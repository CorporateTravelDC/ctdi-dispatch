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
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib

HOST = os.getenv("LLAMA_POOL_HOST", "100.x.x.x")
HOT_PORT = int(os.getenv("LLAMA_HOT_PORT", "8093"))
CHAT_PORT = int(os.getenv("LLAMA_CHAT_PORT", "8094"))
REPORT_PORTS = [
    int(p) for p in os.getenv("LLAMA_REPORT_PORTS", "8095,8096").split(",") if p.strip()
]

STATE_DIR = pathlib.Path("/var/lib/corporatetraveldc/llama-pool")


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
