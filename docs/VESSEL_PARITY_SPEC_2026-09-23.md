# Vessel Parity Spec — 2026-09-23

Written under the standing rule that **aviation is the parity benchmark**
(second brain `20260923T181652Z.md`): every capability below states one of
*at parity*, *defined break in parity (with reason)*, or *stated target
parity*. Nothing here is left implicit.

## Why this exists

Vessels are scaffolded and inert. `src/ais_watcher/ais_watcher.py` v1.0
reads UDP JSON from a local AIS-catcher decoder; `vessel_events` has 0 rows;
`watchlist_entries` has 0 vessel rows. The watcher's own docstring says
"Future: integrate with dispatch vessel watchlist API when implemented."
The quadlets exist and are `.disabled` pending receiver hardware
(`corporatetraveldc-ais-catcher.container.disabled`,
`corporatetraveldc-ais-watcher.container.disabled`), with a udev serial
`AIS0162` already reserved — see `docs/SDR_SERVICES.md`.

So the question is not "should we build vessels" but "what does parity with
the aviation side actually require," answered before hardware arrives so the
identity layer is ready when a receiver is.

## 1. Identity model — the core of this spec

Three layers. This went through two corrections during design and the final
model is a synthesis, not either original position.

| Layer | Field | Behaviour | Aviation analogue |
|---|---|---|---|
| Hull | **IMO number** | permanent for the life of the hull; survives name, flag, owner and MMSI changes | *no clean equivalent* — stronger than Mode S hex, which changes on re-registration |
| Registration | **MMSI** | the de facto operational identifier. Changes on reflag — rare, and far more rigorous than a vanity N-number swap, but it happens and there is a **documented former→new lineage** | **N-number / tail** |
| Operational | **name, AIS callsign, destination** | operator-editable static data, changes freely — including with charter status | **callsign per flight** |

### The 100 GT problem — load-bearing, not a footnote

**IMO numbers are only assigned to vessels of 100 GT and above under SOLAS.**
A large share of private yachts and small charter vessels — which is
precisely the traffic this platform cares about — have **no IMO at all** and
will broadcast `0` or junk in that field.

Therefore:

- **IMO present** → anchor on IMO; MMSI is the current registration.
- **IMO absent** → **MMSI is the anchor**, and name-change detection is the
  *only* identity signal available.

The second case is expected to be the majority of the operationally
interesting fleet. The name/charter guard in §2 is therefore a **first-class
component**, not an enrichment layered on top of IMO anchoring — it has to
carry identity on its own for sub-100 GT vessels.

`ais_watcher` must treat a missing IMO as **normal**, never as an error.

**Parity: TARGET.** Aviation resolves callsign → tail → hex against FAA +
OpenSky and surfaces `hex_mismatch`. Vessels need the equivalent
cross-reference and disagreement flagging. This is a *larger* piece than the
aviation one, not smaller — the earlier assumption that "MMSI is globally
unique so no cross-reference is needed" was wrong and is recorded here so it
is not re-derived.

## 2. The identity guard — three distinct change events

Each means something different operationally, so each gets its own alert
rather than a generic "identity changed":

1. **Name change, same IMO + same MMSI** → charter status change or
   rebranding. *The primary case.*
   `MMSI … / IMO … now broadcasting "NAME B" (was "NAME A", 14d)`
2. **MMSI change, same IMO** → reflag or re-registration. Rare, and a bigger
   deal for a watched hull. Record the former→new lineage.
3. **IMO absent where previously present, or a different IMO on a known
   MMSI** → spoofing, misconfiguration, or a recycled MMSI on a different
   hull. Highest suspicion, lowest confidence.

**Parity: AT PARITY by construction** — this mirrors the aviation side's
existing tail-change detection (`fdps_diversion_continuations`) and
IDENTITY MISMATCH / `hex_mismatch` alerting in `flight-hifi-track`.

### Charter state as derived signal

Once name history per hull exists, the *transition* is the product. A vessel
broadcasting name A with the owner aboard and name B under charter means
detecting the flip reports operational state with nobody filing anything.

Derived state: `owner_aboard` / `chartered` / `unknown`, resolved from which
known alias is currently live. This is a **learned** mapping from that hull's
observed alias history, never a given one.

### Honesty constraint — non-negotiable

**AIS static data is self-reported and trivially spoofable.** A Mode S hex is
at least hardware-assigned; a vessel name is typed into a console. An
identity change is **evidence, never proof**, and an alias match is weaker
still. Same discipline as the SMES bitmask: report the observation with its
confidence, never assert the conclusion. No alert text may state charter
status as fact.

## 3. Data tiers — mirroring aviation exactly

| Tier | Aviation (live today) | Vessel equivalent | State |
|---|---|---|---|
| Local receiver | RTL-SDR → ultrafeeder → tar1090 | RTL-SDR (`AIS0162`) → **AIS-catcher** → `ais_watcher` | code + quadlets exist, **no hardware** |
| Network gap-fill | **airplanes.live** (reciprocal: feed it, get wide-area) | **AISHub** (same reciprocal model — free tier requires contributing a feed) | not wired |
| Beyond-range | FR24 ADS-C for overwater (`overwater-adsb-handoff-track`) | **satellite AIS** (Spire / exactEarth / MarineTraffic) | not wired |

The reciprocity matters: **AISHub's free tier is contingent on running a
local receiver**, exactly like airplanes.live. The hardware unlocks the
network tier; it is not just local coverage.

**Parity: TARGET** on all three tiers.

### Hardware note

`docs/SDR_SERVICES.md` already reserves udev serial `AIS0162` for an RTL-SDR
on 161–162 MHz, so the RTL-SDR path is the existing plan. A dedicated
receiver (e.g. dAISy HAT) would move decode off the CPU entirely, which is a
real consideration on a box with a hard 2-core cap and an llama unit at
CPUWeight 9000 — but it is a **change of plan**, not a correction, and the
existing design is coherent. Recorded as a trade-off, not a recommendation to
override what is already designed.

## 4. Registry sources — the FAA/OpenSky analogue

| Source | Role | Analogue | Access |
|---|---|---|---|
| **FCC ULS** (ship radio station licences) | US MMSI assignments, bulk downloadable | **direct `faa_aircraft_registry` analogue** | free, open, bulk |
| **ITU MARS** | assignment authority; MMSI + callsign by flag state | authoritative registry | download exists but **restricted** (ITU/TIES account) |
| **Equasis** | consolidated identity, class, **management history**, PSC inspections; searchable by MMSI/IMO/callsign/name | **`opensky_aircraft_registry` analogue** — aggregated, broad, carries *history* | free with registration, no bulk API |
| Marinesia et al. | MMSI/IMO profile lookup | commercial gap-fill | paid |

Equasis is the one that answers the ownership/charter-lineage question — its
management history is where former names and former MMSI live.

**Recommended shape:** FCC ULS as the bulk US spine on a refresh timer
(exactly like the FAA registry job), Equasis as the enrichment and history
layer, with disagreement between them surfaced the way `hex_mismatch` is.

**This work needs no hardware** and can land before a receiver arrives —
the same sequencing that made the FAA/OpenSky tables useful before they were
queried in anger.

**Parity: TARGET**, achievable now.

## 5. Watchlist and alerting

- Watch by **IMO where present, MMSI otherwise, never by name** — the same
  rule `flight-hifi-track` already states for hex vs callsign.
  `watchlist_entries` already supports `entry_type='vessel'`; 0 rows today.
- `ais_watcher` currently reads MMSIs from `AIS_STATIC_MMSI` env var instead
  of the DB watchlist. Its own docstring flags this. **Defined break, needs
  closing** — small.
- **Bug to fix:** `NTFY_TOPIC` in `ais_watcher.py` defaults to
  `"flight-alerts"`. `vessel-alerts` already exists (added 2026-08-11 after
  vessel events were found firing under `train-alerts` with train-shaped
  copy). Left as-is, vessel pushes would land on the aviation channel.
- Navigational status (AIS enum: 0 under way using engine, 1 at anchor,
  5 moored, 3 restricted manoeuvrability…) is a genuine **OOOI analogue**,
  and better than rail's — it is broadcast rather than inferred. Model it
  the same way: monotonic where meaningful, authority-gated, source-tagged.

**Parity: TARGET**, with the topic default an outright bug.

## 6. Parity summary

| Capability | Parity state | Note |
|---|---|---|
| Identity resolution | **TARGET** | IMO-anchored, MMSI-keyed, name-guarded; larger than aviation's |
| Identity-change alerting | **AT PARITY** once built | mirrors tail-change / `hex_mismatch` |
| Phase state | **TARGET** | navigational status is a better OOOI analogue than rail has |
| Watchlist integration | **DEFINED BREAK** | env-var list instead of DB; flagged in the watcher's own docstring |
| Alert topic | **BUG** | defaults to `flight-alerts` |
| Local receiver | **TARGET** | code + quadlets ready, no hardware |
| Network gap-fill | **TARGET** | AISHub, reciprocal like airplanes.live |
| Beyond-range | **TARGET** | satellite AIS, the ADS-C analogue |
| Registry cross-reference | **TARGET** | FCC ULS + Equasis; **no hardware needed, can land first** |

## 7. Sequencing

1. **Registry tables** (FCC ULS spine + Equasis enrichment) — no hardware,
   unblocks identity resolution.
2. **`ais_watcher` Type 5 static parsing** + missing-IMO-as-normal handling.
3. **Identity guard** and the three change alerts.
4. **Watchlist integration** and the `NTFY_TOPIC` fix.
5. **Navigational-status phase model.**
6. Hardware, then AISHub reciprocity, then satellite gap-fill.

Items 1–4 are all reachable before a dongle exists.
