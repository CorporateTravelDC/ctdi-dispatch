"""
Regression test for the 2026-08-27 fix (Opus blind review C-2, reinforced
by operator follow-up): the demo runner instance (DEMO_MODE=true, meant
for untrusted/investor-facing viewers) must never touch ANY real-time
ADS-B/ACARS/VDL2/HFDL source -- not this box's local receivers, and not
real third-party sources (airplanes.live, acarsdrama.com, airframes.io)
either. Only fabricated (synthetic) data is acceptable.

The first pass of this fix only skipped the LOCAL fetch on DEMO_MODE,
still falling through to real third-party live data -- not good enough
per the operator's explicit follow-up ("shouldn't be touching anything
real-time, only synthetic"). This file verifies the stronger behavior:
on DEMO_MODE, none of these routes ever call ANY network-touching
function at all -- local or third-party -- and always return the
_synthetic_adsb_snapshot()/_synthetic_signal_messages() generators
instead.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from starlette.requests import Request

from runner import main as runner_main


def _make_request(headers: dict | None = None, client=("127.0.0.1", 12345)) -> Request:
    encoded = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "headers": encoded, "method": "GET",
             "path": "/api/acars/messages", "client": client}
    return Request(scope)


def _boom(*_a, **_kw):
    raise AssertionError("must never touch a real-time source in DEMO_MODE")


# ── ADS-B ─────────────────────────────────────────────────────────────────

def test_adsb_local_demo_mode_returns_synthetic_no_network():
    request = _make_request()
    with patch.object(runner_main, "DEMO_MODE", True), \
         patch("runner.main.httpx.AsyncClient", side_effect=_boom):
        result = asyncio.run(runner_main.adsb_local(request))
    assert result["source"] == "synthetic"
    assert len(result["aircraft"]) > 0


def test_adsb_live_demo_mode_returns_synthetic_no_network():
    """/api/adsb/live is directly reachable too (no gate of its own
    originally) -- must be synthetic-only on DEMO_MODE as well."""
    request = _make_request()
    with patch.object(runner_main, "DEMO_MODE", True), \
         patch("runner.main.httpx.AsyncClient", side_effect=_boom):
        result = asyncio.run(runner_main.adsb_live(request, lat=38.9, lon=-77.0, dist=250))
    assert result["source"] == "synthetic"


def test_adsb_local_non_demo_still_calls_ultrafeeder():
    """Regression guard: the real runner instance must be unaffected."""
    request = _make_request()
    with patch.object(runner_main, "DEMO_MODE", False), \
         patch("runner.main.httpx.AsyncClient") as mock_client_cls:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json = lambda: {"aircraft": []}
        mock_client_cls.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        result = asyncio.run(runner_main.adsb_local(request))
    assert result["source"] == "local"


# ── VDL2 / ACARS / HFDL ──────────────────────────────────────────────────

def test_vdl2_demo_mode_returns_synthetic_no_network():
    request = _make_request()
    with patch.object(runner_main, "DEMO_MODE", True), \
         patch.object(runner_main, "_acarshub_messages", new=AsyncMock(side_effect=_boom)), \
         patch.object(runner_main, "_acarsdrama_messages", new=AsyncMock(side_effect=_boom)), \
         patch.object(runner_main, "_airframes_messages", new=AsyncMock(side_effect=_boom)):
        result = asyncio.run(runner_main.vdl2_messages(request))
    assert result["source"] == "synthetic"
    assert result["count"] > 0


def test_acars_demo_mode_returns_synthetic_no_network():
    request = _make_request()
    with patch.object(runner_main, "DEMO_MODE", True), \
         patch.object(runner_main, "_acarshub_messages", new=AsyncMock(side_effect=_boom)), \
         patch.object(runner_main, "_acarsdrama_messages", new=AsyncMock(side_effect=_boom)), \
         patch.object(runner_main, "_airframes_messages", new=AsyncMock(side_effect=_boom)):
        result = asyncio.run(runner_main.acars_messages(request))
    assert result["source"] == "synthetic"
    assert result["count"] > 0


def test_hfdl_demo_mode_returns_synthetic_no_network():
    request = _make_request()
    with patch.object(runner_main, "DEMO_MODE", True), \
         patch.object(runner_main, "_acarshub_messages", new=AsyncMock(side_effect=_boom)), \
         patch.object(runner_main, "_acarsdrama_messages", new=AsyncMock(side_effect=_boom)), \
         patch.object(runner_main, "_airframes_messages", new=AsyncMock(side_effect=_boom)):
        result = asyncio.run(runner_main.hfdl_messages(request))
    assert result["source"] == "synthetic"
    assert result["count"] > 0


def test_acars_non_demo_still_calls_local_acarshub_first():
    """Regression guard: the real runner instance must be unaffected."""
    request = _make_request()
    with patch.object(runner_main, "DEMO_MODE", False), \
         patch.object(runner_main, "_acarshub_messages", new=AsyncMock(return_value=[{"m": 1}])):
        result = asyncio.run(runner_main.acars_messages(request))
    assert result["source"] == "local"
    assert result["count"] == 1


# ── Synthetic generators themselves ──────────────────────────────────────

def test_synthetic_adsb_snapshot_shape():
    snap = runner_main._synthetic_adsb_snapshot(38.9, -77.0)
    assert snap["source"] == "synthetic"
    assert len(snap["aircraft"]) > 0
    for ac in snap["aircraft"]:
        assert "hex" in ac and "flight" in ac and "lat" in ac and "lon" in ac


def test_synthetic_signal_messages_shape():
    msgs = runner_main._synthetic_signal_messages("acars", count=3)
    assert len(msgs) == 3
    for m in msgs:
        assert m["msg_type"] == "acars"
        assert m["registration"] and m["flight"]


if __name__ == "__main__":
    import traceback
    tests = [
        test_adsb_local_demo_mode_returns_synthetic_no_network,
        test_adsb_live_demo_mode_returns_synthetic_no_network,
        test_adsb_local_non_demo_still_calls_ultrafeeder,
        test_vdl2_demo_mode_returns_synthetic_no_network,
        test_acars_demo_mode_returns_synthetic_no_network,
        test_hfdl_demo_mode_returns_synthetic_no_network,
        test_acars_non_demo_still_calls_local_acarshub_first,
        test_synthetic_adsb_snapshot_shape,
        test_synthetic_signal_messages_shape,
    ]
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
