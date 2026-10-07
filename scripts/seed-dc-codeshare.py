#!/usr/bin/env python3
"""scripts/seed-dc-codeshare.py -- seed/refresh codeshare_map from live DC-metro
flight-plan traffic. Idempotent and re-runnable (this is the core a daily timer
calls).

Why this exists (operator directive 2026-09-27): FDPS files flight plans under
the OPERATING callsign only (e.g. EDV5134) -- the marketing/codeshare number
(DAL5134) never appears in FDPS. So a marketing->operating mapping can't be
DISCOVERED from FDPS alone; it has to be inferred. For US regional "Connection"
flights the marketing flight number EQUALS the operating flight number
(confirmed live: DAL5134/EDV5134, DAL5770/RPA5770, AAL5265/JIA5265), so the only
unknown is the marketing CARRIER -- the mainline the regional flies for.

For WHOLLY-OWNED regionals that is deterministic (Endeavor only flies Delta
Connection, PSA/Envoy only American Eagle, etc.), so we auto-seed those. For
CONTRACT regionals that fly for several mainlines (Republic, SkyWest, Mesa,
GoJet, Air Wisconsin) the carrier is genuinely ambiguous from the operating
callsign + number alone -- we do NOT guess; we count and report them for a
later route/AeroAPI pass. Guessing would lock the wrong mainline onto a flight.

Scope: flights touching the DC-metro airports (BWI/IAD/DCA + the GA/reliever
fields HEF/JYO/CGS/FDK) in the recent window. Mainlines (fly under their own
callsign, no codeshare to resolve) are skipped by construction. Private and
fractional operators (NetJets/EJA, Flexjet/LXJ, bare N-numbers) are NOT dropped
from tracking -- they simply have no marketing<->operating codeshare, so they
do not belong in codeshare_map. Private-jet arrivals into the DC-metro fields
are a SEPARATE monitor lane (tarmac / FBO-meet tracking by tail->hex, per
operator directive 2026-09-27 -- executive pickups at the FBO), handled by the
hifi-track tail-resolution path, not this codeshare seeder. This script only
considers the regionals in the maps below.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from common import db  # noqa: E402

DC_METRO_AIRPORTS = ("KBWI", "KIAD", "KDCA", "KHEF", "KJYO", "KCGS", "KFDK")

# Wholly-owned regionals -> their single mainline parent. Deterministic:
# these carriers fly Connection/Express for exactly one mainline, so the
# operating callsign uniquely implies the marketing carrier. marketing_num ==
# operating_num for US regional codeshares.
EXCLUSIVE_REGIONAL_MARKETING = {
    "EDV": "DAL",   # Endeavor Air -- Delta-owned, Delta Connection only
    "JIA": "AAL",   # PSA Airlines -- American-owned, American Eagle only
    "ENY": "AAL",   # Envoy Air -- American-owned, American Eagle only
    "PDT": "AAL",   # Piedmont Airlines -- American-owned, American Eagle only
    "UCA": "UAL",   # CommutAir -- United Express only
    "QXE": "ASA",   # Horizon Air -- Alaska-owned, Alaska Horizon only
    "JZA": "ACA",   # Jazz Aviation -- Air Canada Express only
}

# 2026-10-05 (JZA825 / AC8825 / UA8366): marketing number == operating number
# is a US-regional rule, NOT universal. Air Canada Express by Jazz markets
# AC(8000 + n) for operating JZA n -- verified live against all 26 AC 8xxx
# flights on the DCA/IAD boards (each with a live FDPS JZA leg on the same
# route). The old equality assumption wrote ACA825 -> JZA825, which no
# marketed number ever matches.
MARKETING_NUM_OFFSET = {
    "JZA": 8000,
}


def marketing_number(op_carrier: str, op_num: str) -> str:
    off = MARKETING_NUM_OFFSET.get(op_carrier)
    if off and op_num.isdigit() and int(op_num) < 1000:
        return str(int(op_num) + off)
    return op_num


# Contract regionals that fly for MULTIPLE mainlines -- ambiguous from callsign
# alone. Not auto-seeded; counted/reported for a later route- or AeroAPI-based
# disambiguation pass.
AMBIGUOUS_REGIONALS = {
    "RPA": "Republic (AAL/DAL/UAL)",
    "SKW": "SkyWest (DAL/AAL/UAL/ASA)",
    "ASH": "Mesa (UAL/AAL)",
    "GJS": "GoJet (UAL/DAL)",
    "AWI": "Air Wisconsin (AAL/UAL, changed operators)",
}

LOOKBACK_SECONDS = int(os.environ.get("DC_CODESHARE_LOOKBACK_SECONDS", str(48 * 3600)))


def main() -> int:
    since = time.time() - LOOKBACK_SECONDS
    airports_sql = ",".join("'%s'" % a for a in DC_METRO_AIRPORTS)
    q = f"""
        SELECT DISTINCT airline, flight_num, origin, destination
        FROM flight_events
        WHERE (origin IN ({airports_sql}) OR destination IN ({airports_sql}))
          AND updated_at > {since}
          AND airline IS NOT NULL AND flight_num IS NOT NULL
    """
    seeded = 0
    ambiguous = {}
    considered = 0
    with db.conn() as c:
        rows = c.execute(q).fetchall()
    for r in rows:
        op_carrier = (r["airline"] or "").upper()
        op_num = str(r["flight_num"]).strip()
        if not op_carrier or not op_num:
            continue
        if op_carrier in AMBIGUOUS_REGIONALS:
            ambiguous[op_carrier] = ambiguous.get(op_carrier, 0) + 1
            continue
        marketing_carrier = EXCLUSIVE_REGIONAL_MARKETING.get(op_carrier)
        if not marketing_carrier:
            continue  # mainline or private -- nothing to resolve
        considered += 1
        try:
            db.upsert_codeshare_mapping(
                marketing_carrier=marketing_carrier,
                marketing_flight_num=marketing_number(op_carrier, op_num),
                operating_carrier=op_carrier,
                operating_flight_num=op_num,
                origin=r["origin"],
                destination=r["destination"],
                source="dc_metro_seed",
            )
            seeded += 1
        except Exception as e:
            print(f"  upsert failed {marketing_carrier}{op_num}->{op_carrier}{op_num}: {e}", file=sys.stderr)

    # Rows the old equality rule wrote for offset carriers (e.g. ACA825 -> JZA825) are wrong; remove them.
    removed = 0
    with db.conn() as c:
        for op_c in MARKETING_NUM_OFFSET:
            cur = c.execute("DELETE FROM codeshare_map WHERE source = 'dc_metro_seed' AND operating_carrier = ? "
                            "AND marketing_flight_num = operating_flight_num", (op_c,))
            removed += cur.rowcount or 0
    fids_added = seed_from_fids_codeshares()

    print(f"DC-metro codeshare seed: {seeded} mappings upserted from {considered} exclusive-regional legs "
          f"(lookback {LOOKBACK_SECONDS//3600}h, {len(rows)} distinct DC-metro legs scanned); "
          f"{removed} wrong-numbered row(s) removed; {fids_added} codeshare(s) from the DCA/IAD boards.")
    if ambiguous:
        total_amb = sum(ambiguous.values())
        print(f"Ambiguous contract regionals NOT auto-seeded ({total_amb} legs) -- need route/AeroAPI disambiguation:")
        for k, n in sorted(ambiguous.items(), key=lambda x: -x[1]):
            print(f"  {k} ({AMBIGUOUS_REGIONALS[k]}): {n} legs")
    return 0


def seed_from_fids_codeshares() -> int:
    """2026-10-05: the DCA/IAD boards list each flight's codeshares (AC8825 ->
    [UA8366]). Map every listed codeshare to the SAME operating flight as the
    board's primary number -- but only when that primary already resolves to an
    operating flight through codeshare_map. Enrichment only (FIDS never asserts
    OOOI); nothing is guessed."""
    from common.airline_codes import IATA_TO_ICAO
    try:
        from common.airport_fids import get_data
    except Exception as e:
        print(f"  FIDS codeshare pass skipped: {e}", file=sys.stderr)
        return 0
    added = 0
    for ap in ("DCA", "IAD"):
        data = get_data(ap) or {}
        for side in ("arrivals", "departures"):
            for f in data.get(side, []):
                prim = IATA_TO_ICAO.get((f.get("IATA") or "").upper())
                num = str(f.get("flightnumber") or "").strip()
                shares = f.get("codeshare") or []
                if not (prim and num and shares):
                    continue
                ops = db.get_codeshare_mapping_by_marketing(prim, num)
                if not ops:
                    continue
                op = ops[0]
                for cs in shares:
                    mk = IATA_TO_ICAO.get((cs.get("IATA") or "").upper())
                    mk_num = str(cs.get("flightnumber") or "").strip()
                    if not (mk and mk_num) or mk == op["operating_carrier"]:
                        continue
                    try:
                        db.upsert_codeshare_mapping(
                            marketing_carrier=mk, marketing_flight_num=mk_num,
                            operating_carrier=op["operating_carrier"], operating_flight_num=op["operating_flight_num"],
                            origin=op.get("origin"), destination=op.get("destination"), source="fids_codeshare")
                        added += 1
                    except Exception as e:
                        print(f"  upsert failed {mk}{mk_num}: {e}", file=sys.stderr)
    return added


if __name__ == "__main__":
    raise SystemExit(main())
