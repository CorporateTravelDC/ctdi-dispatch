#!/usr/bin/env python3
"""scripts/platform-sentinel.py -- run one platform-side sentinel (2026-11-18). Host-side, as the
operator's user units (corporatetraveldc-*-sentinel timers); see common/platform_sentinels.py.

  platform-sentinel.py plan-coverage [--day YYYY-MM-DD]      fleet-wide pre-departure plan coverage
  platform-sentinel.py ground-stop [--replay START END]       ground-stop tripwire (replay: no pushes)
  platform-sentinel.py artcc [--day YYYY-MM-DD]               per-centre FDPS first-message sentinel
  platform-sentinel.py agent-lapse run|status                 agent-lapse sentinel
  platform-sentinel.py gateway-hold run|status                cloud-agent link keeper (holds, grants, tripwire)
  platform-sentinel.py agent-lapse dismiss --account X [--for 24h] --reason TEXT
                                                              (use scripts/ctdc-agent-lapse.sh)

Every sentinel run is fail-soft: an error is logged with its reason and the exit code is 0, so a
transient cause never marks the timer failed. `dismiss` and `--replay` report errors with exit 2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
# Host process (systemd --user), not a container: ntfy is on loopback here (cowork-coord-24h-check.sh).
if not Path("/run/.containerenv").exists():
    os.environ.setdefault("NTFY_URL", "http://127.0.0.1:2586")

from common import platform_sentinels as ps  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="platform-sentinel.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan-coverage"); p.add_argument("--day")
    g = sub.add_parser("ground-stop"); g.add_argument("--replay", nargs=2, metavar=("START", "END"))
    a = sub.add_parser("artcc"); a.add_argument("--day")
    lp = sub.add_parser("agent-lapse"); lp.add_argument("action", choices=("run", "status", "dismiss"))
    lp.add_argument("--account"); lp.add_argument("--for", dest="for_", default="24h"); lp.add_argument("--reason")
    lp.add_argument("--by"); lp.add_argument("--via", default="ssh")
    gh = sub.add_parser("gateway-hold"); gh.add_argument("action", choices=("run", "status"))
    args = ap.parse_args(argv)

    if args.cmd == "gateway-hold":
        from common import gateway_hold
        if args.action == "run":
            return ps.run_safely("gateway-hold", gateway_hold.run)
        for r in gateway_hold.current():
            print(f"{r['slug']:<10} {r['status']:<12} hold {ps.fmt(r.get('hold_until'))}  grant {ps.fmt(r.get('grant_until'))}  "
                  f"renewed {ps.fmt(r.get('last_renewal_at'))}  call {ps.fmt(r.get('last_call_at'))}")
        return 0

    if args.cmd == "plan-coverage":
        from common import plan_coverage
        return ps.run_safely("plan-coverage", plan_coverage.run, args.day)
    if args.cmd == "artcc":
        from common import artcc_sentinel
        return ps.run_safely("artcc-sentinel", artcc_sentinel.run, args.day)
    if args.cmd == "ground-stop":
        from common import ground_stop_tripwire as gst
        if args.replay:
            try:
                r = gst.run_replay(*args.replay)
            except (ValueError, Exception) as e:   # noqa: BLE001
                print(f"replay failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
                return 2
            for ev in r["events"]:
                fac = ",".join(ev.get("facilities") or []) or ev.get("facility", "")
                print(f"{ev['at_fmt']}  {ev['event']:<11} {fac}")
            print(f"{len(r['events'])} event(s); wide_n={r['wide_n']}; {r['limits']}")
            return 0
        return ps.run_safely("ground-stop-tripwire", gst.run)
    # agent-lapse
    from common import agent_lapse
    if args.action == "run":
        return ps.run_safely("agent-lapse", agent_lapse.run)
    if args.action == "status":
        for r in agent_lapse.current():
            d = r.get("detail") or {}
            print(f"{r['account']:<36} stage={r['stage']:<10} sent={r.get('stage_sent') or '-':<9} "
                  f"last={ps.fmt(r.get('last_activity'))}  silent={ps.hours(d.get('silent_s'))}")
        return 0
    try:
        if not args.account or not args.reason:
            raise ValueError("dismiss needs --account and --reason")
        r = agent_lapse.dismiss(args.account, agent_lapse.parse_for(args.for_), args.reason,
                                by=args.by or "unknown", via=args.via)
    except ValueError as e:
        print(f"not dismissed: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: r[k] for k in ("account", "until_fmt", "by", "via")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
