"""
2026-11-04 operator rule: inside a container, 127.0.0.1 or host.containers.internal;
anything that leaves a container or goes into another one uses 100.x.x.x, hard-set.

ntfy runs in its own container, so every first-party unit that can push pins
NTFY_URL to the tailnet address, and no code default falls back to an alias.
Before: the ingest units had no NTFY_URL, the code default host.containers.internal
does not resolve on default pasta networking (--no-map-gw), and every TBFM / TFMS
TMI push for ASH4067 was recorded in watch history but never reached the phone.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
QUADLETS = ROOT / ".config/containers/systemd"
PIN = "http://100.x.x.x:2586"


def _first_party_units():
    out = []
    for f in sorted(QUADLETS.glob("*.container")):
        s = f.read_text()
        m = re.search(r"^Image=localhost/corporatetraveldc-([a-z0-9-]+):", s, re.M)
        if m and not m.group(1).startswith("demo"):     # the demo never pushes to production ntfy
            out.append((f.name, s))
    return out


def test_there_are_first_party_units():
    assert len(_first_party_units()) >= 40


@pytest.mark.parametrize("name,body", _first_party_units(), ids=lambda x: x if isinstance(x, str) and x.endswith(".container") else "")
def test_every_first_party_unit_hardsets_ntfy(name, body):
    pins = re.findall(r"^Environment=NTFY_URL=(\S+)\s*$", body, re.M)
    assert pins == [PIN], f"{name}: NTFY_URL must be hard-set exactly once to {PIN}, found {pins}"


@pytest.mark.parametrize("path", ["src/common/config.py", "src/runner/main.py",
                                  "src/shared/watchlist.py", "src/poller/skills/ingest_feed_watch.py"])
def test_no_code_default_uses_an_alias_for_ntfy(path):
    text = (ROOT / path).read_text()
    for line in text.splitlines():
        if "NTFY" in line and ("=" in line or "get(" in line) and not line.lstrip().startswith("#"):
            assert "host.containers.internal" not in line and "localhost" not in line, f"{path}: {line.strip()}"
    assert PIN in text, f"{path}: the hard-set ntfy address is missing"
