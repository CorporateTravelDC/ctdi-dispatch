#!/usr/bin/env python3
"""
data-usage snapshot — daily logger, mirrors SR-1 (api-usage.csv) pattern.
Reads vnstat JSON for wld0 + tailscale0, appends one row per interface to data-usage.csv.
Schedule: daily at 00:05 ET (00:05 after midnight so vnstat 5-min window closes cleanly).
Retroactive: if running for the first time, back-fills from boot date to yesterday with zeros
             then seeds today with the day's running total so far.

CSV columns: date, interface, rx_bytes, tx_bytes, total_bytes, rx_gb, tx_gb, total_gb
"""

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

USAGE_LOG = Path("/var/lib/corporatetraveldc/data-usage.csv")
INTERFACES = ["wld0", "tailscale0"]
_FIELDS = ["date", "interface", "rx_bytes", "tx_bytes", "total_bytes", "rx_gb", "tx_gb", "total_gb"]


def _vnstat_day(iface: str, target_date: date) -> dict | None:
    """Return vnstat day record for iface on target_date, or None."""
    try:
        result = subprocess.run(
            ["vnstat", "-i", iface, "--json", "d"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        for iface_data in data.get("interfaces", []):
            for day in iface_data.get("traffic", {}).get("day", []):
                d = day.get("date", {})
                if (d.get("year") == target_date.year and
                        d.get("month") == target_date.month and
                        d.get("day") == target_date.day):
                    return {"rx": day.get("rx", 0), "tx": day.get("tx", 0)}
    except Exception:
        pass
    return None


def _vnstat_today_running(iface: str) -> dict:
    """Return running today total from vnstat (may be partial day)."""
    today = date.today()
    result = _vnstat_day(iface, today)
    if result:
        return result
    # Fall back to kernel counters if vnstat hasn't ticked yet
    try:
        rx = int(Path(f"/sys/class/net/{iface}/statistics/rx_bytes").read_text())
        tx = int(Path(f"/sys/class/net/{iface}/statistics/tx_bytes").read_text())
        return {"rx": rx, "tx": tx}
    except Exception:
        return {"rx": 0, "tx": 0}


def _already_logged(target_date: date, iface: str) -> bool:
    if not USAGE_LOG.exists():
        return False
    with USAGE_LOG.open() as f:
        for row in csv.DictReader(f):
            if row.get("date") == str(target_date) and row.get("interface") == iface:
                return True
    return False


def _append_row(target_date: date, iface: str, rx: int, tx: int) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    write_header = not USAGE_LOG.exists() or USAGE_LOG.stat().st_size == 0
    total = rx + tx
    with USAGE_LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(_FIELDS)
        w.writerow([
            str(target_date),
            iface,
            rx, tx, total,
            round(rx / 1e9, 4),
            round(tx / 1e9, 4),
            round(total / 1e9, 4),
        ])


def main() -> None:
    today = date.today()

    # Back-fill any missing days from boot date to yesterday with vnstat data
    boot_date = today  # vnstat only has data since daemon started — skip older dates
    try:
        result = subprocess.run(
            ["vnstat", "--json", "d"], capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        for iface_data in data.get("interfaces", []):
            iface = iface_data.get("name", "")
            if iface not in INTERFACES:
                continue
            for day in iface_data.get("traffic", {}).get("day", []):
                d = day.get("date", {})
                try:
                    day_date = date(d["year"], d["month"], d["day"])
                except (KeyError, ValueError):
                    continue
                if day_date >= today:
                    continue
                if _already_logged(day_date, iface):
                    continue
                _append_row(day_date, iface, day.get("rx", 0), day.get("tx", 0))
    except Exception:
        pass

    # Log today's running total
    for iface in INTERFACES:
        if _already_logged(today, iface):
            continue
        running = _vnstat_today_running(iface)
        _append_row(today, iface, running["rx"], running["tx"])

    print(f"data-usage: snapshot complete for {today}")


if __name__ == "__main__":
    main()
