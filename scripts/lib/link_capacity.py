# VENDORED from agentic-management-tooling-mcp agentic/link_capacity.py (2026-10-12).
# Keep the two copies identical below this header; the toolkit copy is the generalized original.
"""
agentic/link_capacity.py -- measured upload/download caps for agent-driven bulk
transfers, plus a sanity check on what the network underneath is doing.

Why this exists
---------------
Agents and automation move bulk data: backups to cloud storage, dataset and model
pulls, sync jobs, artifact uploads. On a link that also carries live traffic
(an API feed, a VPN, a dispatch stream, a household), the transfer cap is a real
guardrail -- too high and the live traffic starves; too low and the job never
finishes. The usual answer is a guessed number. This module replaces the guess
with a measurement, taken on the live link, against the traffic you care about.

Generalized from a production deployment (2026-10): a single-board edge server
seeding an encrypted backup to cloud storage over the same home link that carries
its live aviation data feeds. The first cap there was an arbitrary 2 MB/s; the
ramp measured the real ceiling with the feeds as the health signal.

What it does
------------
ramp():   runs YOUR transfer in timed phases at rising caps (upload or download --
          same algorithm, the direction only selects which interface counter is
          read). Every few seconds it samples the interface byte counter and one or
          more HEALTH PROBES you supply (latency to a host, freshness of a feed,
          any command). It stops raising the cap the moment a probe fails or the
          link stops delivering the cap, then finishes the job at the last cap that
          was healthy. Every sample summary and phase verdict goes to a JSONL ledger.

assess(): reads the ledger(s) and recommends guardrail caps per direction, and
          flags what the network itself is doing:
            * latency inflation under load ("bufferbloat"): the queue in the modem
              or router fills before the link does -- shape below that point;
            * burst-then-police: a high first few seconds then a flat lower rate --
              a provider token-bucket policer; the sustainable rate is the floor;
            * high variability at a fixed cap, or large differences between runs at
              different times of day: a shared / multi-tenant segment (cable node,
              shared office uplink, wireless contention) -- schedule bulk work for
              quiet windows rather than raising caps;
            * link-bound below the cap you asked for: the cap is not the limit, the
              link is.

It is a sanity check and a sizing aid, not a guarantee: one run measures one time
of day. Run it in the windows your jobs actually use and let the ledger accumulate.

Safety
------
A ramp generates real traffic and runs your real transfer, so ramp_run() follows
this package's mutation-gate convention: it does nothing unless confirmed=True.
Interface counters come from /sys/class/net (Linux). Nothing here reads secrets;
the transfer command is yours and runs as you.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

MB = 1_000_000

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def iface_bytes(iface: str, direction: str) -> int:
    """Cumulative bytes on `iface` for 'up' (tx) or 'down' (rx). Linux /sys/class/net."""
    name = "tx_bytes" if direction == "up" else "rx_bytes"
    return int((Path("/sys/class/net") / iface / "statistics" / name).read_text())


# ---------------------------------------------------------------------------
# Health probes: callables returning {"ok": bool, "value": float|None, "detail": str}
# ---------------------------------------------------------------------------

Probe = Callable[[], dict]


def tcp_latency_probe(host: str, port: int = 443, max_ms: float | None = None, timeout_s: float = 3.0) -> Probe:
    """TCP connect time to host:port. ok=False if it fails or exceeds max_ms.
    With max_ms=None the probe only records (useful for the idle baseline)."""
    def probe() -> dict:
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                ms = (time.monotonic() - t0) * 1000
        except OSError as e:
            return {"ok": False, "value": None, "detail": f"connect {host}:{port} failed: {e.__class__.__name__}"}
        ok = max_ms is None or ms <= max_ms
        return {"ok": ok, "value": round(ms, 1), "detail": f"rtt_ms={ms:.1f}"}
    probe.name = f"tcp:{host}:{port}"  # type: ignore[attr-defined]
    return probe


def command_probe(cmd: str, max_value: float | None = None, name: str = "command") -> Probe:
    """Run a shell command. Exit 0 = ok. If it prints a number on its last line and
    max_value is set, the number must be <= max_value (e.g. a feed age in seconds)."""
    def probe() -> dict:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return {"ok": False, "value": None, "detail": "probe timed out"}
        last = (r.stdout.strip().splitlines() or [""])[-1]
        try:
            v = float(last)
        except ValueError:
            v = None
        ok = r.returncode == 0 and (max_value is None or v is None or v <= max_value)
        return {"ok": ok, "value": v, "detail": f"rc={r.returncode}"}
    probe.name = name  # type: ignore[attr-defined]
    return probe


# ---------------------------------------------------------------------------
# Ramp
# ---------------------------------------------------------------------------


@dataclass
class RampConfig:
    command: str                       # your transfer; "{cap}" is replaced with the cap in MB/s (e.g. --bwlimit {cap}M)
    direction: str = "up"              # "up" or "down"
    iface: str = "eth0"
    caps_mb_s: list[float] = field(default_factory=lambda: [1, 2, 4, 6, 8, 12])
    phase_s: float = 600               # length of each phase
    sample_s: float = 30
    busy_mb_s: float = 0.1             # a sample counts as "transferring" above this rate
    min_busy_samples: int = 3
    link_bound_ratio: float = 0.8      # busy rate below this fraction of the cap -> link-bound
    max_inconclusive: int = 3          # phases with too little transfer before giving up on raising
    ledger: str = ""                   # JSONL path; default under $AGENTIC_MCP_STATE_DIR or ~/.local/state
    stop_after_phase: Callable[[dict], bool] | None = None   # testing hook


def _ledger_path(cfg: RampConfig) -> Path:
    if cfg.ledger:
        return Path(cfg.ledger)
    base = os.environ.get("AGENTIC_MCP_STATE_DIR") or str(Path.home() / ".local/state/agentic-mcp")
    return Path(base) / "link-capacity.jsonl"


def _record(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **rec}, sort_keys=True) + "\n")


def _probe_all(probes: list[Probe]) -> tuple[bool, dict]:
    out, ok = {}, True
    for p in probes:
        r = p()
        out[getattr(p, "name", "probe")] = r
        ok = ok and bool(r.get("ok"))
    return ok, out


def run_phase(cfg: RampConfig, cap: float | None, probes: list[Probe], timed: bool, enforce: bool,
              counter: Callable[[], int] | None = None, launcher=None) -> dict:
    """One phase: start the transfer at `cap`, sample, stop at phase end (timed) or on probe failure (enforce)."""
    counter = counter or (lambda: iface_bytes(cfg.iface, cfg.direction))
    cmd = cfg.command.replace("{cap}", f"{cap:g}" if cap else "0")
    launcher = launcher or (lambda c: subprocess.Popen(c, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True))
    b0 = last_b = counter(); t0 = last_t = time.monotonic()
    proc = launcher(cmd)
    rates, probe_values, failed, stop_reason = [], {}, None, ""
    while proc.poll() is None:
        time.sleep(cfg.sample_s)
        b, t = counter(), time.monotonic()
        rate = (b - last_b) / max(1e-3, t - last_t) / MB; last_b, last_t = b, t
        rates.append(rate)
        ok, res = _probe_all(probes)
        for k, v in res.items():
            if v.get("value") is not None:
                probe_values.setdefault(k, []).append(v["value"])
        if not ok:
            failed = {k: v for k, v in res.items() if not v.get("ok")}
            if enforce:
                stop_reason = "probe failed"; proc.terminate(); break
        if timed and t - t0 >= cfg.phase_s:
            stop_reason = "phase time"; proc.terminate(); break
    try:
        proc.wait(timeout=60)
    except Exception:
        proc.kill()
    dt = max(1e-3, time.monotonic() - t0)
    busy = [r for r in rates if r >= cfg.busy_mb_s]
    head, tail = busy[: max(1, len(busy) // 4)], busy[len(busy) // 4:]
    return {
        "cap_mb_s": cap, "seconds": round(dt), "avg_mb_s": round((counter() - b0) / dt / MB, 3),
        "busy_median_mb_s": round(statistics.median(busy), 3) if busy else None,
        "busy_samples": len(busy),
        "busy_cv": round(statistics.pstdev(busy) / statistics.mean(busy), 3) if len(busy) >= 3 else None,
        "head_median_mb_s": round(statistics.median(head), 3) if len(busy) >= 4 else None,
        "tail_median_mb_s": round(statistics.median(tail), 3) if len(busy) >= 4 else None,
        "probe_medians": {k: round(statistics.median(v), 2) for k, v in probe_values.items()},
        "probe_failures": failed, "stop_reason": stop_reason or "transfer finished",
        "transfer_rc": proc.returncode if stop_reason == "" else None,
    }


def ramp(cfg: RampConfig, probes: list[Probe], counter=None, launcher=None, log=print) -> dict:
    """Run the ramp. Returns {"safe_cap_mb_s", "phases", "completed", "ledger"}."""
    led = _ledger_path(cfg)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _, baseline = _probe_all(probes)
    _record(led, {"event": "start", "run": run_id, "direction": cfg.direction, "iface": cfg.iface,
                  "caps_mb_s": cfg.caps_mb_s, "phase_s": cfg.phase_s, "baseline": baseline})
    phases, good, i, inconclusive = [], None, 0, 0
    while i < len(cfg.caps_mb_s):
        cap = cfg.caps_mb_s[i]
        r = run_phase(cfg, cap, probes, timed=True, enforce=True, counter=counter, launcher=launcher)
        bm = r["busy_median_mb_s"]
        if r["probe_failures"]:
            verdict = "degraded"
        elif r["stop_reason"] == "transfer finished":
            verdict = "completed"
        elif bm is None or r["busy_samples"] < cfg.min_busy_samples:
            verdict = "inconclusive"
        elif bm < cfg.link_bound_ratio * cap:
            verdict = "link-bound"
        else:
            verdict = "ok"
        r["verdict"] = verdict
        phases.append(r)
        _record(led, {"event": "phase", "run": run_id, "direction": cfg.direction, **r})
        log(f"[{cfg.direction}] cap {cap:g} MB/s: busy median {bm} MB/s ({r['busy_samples']} samples) -> {verdict}")
        if cfg.stop_after_phase and cfg.stop_after_phase(r):
            break
        if verdict == "completed":
            _record(led, {"event": "end", "run": run_id, "direction": cfg.direction, "safe_cap_mb_s": cap, "completed": True})
            return {"safe_cap_mb_s": cap, "phases": phases, "completed": True, "ledger": str(led)}
        if verdict == "degraded":
            break
        if verdict == "inconclusive":
            inconclusive += 1
            if inconclusive < cfg.max_inconclusive:
                continue
            break
        inconclusive = 0
        if verdict == "link-bound":
            break          # the link, not the cap, is the limit: keep the last cap it fully delivered
        good = cap
        i += 1
    final = good if good else round(cfg.caps_mb_s[0] / 2, 3)
    log(f"[{cfg.direction}] finishing at {final:g} MB/s (last healthy cap)")
    r = run_phase(cfg, final, probes, timed=False, enforce=False, counter=counter, launcher=launcher)
    _record(led, {"event": "end", "run": run_id, "direction": cfg.direction, "safe_cap_mb_s": final,
                  "completed": r["stop_reason"] == "transfer finished" and r["transfer_rc"] == 0, **r})
    return {"safe_cap_mb_s": final, "phases": phases + [r], "completed": r.get("transfer_rc") == 0, "ledger": str(led)}


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def assess(ledger: str | None = None, headroom: float = 0.8) -> dict:
    """Recommend guardrail caps per direction from every run in the ledger, and flag
    shaping / shared-link signals. headroom: fraction of the measured safe cap to use."""
    path = Path(ledger) if ledger else _ledger_path(RampConfig(command=""))
    if not path.exists():
        return {"error": f"no ledger at {path}"}
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    out: dict = {"ledger": str(path), "directions": {}}
    for d in ("up", "down"):
        ends = [r for r in rows if r.get("event") == "end" and r.get("direction") == d]
        ph = [r for r in rows if r.get("event") == "phase" and r.get("direction") == d]
        if not ends and not ph:
            continue
        safe = [e["safe_cap_mb_s"] for e in ends if e.get("safe_cap_mb_s")]
        flags, notes = [], []
        bound = [p for p in ph if p.get("verdict") == "link-bound" and p.get("busy_median_mb_s")]
        if bound:
            notes.append(f"link delivered at most {max(p['busy_median_mb_s'] for p in bound):g} MB/s when asked for more")
        cvs = [p["busy_cv"] for p in ph if p.get("busy_cv") is not None and p.get("verdict") in ("ok", "link-bound")]
        if cvs and statistics.median(cvs) > 0.25:
            flags.append("variable-throughput")
            notes.append("throughput swings a lot at a fixed cap (median CV %.2f): shared/multi-tenant segment or "
                         "wireless contention -- prefer quiet-window scheduling over higher caps" % statistics.median(cvs))
        police = [p for p in ph if p.get("head_median_mb_s") and p.get("tail_median_mb_s")
                  and p["head_median_mb_s"] > 1.4 * p["tail_median_mb_s"]]
        if police:
            flags.append("burst-then-police")
            notes.append("a fast start then a lower flat rate: a provider token-bucket policer; the sustainable rate "
                         "is the tail (%g MB/s) -- cap below it" % min(p["tail_median_mb_s"] for p in police))
        lat = []
        for s in [r for r in rows if r.get("event") == "start" and r.get("direction") == d]:
            for k, v in (s.get("baseline") or {}).items():
                if v.get("value") is not None and k.startswith("tcp:"):
                    lat.append((k, v["value"]))
        for k, base in lat:
            loaded = [p["probe_medians"].get(k) for p in ph if p.get("probe_medians", {}).get(k) is not None]
            if loaded and max(loaded) > max(2 * base, base + 50):
                flags.append("latency-inflation")
                notes.append(f"{k}: idle {base:g} ms -> {max(loaded):g} ms under load: queues fill before the link "
                             "does (bufferbloat); a shaper (fq_codel/cake) set just below the measured cap fixes it")
                break
        if len(safe) >= 3 and statistics.pstdev(safe) / statistics.mean(safe) > 0.3:
            flags.append("time-of-day-variance")
            notes.append("safe caps differ a lot between runs: capacity depends on when you run -- schedule by window")
        rec = round(min(safe) * headroom, 3) if safe else None
        out["directions"][d] = {
            "runs": len(ends), "safe_caps_mb_s": safe,
            "recommended_cap_mb_s": rec, "recommended_cap_mbit_s": round(rec * 8, 1) if rec else None,
            "basis": f"{int(headroom * 100)}% of the lowest safe cap measured" if rec else "no completed run yet",
            "flags": sorted(set(flags)), "notes": notes,
            "needs_shaping": bool({"latency-inflation", "burst-then-police"} & set(flags)),
            "shared_link_suspected": bool({"variable-throughput", "time-of-day-variance"} & set(flags)),
        }
    return out


# ---------------------------------------------------------------------------
# MCP-facing wrappers
# ---------------------------------------------------------------------------


def link_ramp_run(command: str, direction: str = "up", iface: str = "eth0", caps_mb_s: list[float] | None = None,
                  phase_seconds: float = 600, latency_host: str | None = None, latency_max_ms: float | None = None,
                  probe_command: str | None = None, probe_max_value: float | None = None,
                  ledger: str = "", confirmed: bool = False) -> dict:
    """Mutation-gated: runs real traffic. Without confirmed=True returns the plan only."""
    caps = caps_mb_s or [1, 2, 4, 6, 8, 12]
    probes: list[Probe] = []
    if latency_host:
        probes.append(tcp_latency_probe(latency_host, max_ms=latency_max_ms))
    if probe_command:
        probes.append(command_probe(probe_command, max_value=probe_max_value, name="health-command"))
    plan = {"direction": direction, "iface": iface, "caps_mb_s": caps, "phase_seconds": phase_seconds,
            "probes": [getattr(p, "name", "probe") for p in probes],
            "worst_case_minutes": round(len(caps) * phase_seconds / 60, 1),
            "command": command}
    if not probes:
        return {"blocked": True, "reason": "at least one health probe (latency_host or probe_command) is required: "
                "a ramp without a health signal can only find the link limit, not the safe limit", "plan": plan}
    if not confirmed:
        return {"blocked": True, "reason": "this generates real traffic and runs your transfer; call again with "
                "confirmed=True", "plan": plan}
    cfg = RampConfig(command=command, direction=direction, iface=iface, caps_mb_s=caps, phase_s=phase_seconds, ledger=ledger)
    res = ramp(cfg, probes, log=lambda *_: None)
    return {"blocked": False, **res, "assessment": assess(res["ledger"])}


def link_assessment(ledger: str = "", headroom: float = 0.8) -> dict:
    """Read-only: recommended upload/download caps and shaping/shared-link flags from the ledger."""
    return assess(ledger or None, headroom)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Measured transfer caps (upload/download) with health probes.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("up", "down"):
        s = sub.add_parser(name, help=f"ramp a {'n upload' if name == 'up' else ' download'} transfer")
        s.add_argument("--command", required=True, help='your transfer; "{cap}" = cap in MB/s, e.g. "rclone sync SRC DST --bwlimit {cap}M"')
        s.add_argument("--iface", required=True)
        s.add_argument("--caps", default="1,2,4,6,8,12")
        s.add_argument("--phase-min", type=float, default=10)
        s.add_argument("--sample-s", type=float, default=30)
        s.add_argument("--latency-host", help="host:port whose connect time is a health probe")
        s.add_argument("--latency-max-ms", type=float)
        s.add_argument("--probe-command", help="shell command; exit 0 = healthy; optional number on last line")
        s.add_argument("--probe-max", type=float)
        s.add_argument("--ledger", default="")
    a = sub.add_parser("assess"); a.add_argument("--ledger", default=""); a.add_argument("--headroom", type=float, default=0.8)
    args = ap.parse_args(argv)
    if args.cmd == "assess":
        print(json.dumps(assess(args.ledger or None, args.headroom), indent=2)); return 0
    probes: list[Probe] = []
    if args.latency_host:
        h, _, p = args.latency_host.partition(":")
        probes.append(tcp_latency_probe(h, int(p or 443), max_ms=args.latency_max_ms))
    if args.probe_command:
        probes.append(command_probe(args.probe_command, max_value=args.probe_max, name="health-command"))
    if not probes:
        ap.error("give at least one health probe (--latency-host and/or --probe-command)")
    cfg = RampConfig(command=args.command, direction=args.cmd, iface=args.iface,
                     caps_mb_s=[float(c) for c in args.caps.split(",")], phase_s=args.phase_min * 60,
                     sample_s=args.sample_s, ledger=args.ledger)
    res = ramp(cfg, probes)
    print(json.dumps(assess(res["ledger"]), indent=2))
    return 0 if res["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
