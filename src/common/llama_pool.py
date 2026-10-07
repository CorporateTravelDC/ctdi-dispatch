"""
common.llama_pool -- slot discipline for the ONE llama.cpp server.

Current architecture (2026-09-06, operator directive; Qwen3-4B-Instruct
since 2026-09-21): corporatetraveldc-llama.service is the only model
server -- one model resident 24/7, two slots (-np 2), hard-capped at two
cores by CPUQuota=200%, port LLAMA_PORT (8093). HOT_PORT / CHAT_PORT /
REPORT_PORTS are kept as names for importers but all resolve to that one
port. This module's job is the slot rule described below, not port
claiming.

History, kept short: 2026-08-27 Ollama -> llama-server cutover with three
tiers (hot :8093, chat :8094, report-N :8095+), an elastic report pool
that was abandoned the same day (containers cannot spawn host
processes), and report-1 made on-demand 2026-08-30 after near-OOM
incidents. All of that was consolidated into the single unit on
2026-09-06; nothing listens on 8094/8095 any more. (Docstring rewritten
2026-10-03 -- it had still described the three-tier layout as current.)

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

# 2026-10-05: LLAMA_BASE_URL (dispatch.env) is the one address of the server;
# LLAMA_POOL_HOST / LLAMA_PORT still override it individually.
def _from_base_url() -> tuple[str, int]:
    from urllib.parse import urlparse
    u = urlparse(os.getenv("LLAMA_BASE_URL", "") or "")
    return (u.hostname or "100.x.x.x", u.port or 8093)


HOST = os.getenv("LLAMA_POOL_HOST") or _from_base_url()[0]
LLAMA_PORT = int(os.getenv("LLAMA_PORT") or _from_base_url()[1])
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
