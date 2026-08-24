#!/usr/bin/env python3
"""
scripts/hub_arrivals_lookup.py

Filtered arrivals lookup across DCA and IAD, by carrier and time window.
Built 2026-07-20 after a one-off AA/UA-into-DCA question turned into a
repeatable need. Reuses common.airport_fids.get_data() (same cached fetch
the poller and web app already use -- correct cookie/header auth, 60s
in-process cache) rather than re-implementing the raw fetch.

BWI is NOT supported here -- Baltimore/Washington Intl is operated by the
Maryland Aviation Administration, not MWAA, and has no equivalent free
public FIDS JSON feed (checked 2026-07-20). The only path to BWI arrival
data is FlightAware AeroAPI, and FLIGHTAWARE_API_KEY in
/etc/corporatetraveldc/dispatch-secrets.env is present but empty --
someone needs to decide whether to pay for a key before this can cover BWI.

Union Station (Amtrak, WAS) is NOT this script's job -- it's a different
data shape (train delay/status, not gate-scheduled arrivals) already
served by GET /api/v1/amtrak / the dispatch_get_amtrak MCP tool. Use that
directly instead of extending this script to fake-fit trains into an
airport arrivals shape.

Usage (run from /opt/corporatetraveldc with PYTHONPATH=src, per CLAUDE.md
convention):
    PYTHONPATH=src python3 scripts/hub_arrivals_lookup.py \
        --airports DCA,IAD --carriers AA,UA --within 90

    --airports   comma list, subset of DCA,IAD (default: DCA,IAD)
    --carriers   comma list of IATA carrier codes, e.g. AA,UA,DL (default:
                 no filter -- all carriers)
    --within     minutes from now, forward-looking only (default: 90)
    --direction  arrivals|departures (default: arrivals)
    --json       emit JSON instead of a human-readable table

Only flights still in a forward-looking state are included --
Scheduled / InAir / Delayed. Already-landed (InGate/Landed/OutGate) or
Cancelled flights are excluded even if their published time falls in the
window, since the question this script answers is "what's still coming."
"""
import argparse
import json
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/opt/corporatetraveldc/private/ctdi-dispatch-internal/src")

from common.airport_fids import get_data, _effective_gate, _effective_claim  # noqa: E402

FORWARD_LOOKING_STATUSES = {"Scheduled", "InAir", "Delayed"}


def lookup(airports, carriers, within_minutes, direction):
    now = datetime.now()
    cutoff = now + timedelta(minutes=within_minutes)
    carriers = {c.upper() for c in carriers} if carriers else None

    results = []
    errors = []
    for airport in airports:
        airport = airport.upper()
        if airport == "BWI":
            errors.append(
                "BWI: no free public FIDS feed (MAA-operated, not MWAA). "
                "Would need a funded FlightAware AeroAPI key "
                "(FLIGHTAWARE_API_KEY is currently unset) -- not attempted."
            )
            continue
        if airport not in ("DCA", "IAD"):
            errors.append(f"{airport}: not supported by this script (only DCA/IAD).")
            continue

        data = get_data(airport)
        if data is None:
            errors.append(f"{airport}: fetch failed (see poller logs / feed_state).")
            continue

        flights = data.get(direction, [])
        for f in flights:
            iata = f.get("IATA")
            if carriers and iata not in carriers:
                continue
            status = f.get("status")
            if status not in FORWARD_LOOKING_STATUSES:
                continue
            pub = f.get("publishedTime")
            if not pub:
                continue
            try:
                pub_dt = datetime.strptime(pub, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if not (now <= pub_dt <= cutoff):
                continue

            results.append({
                "airport": airport,
                "carrier": iata,
                "airline": f.get("airline"),
                "flight": f.get("flightnumber"),
                "status": status,
                "scheduled": pub,
                "other_airport": f.get("dep_airport_code") if direction == "arrivals" else f.get("airportcode"),
                "city": f.get("city"),
                "gate": _effective_gate(f),
                "terminal": f.get("arr_terminal") if direction == "arrivals" else f.get("dep_terminal"),
                "baggage_claim": _effective_claim(f, airport) if direction == "arrivals" else None,
            })

    results.sort(key=lambda r: r["scheduled"])
    return results, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--airports", default="DCA,IAD")
    ap.add_argument("--carriers", default="")
    ap.add_argument("--within", type=int, default=90)
    ap.add_argument("--direction", choices=["arrivals", "departures"], default="arrivals")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    airports = [a.strip() for a in args.airports.split(",") if a.strip()]
    carriers = [c.strip() for c in args.carriers.split(",") if c.strip()]

    results, errors = lookup(airports, carriers, args.within, args.direction)

    if args.json:
        print(json.dumps({"results": results, "errors": errors}, indent=2))
        return

    print(f"{len(results)} matching {args.direction} within {args.within}min "
          f"({', '.join(airports)}, carriers={','.join(carriers) or 'ALL'})")
    for r in results:
        line = (f"  {r['carrier']} {r['flight']:>5}  {r['airport']}  "
                f"{r['scheduled'][-8:-3]}  {r['status']:<10}  "
                f"{r['other_airport'] or '?':>4}  gate {r['gate'] or '?'}"
                f"  T{r['terminal'] or '?'}")
        if r.get("baggage_claim"):
            line += f"  claim {r['baggage_claim']}"
        print(line)
    for e in errors:
        print(f"  ! {e}")


if __name__ == "__main__":
    main()
