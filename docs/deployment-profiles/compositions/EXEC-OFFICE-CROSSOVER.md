# Composition: executive-office crossover (CFO, EA, CoS) -- the demo story

> **Template, disabled until after the feature freeze** (staged 2026-10-20 UTC, operator decisions of
> 2026-10-20). File: [`exec-office-crossover.json`](exec-office-crossover.json), compiled by
> `src/common/composition.py`. State `mapped`, both overlays `enabled: false`. Nothing reads compositions at
> runtime.

## Shape

- **Core:** `../profiles/executive-assistant.json`, pinned by SHA-256. The EA is the profile that touches
  dispatch.
- **Overlays:** `cfo-finance` (`../profiles/cfo.json`) and `cos-governance` (`../profiles/chief-of-staff.json`).
  Their `settings` are empty: the overlays point at whole profiles and do not patch the core. By the compiler's
  rules they could not widen the core's audiences anyway, and a key set by both would be an error.
- **Crossover:** one link key, the **trip tag** (`trip-` plus a synthetic id). The calendar event, the documents,
  the ledger lines and the initiative card all carry it. No permission is shared. Each profile keeps its own
  scopes, roles and database; the crossover is tags and links.

## The story (synthetic seed data only)

1. **EA books the travel.** A Nextcloud Calendar event, "Client tour, IAD to SFO, synthetic flight, 2026-11-03",
   category `travel`.
2. **Dispatch watches the flight.** The flight number becomes a watchlist entry through the existing route, so
   the LADD filter applies unchanged. The demo shows the flight-alerts push, the gate/baggage assignment and an EP
   advance for the meeting venue. The adapter that reads the calendar is not built; in the demo the watch is
   seeded.
3. **EA files the documents.** The e-ticket and the car-service receipt go into Paperless-ngx, tagged with the
   trip tag.
4. **CFO sees the cost line.** One Firefly III transaction per receipt, with the trip tag, budget Travel and the
   Paperless link in its notes. The budget view shows spend against plan (variance). A payment over the
   threshold needs a signed approval (`approve_payment`). The platform's own per-trip data cost (the AeroAPI
   gate-call ledger) shows as a cost-of-goods line.
5. **CoS sees the initiative.** A Nextcloud Deck card, "Q4 West-coast client tour", links the event, the ledger
   tag report and a Collectives page, "Decision: fly or rail". A Tables row holds the objective "3 client visits a
   quarter", at 1 of 3.
6. **Profile view.** The three glossary slices (`render-glossary.py --profile cfo | executive-assistant |
   chief-of-staff`) and `composition.compile()` with `diff()` show that each persona sees only its own scopes.

## Rules

- Synthetic data only. The public demo is fed from production only through the demo scrub, and none of this
  touches production.
- No tail in any calendar event or document: tails come from SWIM only.
- Overlay enablement past `shadow` needs a signed activation record, which does not exist yet, so the compiler
  refuses it. Unknown-overlay and missing-dependency rejection (EMCOMM spec §3) must land before overlays are
  relied on.
