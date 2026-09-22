# CIFP data — fixes, procedures, holds

Built 2026-09-05. FAA CIFP (Coded Instrument Flight Procedures, ARINC
424-18) gives authoritative coordinates and published procedure/hold
detail for the Washington/Baltimore terminal area, closing a gap
`tbfm_parser.py`'s meter-fix work flagged as its "honest next step"
(`corporatetraveldc/01-Sources/manual/20260831T230659Z.md` in the second
brain): pulling real CIFP data instead of continuing ad-hoc web search
per fix.

## Pipeline

1. **`src/poller/skills/faa_cifp_pull.py`** — weekly timer (Thu 08:00 ET),
   downloads the current 28-day cycle's zip from AeroNav's public
   download page (genuinely public, no login). Keeps only the current
   cycle on disk (`/var/lib/corporatetraveldc/faa-cifp/CIFP_<cycle>.zip`).
2. **`src/poller/skills/faa_cifp_parse.py`** — weekly timer (Thu 08:20 ET,
   20 min after the pull), extracts `FAACIFP18` from that zip in memory
   and parses it into three DB tables (below). Idempotent — skips if the
   cycle already matches what's stored.
3. **`src/common/cifp_lookup.py`** — the only module anything else should
   query these tables through. `resolve_fix()`, `get_procedure_transitions()`,
   `get_holds()`, `expand_arrival_route()`.

Column map (db.py's `SCHEMA_V45` / `faa_cifp_parse.py`'s parsing) was
verified 2026-09-05 by byte-inspection of the real cycle 2609 file, not
from memory of the ARINC 424-18 spec — several commonly-cited layouts are
off by one in the altitude/speed block. Independently re-derived twice
against the same real file (once during research, once as the production
module) and both times reproduced the exact same counts: 5,060 primary
legs, 135 hold legs, 242 procedures, across the 21-airport scope below.

## Scope

21 airports: KIAD KDCA KBWI KADW KHEF KJYO KMTN KDMW KDAA KNYG KGAI KFDK
KFME KCGS KVKX KESN KOKV KMRB KRMN KW32 KAPG.

`cifp_fixes` scope is deliberately wider than a flat geographic box: it's
the union of every fix physically inside lat 37.3–40.7 / lon −80.5 to
−74.3 **and** every distinct `(fix, fix_region)` actually referenced by a
leg of one of these 21 airports' procedures — the union is what makes an
out-of-box enroute-transition fix like HVQ (Charleston, WV — used as
GIBBZ6/TRUPS6's Charleston transition, well outside the box) resolvable.
A box-only catalog would silently miss it.

## Tables (`src/common/db.py` `SCHEMA_V45`)

- **`cifp_fixes`** — `(ident, icao_region)` primary key. `type` is one of
  `VHF_NAVAID`, `NDB`, `ENROUTE_WPT`, `TERMINAL_WPT`, `RUNWAY_THRESHOLD`,
  `AIRPORT`. **Duplicate idents across regions are real** — always
  resolve by `(ident, icao_region)` when you have the region (e.g. from
  a leg's `fix_region`), never by ident alone. `cifp_lookup.resolve_fix()`
  without a region only succeeds when the ident is unambiguous across
  every stored region; ambiguous cases return `None`, never a guess.
- **`cifp_procedure_legs`** — every primary leg (continuation records
  already excluded at parse time) of every SID/STAR/approach at the 21
  airports, in `(airport, proc_type, procedure, transition, seq)` order.
  `transition = '(common)'` is the shared body portion; a transition
  starting `RW` is a runway-specific transition. Legs with a blank `fix`
  are heading legs (`VA`/`CA`/`VI`) — kept in sequence with null lat/lon,
  never dropped (dropping them corrupts leg order downstream).
- **`cifp_holds`** — every leg whose path/terminator is `HA`/`HF`/`HM`.
  `HM` ("hold, manual — remain until ATC clears you out") is the closest
  published analogue to an ATC-discretion metering hold.

`cifp_meta` tracks the currently-loaded `cycle` and `last_full_import`
timestamp. A parse with zero fixes or legs is refused, not applied — same
lesson as the FAA LADD privacy-list wipe (C-31): an empty result means
the fetch/parse failed, not that the data should be cleared.

## Hard limits — document, don't paper over

1. CIFP contains only holds **charted as part of a published procedure**.
   Enroute holding patterns depicted only on enroute high/low charts are
   **not** in this data.
2. Potomac TRACON's internal metering fixes and airborne-holding fixes
   assigned verbally are **not published data anywhere** — not CIFP, not
   NASR, not d-TPP. If a future feature needs those, they have to come
   from observed ADS-B track clustering or a facility SOP, flagged as
   inferred, never as authoritative.
3. An `HM` leg at a fix is the closest published proxy for a metering
   hold — but the *absence* of an `HM` leg at a fix does **not** mean ATC
   won't hold you there.
4. `expand_arrival_route()` picks a runway transition only when exactly
   one exists with legs. A STAR commonly fans out to several runway
   transitions; without a cleared runway, which one will actually be
   flown is genuinely unknown — the function returns the entry+common
   legs only rather than guessing a runway.

## Known-resolved / known-unresolved fixes (tbfm_parser.py cross-check)

`tbfm_parser.py`'s `DC_METER_FIXES` dict was checked against this data
(2026-09-05) as a second, independent confirmation of an earlier
2026-09-02 check against the general NASR FIX file. Same result both
times: PALEO/RAVNN/SWANN/WOOLY/FLUKY are real, byte-identical DC-area
CIFP fixes; LUCIT/JIMBO/WAVER/MERIT exist nationally under other
regions but are referenced by zero DC-area procedure legs; SFARA has no
record anywhere. See that file's own comment for the full history.

## Not yet done

`expand_arrival_route()` is a tested, ready-to-use utility — it is
**not** wired into `ingest/parsers/fdps_parser.py`'s live route-parsing
path (`_parse_nas_route()`). That parser currently tokenizes a filed
route string into origin/dep_proc/body/arr_entry_fix/arr_proc/dest as
pure strings, with no coordinate resolution. Wiring `expand_arrival_route()`
into that path (or a downstream consumer of `fdps_route_versions`) is a
real, scoped next step for whatever needs an actual route polyline (e.g.
a future hole-detection/coverage-gap feature) — deliberately not done
this pass, same as RVR's plumbing-without-scoring precedent, pending a
concrete consumer and its own review.
