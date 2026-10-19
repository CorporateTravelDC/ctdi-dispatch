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

~~Only flights still in a forward-looking state are included --
Scheduled / InAir / Delayed. Already-landed (InGate/Landed/OutGate) or
Cancelled flights are excluded even if their published time falls in the
window, since the question this script answers is "what's still coming."~~
2026-10-18 (operator, gate batch): FIDS supplies gate, terminal and baggage
ONLY -- never a time or a status. The FIDS status filter is gone, and the
output no longer carries `status` or `scheduled`; the published time only
selects rows inside the window (in airport-local time) and orders them.
For one flight from every source use GET /api/v1/flights/{ident}/assignments.
~~the published time only selects rows inside the window~~ (2026-10-19,
operator, Gate-1b: the FIDS published time is removed from forward-window
selection entirely. Arrivals now come from common.flight_resolver
.resolve_arrivals() -- SWIM-timed rows only, with their SWIM times (UTC), DCA,
IAD and BWI; a FIDS-only arrival drops out. Departures have no SWIM-timed
forward source here, so --direction departures returns no rows and says so.)
"""
import argparse
import json
import sys

sys.path.insert(0, "/opt/corporatetraveldc/private/ctdi-dispatch-internal/src")

from common.flight_resolver import resolve_arrivals, SUPPORTED_HUB_AIRPORTS  # noqa: E402

# 2026-10-18: FORWARD_LOOKING_STATUSES (a filter on the FIDS status) removed.
# 2026-10-19: the FIDS board is no longer read here at all (published time out
# of the forward window; gate/baggage per flight: /api/v1/flights/{ident}/assignments).


def lookup(airports, carriers, within_minutes, direction):
    results = []
    errors = []
    if direction != "arrivals":
        errors.append(
            "departures: no SWIM-timed forward source in this script; the FIDS "
            "published time is not used for window selection (2026-10-19)."
        )
        return results, errors
    for airport in airports:
        airport = airport.upper()
        if airport not in SUPPORTED_HUB_AIRPORTS:
            errors.append(f"{airport}: not supported (only {', '.join(SUPPORTED_HUB_AIRPORTS)}).")
            continue
        out = resolve_arrivals(airport, carriers or None, within_minutes)
        if out.get("source_used") != "swim":
            if out.get("note"):
                errors.append(f"{airport}: {out['note']}")
            continue
        for r in out.get("results", []):
            results.append({
                "airport": airport,
                "flight": r.get("flight_id") or r.get("flight_num"),
                "airline": r.get("airline"),
                "other_airport": r.get("origin"),
                "arrival_utc": r.get("scheduled"),    # SWIM time, UTC
                "source": "swim",
            })
    results.sort(key=lambda r: (r["arrival_utc"] or "", r["airport"]))
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
        print(f"  {r['flight'] or '?':>8}  {r['airport']}  from {r['other_airport'] or '?':>4}"
              f"  arr {r['arrival_utc'] or '?'}Z (SWIM)")
    for e in errors:
        print(f"  ! {e}")


if __name__ == "__main__":
    main()
