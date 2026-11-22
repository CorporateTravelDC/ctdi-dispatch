"""2026-11-22 (operator): ultrafeeder's Beast output (30005, raw aircraft positions) is tailnet-only, like its web
ports, and every feeder that reads it reaches it at the tailnet IP (a scoped bind is not routed through
host.containers.internal on this host's container networking)."""
from __future__ import annotations

from pathlib import Path

Q = Path(__file__).resolve().parents[2] / ".config/containers/systemd"
TAILNET = "100.x.x.x"
FEEDERS = ("airnavradar", "fr24feed", "opensky", "piaware", "planefinder")


def _lines(name):
    return [l.strip() for l in (Q / f"corporatetraveldc-{name}.container").read_text().splitlines()
            if l.strip() and not l.strip().startswith("#")]


def test_every_ultrafeeder_port_is_scoped_to_loopback_or_the_tailnet():
    ports = [l.split("=", 1)[1] for l in _lines("ultrafeeder") if l.startswith("PublishPort=")]
    assert ports, "ultrafeeder publishes no ports?"
    for p in ports:
        assert p.startswith((f"{TAILNET}:", "127.0.0.1:")), f"unscoped PublishPort={p}"
    assert f"{TAILNET}:30005:30005" in ports


def test_every_feeder_reads_beast_at_the_tailnet_ip():
    for f in FEEDERS:
        hosts = [l for l in _lines(f) if l.startswith("Environment=BEASTHOST=")]
        assert hosts == [f"Environment=BEASTHOST={TAILNET}"], f"{f}: {hosts}"
