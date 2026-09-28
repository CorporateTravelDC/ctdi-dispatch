# OOOI Source Authority — SWIM as Validator of Record

**Written 2026-09-28.** Code-validated. Documents the source-authority model
that governs which feed is *entitled* to assert an OOOI phase (OUT/OFF/ON/IN),
introduced by migration **0065** (`src/common/pg_schema/0065_oooi_authority_lock.sql`)
and enforced in `src/common/db.py::_oooi_authority_check()`.

---

## The failure this exists to prevent

Two live false-positive landings on one evening (2026-09-23), quoted verbatim in
the migration header (`0065_…sql:6-14`):

- **UA1240** — swept "landed (oooi_phase=in, ACARS/ADS-B confirmed)" while
  verifiably at FL370 over southern Ohio, 504 kts, ADS-B age 0.0 s. SWIM said
  `flight_status=ACTIVE` with ON at 01:06Z and IN at 01:11Z — both still in the
  **future**. The one source with real authority disagreed and was never asked.
- **UA2408** — same sweep, **3.5 hours** before scheduled arrival.

Root cause (`0065_…sql:16-25`): nothing recorded **which source was entitled** to
assert the phase. `watchlist.py`'s sweep fires on `phase == "in"` alone, and its
reason string `"ACARS/ADS-B confirmed"` is a **hardcoded literal** — it claims
corroboration regardless of what actually wrote the phase. Four writers
(`_check_flight_airplanes_live`, `_check_flight_schedule_inference`,
`_check_flight_fdps_cache`, `_check_flight_fids`) all write `oooi_phase`, the
last two unconditionally every tick, last-write-wins.

---

## The rule (operator, verbatim intent — `0065_…sql:27-38`)

- **SWIM — FDPS and SWIM-borne FIDS — is the ultimate validator.**
- Local receivers may **never** authoritatively claim off/on/in unless **BOTH**:
  (a) SWIM is demonstrably down — measured from `feed_state`, not assumed; and
  (b) that same local receiver already held a prior active track on the entry.
  A receiver that has never seen the aircraft cannot become its authority just
  because SWIM went quiet.
- Whichever source established the initial fix lock is recorded, and nothing
  **below** that tier may authorize off/on/in while that source is still alive.

This aligns with the standing memory rule (OOOI SWIM authority): confirmation =
SWIM + network ACARS/ADS-B aggregators, never gated on the local receiver.

---

## The source-tier hierarchy — `_OOOI_SOURCE_TIER` (`db.py:5771-5795`)

Higher wins. Gaps left between tiers so a source can be inserted without
renumbering (which would silently re-rank existing locks):

| tier | sources | role |
|---|---|---|
| **40** | `fdps`, `swim_fids`, `tbfm`, `tfms`, `smes`, `itws`, `aim` | **SWIM — the validator of record** |
| **30** | `acars`, `adsb` | network aggregators (off-box, multi-receiver) — admitted alongside SWIM |
| **20** | `fids` | **MWAA airport display — ENRICHMENT ONLY**, never reached for an assertive phase (see below) |
| **10** | `local_adsb`, `local_acars`, `dump1090`, `acarshub`, `dumpvdl2` | **local receivers** — authority only via the (a)+(b) escape hatch |
| **0** | `schedule`, `schedule_inference` | **inference — a guess**; may never assert an assertive phase, ever |

Distinct from `_OOOI_SOURCE_PRIORITY` (`db.py:5754`), which only breaks
**same-phase ties**; entitlement is decided by the tier table via
`_oooi_authority_check()`. Only the **assertive phases** `{off, on, in}` are
gated (`_OOOI_ASSERTIVE_PHASES`, `db.py:5800`); `out`/`pre_departure` stay
ungated — being wrong about them costs nothing and gating them would block the
pre-track bookkeeping the gate itself relies on.

---

## FIDS and TFMS are enrichment-only for phase assertion

- **FIDS** is hard-denied from asserting *any* OOOI phase
  (`_oooi_authority_check`, `db.py:5865-5866`): `if source == "fids": return
  False`. Belt-and-suspenders — the sole caller that ever passed `source="fids"`
  (`poller._check_flight_fids`) has had its phase-promotion removed; the deny
  here stops any future caller reintroducing the false-early-landing path an
  airport display created. FIDS remains a **gate/baggage enrichment** source
  only. Known-wrong-on-timing evidence in the tier comment (`db.py:5778-5785`):
  UAL2670 (2026-07-27, "Landed" 15 min early), UAL599 (2026-09-27, early arrival
  while at cruise).
- **TFMS** carries the airline's own literal reported OUT/OFF/ON/IN times but
  sits at tier 40 (SWIM) and is never outranked — so the authority gate alone
  would **not** stop it asserting a *future* scheduled time (see next section).

---

## The no-future-milestone rule (TFMS premature-landing bug)

`_oooi_authority_check()` has **no notion of a future timestamp**
(`tfms_parser.py:916-918`). Because TFMS is tier 40, an unconditional assert of
its `airlineInTime` would sail through the gate — this is exactly what produced
**UAL599 and UAL1791, both swept as landed (`oooi_phase=in`, `source=tfms`) ~5
hours early** off a future `airlineInTime` (`tfms_parser.py:918-920`).

The fix lives in the **parser**, not the gate — `_handle_flight_times()`,
`src/ingest/parsers/tfms_parser.py:909-940`:

- TFMS carries all four milestones at once as SCHEDULED/ESTIMATED values from
  before departure, so a pre-departure message routinely already contains a
  future `airlineInTime`.
- The 2026-09-27 fix iterates `[in, on, off, out]` and picks the
  **highest-order phase whose airline time is `<= now`** (`_dt <= _now`,
  line 938). A future time is a schedule estimate and asserts nothing about
  `oooi_phase`.
- Only then does it call `db.update_watchlist_oooi_phase_authoritative(...,
  source="tfms", ...)` (`tfms_parser.py:943-946`).

---

## The two premature-landing bug classes this prevents

1. **TFMS asserting a future `airlineInTime`** — closed in the parser by the
   `<= now` achieved-milestone filter (`tfms_parser.py:909-940`). Root cause:
   the gate can't reject a future timestamp and TFMS is never outranked.
2. **FIDS asserting landed** — closed by the hard `source == "fids"` deny in the
   gate (`db.py:5865-5866`) plus removal of the poller's FIDS phase-promotion.
   Root cause: an MWAA airport display, not a sensor, known wrong on timing.

Both the UA1240/UA2408 class (local/inference/lower-tier asserting off/on/in
while SWIM disagreed) are closed by the tier + lock machinery below.

---

## The authority check + lock machinery

`_oooi_authority_check(phase, source, lock) -> (allowed, note)` —
`db.py:5850-5900`. Decision order:

1. Ungated phase (not off/on/in) → allow (`db.py:5857-5858`).
2. `source == "fids"` → **deny** (`db.py:5865-5866`).
3. tier ≤ 0 (inference) → **deny**, no outage or lock state unlocks it
   (`db.py:5871-5872`).
4. Local receiver → deny unless **both** `swim_is_live()` is false **and** a
   prior local track by *this same receiver* exists
   (`oooi_local_track_at` / `oooi_local_track_source`) — the (a)+(b) escape
   hatch (`db.py:5875-5884`).
5. Lock-holder check — a source below the established `oooi_lock_tier` cannot
   assert while the lock holder is alive (`db.py:5886-5898`).

Supporting pieces:

- **`swim_is_live()`** (`db.py:5812-5847`) — true if **ANY** of
  `_SWIM_FEED_KEYS = (push:fdps, push:tbfm, push:tfms, push:stdds, push:itws)`
  is fresh within `SWIM_STALE_SECONDS=900`. Deliberately ANY not ALL: one live
  feed means the network path works, so a local receiver stays unpromoted. It
  **fails closed** — if `feed_state` is unreadable it returns True (SWIM assumed
  alive), keeping local receivers unpromoted (`db.py:5820-5822`).
- **Lock columns** (0065, all nullable/additive — `0065_…sql:44-83`):
  `oooi_lock_source`, `oooi_lock_tier` (denormalised at lock time so a later
  re-ranking of the tier table can't silently move existing authority),
  `oooi_lock_at`, `oooi_local_track_at`, `oooi_local_track_source`,
  and `oooi_authority_note` (persisted on **both** accept and reject so a false
  positive is diagnosable after the fact — the only reason the two 2026-09-23
  cases were caught at all, `0065_…sql:73-83`, `db.py:5853-5856`).
- **Enforcement site** — `update_watchlist_oooi_phase_authoritative()`
  (`db.py:6022-…`): forward-only phase move, same-phase tie broken by
  `_OOOI_SOURCE_PRIORITY`, then the 0065 authority gate at `db.py:6063-6075`.
  The lock is established on the first assertive write and only a strictly
  higher tier may take it over (`db.py:6082-6088`).
