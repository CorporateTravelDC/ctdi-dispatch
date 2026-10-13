#!/usr/bin/env python3
"""scripts/backup/uplink_ramp.py -- the CTDI use of scripts/lib/link_capacity.py (2026-10-12).

Seeds / syncs the Borg repository to the off-box target in timed phases at rising
upload caps, with the live SWIM push feeds as the health signal: the high-cadence
feeds (FDPS, STDDS, ITWS, FNS) are normally seconds old; any of them older than
--max-feed-age stops the ramp. TBFM/TFMS are excluded (quiet for minutes at night
by nature). The measured safe cap and every phase go to the private ledger
~/.local/state/ctdc-backup/uplink-ramp.jsonl (0600); link_capacity.assess() reads it.

  uplink_ramp.py SRC DST [--caps 1,2,4,6,8,12] [--phase-min 10] [--iface wld0]
                 [--max-feed-age 90] [--rclone-prefix "podman unshare"] [--config PATH]
  uplink_ramp.py assess
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import link_capacity as lc  # noqa: E402

LEDGER = os.environ.get("UPLINK_RAMP_LEDGER", str(Path.home() / ".local/state/ctdc-backup/uplink-ramp.jsonl"))
CORE_FEEDS = ("push:fdps", "push:stdds", "push:itws", "push:fns")
ENV_WRAPPER = os.environ.get("UPLINK_RAMP_ENV_WRAPPER",
                             "/opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/with-dispatch-env.sh")


def swim_feed_probe(max_age_s: float) -> lc.Probe:
    """Worst age (s) of the high-cadence SWIM push feeds; ok while <= max_age_s."""
    def probe() -> dict:
        if os.environ.get("UPLINK_RAMP_FAKE_AGES"):
            ages = json.loads(os.environ["UPLINK_RAMP_FAKE_AGES"])
        else:
            try:
                r = subprocess.run([ENV_WRAPPER, "python3", "-"], capture_output=True, text=True, timeout=30, input=(
                    "import json,time\nfrom common import db\nwith db.conn() as c:\n"
                    "    print(json.dumps({r['feed_name']: time.time()-float(r['fetched_at']) for r in "
                    "c.execute(\"SELECT feed_name, fetched_at FROM feed_state WHERE feed_name LIKE 'push:%%'\")}))\n"))
                ages = json.loads(r.stdout.strip().splitlines()[-1])
            except Exception as e:
                return {"ok": True, "value": None, "detail": f"feed ages unreadable ({e.__class__.__name__}); not counted"}
        core = {k: v for k, v in ages.items() if k in CORE_FEEDS}
        worst = round(max(core.values())) if core else None
        return {"ok": worst is None or worst <= max_age_s, "value": worst,
                "detail": ",".join(f"{k}={round(v)}" for k, v in sorted(core.items()))}
    probe.name = "swim-feed-age"  # type: ignore[attr-defined]
    return probe


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["assess"]:
        print(json.dumps(lc.assess(LEDGER), indent=2))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--caps", default="1,2,4,6,8,12")
    ap.add_argument("--phase-min", type=float, default=10)
    ap.add_argument("--iface", default="wld0")
    ap.add_argument("--max-feed-age", type=float, default=90)
    ap.add_argument("--sample-s", type=float, default=30)
    ap.add_argument("--transfers", type=int, default=4)
    ap.add_argument("--rclone-prefix", default="")
    ap.add_argument("--config", default="")
    a = ap.parse_args(argv)
    cmd = " ".join(shlex.split(a.rclone_prefix) + ["rclone"] + (["--config", shlex.quote(a.config)] if a.config else []) +
                   ["sync", "--checksum", "--transfers", str(a.transfers), "--stats", "0",
                    shlex.quote(a.src), shlex.quote(a.dst), "--bwlimit", "{cap}M"])
    cfg = lc.RampConfig(command=cmd, direction="up", iface=a.iface, caps_mb_s=[float(c) for c in a.caps.split(",")],
                        phase_s=a.phase_min * 60, sample_s=a.sample_s, ledger=LEDGER)
    res = lc.ramp(cfg, [swim_feed_probe(a.max_feed_age)], log=lambda m: print(m, flush=True))
    print(json.dumps(lc.assess(LEDGER).get("directions", {}).get("up", {}), indent=2), flush=True)
    return 0 if res["completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
