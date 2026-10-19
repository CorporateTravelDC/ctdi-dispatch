"""2026-10-18: the dumpvdl2 channel list lives only in containers/dumpvdl2/launch.sh (FREQ_STRING).
Guards: the DC-area channels are present, dumpvdl2's 8-channel maximum is respected, and every channel
fits inside the sampled bandwidth around the auto (midpoint) centre at 105 kHz x oversample 12."""
from __future__ import annotations

import re
from pathlib import Path

LAUNCH = Path(__file__).resolve().parents[2] / "containers/dumpvdl2/launch.sh"
REQUIRED = {136650000, 136700000, 136725000, 136775000, 136800000, 136825000, 136875000, 136975000}
DROPPED = {136900000, 136925000}   # 0.01% of 14 days' traffic, dropped 2026-10-18
MAX_CHANNELS = 8
SAMPLE_RATE = 105000 * 12


def _freqs() -> list[int]:
    m = re.search(r'^FREQ_STRING="([0-9 ]+)"', LAUNCH.read_text(), re.M)
    assert m, "FREQ_STRING not found"
    return [int(x) for x in m.group(1).split()]


def test_required_channels_present_and_within_limits():
    f = _freqs()
    assert REQUIRED <= set(f)
    assert not (DROPPED & set(f))
    assert len(f) == len(set(f)) <= MAX_CHANNELS
    centre = (min(f) + max(f)) / 2
    half = SAMPLE_RATE / 2 - 25000          # keep a 25 kHz VDL2 channel inside the edge
    assert all(abs(x - centre) <= half for x in f)
