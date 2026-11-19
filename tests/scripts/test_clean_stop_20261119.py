"""2026-11-19: pusher and poller stop cleanly when the load guard stops them.

On 11-19 at 00:22Z the load guard's stop hit podman's default 10 s StopTimeout, so the
pusher was SIGKILLed (exit 137) and recorded as a failed unit. The loops now wake on
SIGTERM instead of sleeping out their interval, and the quadlets give an in-flight
cycle time to finish.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUADLETS = ROOT / ".config/containers/systemd"


def _quadlet(name: str) -> str:
    return (QUADLETS / f"{name}.container").read_text()


def _section(text: str, name: str) -> str:
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
    assert m, f"no [{name}] section"
    return m.group(1)


def test_quadlets_allow_a_clean_stop():
    for unit in ("corporatetraveldc-pusher", "corporatetraveldc-poller"):
        text = _quadlet(unit)
        stop = int(re.search(r"^StopTimeout=(\d+)$", _section(text, "Container"), re.M).group(1))
        svc = int(re.search(r"^TimeoutStopSec=(\d+)$", _section(text, "Service"), re.M).group(1))
        assert stop >= 30, unit
        assert svc > stop, f"{unit}: systemd must outwait podman's stop timeout"


def test_loops_wake_on_shutdown_not_sleep():
    pusher = (ROOT / "src/pusher/main.py").read_text()
    poller = (ROOT / "src/poller/main.py").read_text()
    assert "await asyncio.sleep(PUSH_INTERVAL)" not in pusher
    assert "wait_for(shutdown.wait(), timeout=PUSH_INTERVAL)" in pusher
    assert "await asyncio.sleep(10)  # Tight loop" not in poller
    assert "wait_for(shutdown.wait(), timeout=10)" in poller


def test_wait_pattern_returns_as_soon_as_shutdown_is_set():
    async def run() -> float:
        shutdown = asyncio.Event()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        loop.call_later(0.05, shutdown.set)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        return loop.time() - t0

    assert asyncio.run(run()) < 2.0
