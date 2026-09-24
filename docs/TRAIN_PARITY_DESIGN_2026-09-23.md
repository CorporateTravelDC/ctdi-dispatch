# Train Parity Design — 2026-09-23

Written under the standing rule that **aviation is the parity benchmark**
(second brain `20260923T181652Z.md`). Every component below states its
parity position explicitly.

## Why this exists

On 2026-09-23 the train side was found far below aviation parity in five
ways, **none of which was documented anywhere**. All 304 train watchlist
entries had `last_event_summary` NULL — the train alert path had never fired
once, while the identical flight path worked.

| # | Capability | Aviation | Trains (before 2026-09-23) |
|---|---|---|---|
| 1 | Phase state | OOOI, monotonic, authority-gated | **none** — 0 of 304 entries |
| 2 | Live status refresh | FIDS / FDPS / TBFM columns | `scheduled_arrival` written once, never updated |
| 3 | Alert path | working | **never fired** |
| 4 | Topic segmentation | `flight-alerts`, `hot-alerts` | every operator into one `train-alerts` |
| 5 | Data coverage | multiple sources | MARC + VRE (39% of entries) have **no feed** |

Break 3 was closed on 2026-09-23 (see below). This document covers 1, 2, 4,
5 and one new correctness bug the fix exposed.

## 0. Already fixed, 2026-09-23 — recorded so it is not re-derived

**The unwrap bug.** `_check_train_amtraker` (`src/poller/main.py`) did:

```python
train = trains[0] if isinstance(trains, list) else trains
```

amtraker v3 returns a **dict keyed by train number** — `{"141": [{...}]}`.
A dict is not a list, so `train` became the *wrapper*. Every field read
afterwards (`trainState`, `stations`, everything) was `None`, `state` was
`""`, every classification evaluated False, and the function returned having
fired nothing. For every train, on every sweep, since it was written.

**The arrival-field bug.** The code read `estimatedArrival` /
`predicted_arrival` / `arrivalTime` — **none of which exist in amtraker v3**.
Real times live per-station in `stations[]` as `schArr` / `arr`, alongside
that station's own `status`. Delay is now derived from the **destination
station**, which removes the dependency on the entry carrying
`scheduled_arrival` — none of the 304 did, so `delay_min` was permanently
`None` and the `LATE ≥30` / `late ≥15` branches were unreachable even after
the unwrap was fixed.

Verified live on Regional 141: `delay_min: 0`, correct classification, event
recorded and pushed.

## 1. NEW BUG the fix exposed — provider collision (P0)

Fixing the unwrap turned "silently dead" into **"confidently wrong"** for
104 entries.

The sweep queries `api.amtraker.com/v3/trains/{ident}` for **every** train
entry regardless of operator. VRE and MARC train numbers collide with Amtrak
numbers. Observed 2026-09-23 17:24:

```
#334 VRE Manassas Line →WAS en route (on time)
```

Route name came from the entry; **all data came from an Amtrak train 334**.
Note the empty origin. `train_events` rows for MARC/VRE: **0**.

**Fix:** a provider guard — only query amtraker for entries whose route maps
to the Amtrak provider. Unmapped routes must produce **no data**, not wrong
data. This is required before anything else in this document ships.

**Parity: correctness bug, not a parity item.** Aviation has no equivalent
because flight identifiers are globally unique per operator.

## 2. Phase state (closes break 1)

Measured across 195 live trains, 47 routes:

```
trainState:      Active 173 | Predeparture 15 | Completed 7
station status:  Departed 1627 | Enroute 1772 | Station 63
```

Four phases, deliberately mirroring OOOI:

| Train phase | Derived from | Flight analogue |
|---|---|---|
| `scheduled` | `trainState == Predeparture` | pre-OUT |
| `departed` | origin station `status == Departed` | OUT / OFF |
| `approaching` | destination is the first station not yet `Departed` | ON |
| `arrived` | destination `status == Station`, or `trainState == Completed` | IN |

**Monotonic and authority-gated**, reusing the existing
`update_watchlist_oooi_phase_authoritative` discipline rather than growing a
second mechanism — a late poll still reporting `Enroute` must not pull a
train back out of `arrived`.

**Parity: TARGET → AT PARITY on delivery.**

## 3. Live ETA (closes break 2)

`scheduled_arrival` is written once at add time and never touched; the live
estimate is read, compared, discarded.

Add: `live_eta` (destination `arr`), `live_eta_updated_at`,
`arrival_delay_min` (`arr − schArr`, already computed, just not persisted).

**`scheduled_arrival` is deliberately NOT overwritten.** The schedule and the
estimate are different facts; conflating them destroys the ability to say
"40 minutes late."

Migration: `0064_train_phase_eta.sql` — `train_phase`,
`train_phase_updated_at`, `train_phase_source`, `live_eta`,
`live_eta_updated_at`, `arrival_delay_min`.

**Parity: TARGET → AT PARITY on delivery.**

## 4. Topic segmentation (closes break 4)

| Topic | Carries |
|---|---|
| `train-alerts` | **watched** entries (any operator) + prominent delays/early running + segment-level disruption (§6) |
| `amtrak-alerts` | every Amtrak event — Acela, Regional, long-distance |
| `marc-alerts` | every MARC event |
| `vre-alerts` | every VRE event |

`train-alerts` stays the "things that matter" channel; per-provider topics
are the firehose, subscribed to selectively. All events continue to hit
`dispatch`.

Service tag leads the message so it is filterable by eye and by ntfy search:

```
[Acela]      #2151 NYP→WAS LATE 32min
[Regional]   #141 SPG→WAS en route (on time)
[MARC Penn]  #538 BAL→WAS departed
[VRE Fred]   #308 FBG→WAS arrived on time
```

`TOPIC_CLICK` gains entries for the new topics, all → `/trains`.

**Parity: TARGET → AT PARITY on delivery.**

## 5. Provider abstraction and parameterization (enables closing break 5)

Every provider normalizes to one shape so the sweep stops knowing about
Amtrak specifically:

```
{identifier, route_name, origin, destination, lat, lon, state, velocity,
 provider, stations: [{code, sched_arr, est_arr, status}]}
```

**The phase mapping lives in the provider adapter, not the sweep.**
`stations[].status` is provider vocabulary — MARC and VRE will not emit
`Enroute`/`Station`/`Departed`. Putting the mapping in the sweep means every
new provider edits the sweep. This boundary is most of the design value.

Config generalizes what already exists rather than inventing an idiom:

```
TRAIN_PROVIDERS=amtrak,marc,vre
TRAIN_PROVIDER_ROUTES_AMTRAK=Acela,Northeast Regional,Crescent,...
TRAIN_PROVIDER_ROUTES_MARC=MARC Penn Line,MARC Camden Line,MARC Brunswick Line
TRAIN_PROVIDER_ROUTES_VRE=VRE Fredericksburg Line,VRE Manassas Line
TRAIN_CORE_ROUTES=Acela,Northeast Regional
TRAIN_REGIONAL_STATIONS=WAS,BAL,WIL,PHL,TRE,NYP,NHV,NLC,BOS
TRAIN_HOME_HUBS=WAS
TRAIN_ALERT_DELAY_THRESHOLD_MIN=15
```

`AMTRAK_CORE_ROUTES` / `AMTRAK_REGIONAL_STATIONS` remain as deprecated
aliases. An outside operator adds `TRAIN_PROVIDER_ROUTES_METRA=...` and gets
`metra-alerts` with no code change — the public-repo requirement.

Unmapped routes fall back to `train-alerts` only and are **never queried
against another provider's API** (see §1).

**Parity: TARGET.** MARC (MTA Maryland GTFS-RT) and VRE fetchers are real
ingest work with their own failure modes; the abstraction must land first so
they slot in rather than being retrofitted.

## 6. Segment-delay metering — the approach-fix analogue

Operator framing: not "141 is 20 late" but "141 lost 20 minutes between X
and Y, who else is routed over that segment, and is it getting worse."

Prototyped and working against the live feed on 2026-09-23:

```
trains=189  segments=1530  qualifying(>=3 trains)=340

BUF -> ROC   +19.0 min  n=4  routes=3: Empire Service, Lake Shore Limited, Maple Leaf
NHV -> STM   +10.0 min  n=6  routes=2: Acela, Northeast Regional

inbound trains yet to hit those segments:
  BUF->ROC (+19m): 3 inbound -- #63 (Maple Leaf), #281, #283 (Empire Service)
  CHI->SOB (+23m): none still upstream
```

Three outputs: **where** time is lost (per segment, not per train), **who has
not reached it yet** (the predictive half), and **trend** across successive
polls.

`MIN_TRAINS=3` and `ALERT_MIN=10.0` are unvalidated starting values and
belong in config beside the `TRAIN_*` keys; they should be watched across
several days before being fixed.

The segment maths is operator-agnostic, which is a further argument for §5
landing first.

**Parity: this is the rail equivalent of an aviation capability the operator
already relies on — TARGET.**

## 7. Build order

1. **§1 provider guard** — correctness, blocks everything else.
2. **§2 + §3** phase and live ETA (migration + sweep) — closes breaks 1, 2.
3. **§4** topic segmentation — closes break 4.
4. **§5** provider abstraction + config.
5. **§6** segment metering as a skill.
6. MARC + VRE fetchers — closes break 5.
