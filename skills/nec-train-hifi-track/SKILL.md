---
name: "nec-train-hifi-track"
description: "Default handler for ANY NEC train query — hifi status snapshot + dual ntfy push (train-alerts: short train#/delay/status; dispatch-debriefs: full table). Trigger on any mention of a train number, Acela, NE Regional, or train status at WAS/Union Station — no explicit \"hifi\" required."
---

## Purpose
Default handler for any NEC corridor train query. Fetches hifi status for Acela and NE Regional trains bound for Washington Union Station, fires a short push to `train-alerts` (train# + delay + status) and a full debrief to `dispatch-debriefs`, and checks the runsheet for a matching trip. Mirrors the flight-hifi-track pattern. No explicit "hifi" keyword required.

## Trigger
Any mention of a train number, Acela, NE Regional, or train status — "Acela 2163", "train 175 status", "NEC trains", "hifi trains", "track train [number]", "is the Acela delayed", "what's the train situation", etc.

---

## Auth token

All admin API calls (the push-alert step) use:
```
Authorization: Bearer $CTDC_ADMIN_TOKEN
```

**This is the same token used by flight-hifi-track** — both skills hit the same admin-tier endpoint (`/admin/push-alert`), so there's no reason to maintain separate tokens. ~~Confirmed working 2026-07-17 (fired a live push for NE Regional 84 successfully). If this token stops working, cross-check flight-hifi-track's copy first (they should always match); if both are dead, regenerate on the Pi with `podman exec systemd-corporatetraveldc-web python3 /app/ctdc_token/cli.py create --user cowork --tier admin --label "cowork-dispatch-ops"` and update both skill files.~~ (corrected 2026-10-15: no skill file carries a token any more, the CLI path is `/app/src/ctdc_token/cli.py`, and minting is an operator decision. Resolve the token as in "Token resolution" at the bottom.)

**Reach:** admin calls must go to `http://127.0.0.1:8000` or `http://100.x.x.x:8000` (Tailscale) — Cloudflare strips Authorization headers.

**Note on the corporatetravel-dispatch MCP tools** (2026-10-15: that MCP server was archived 2026-08-17; use the direct `curl` path, this note is history)**:** they proxy through a separate "dispatch-runner" relay that has shown intermittent JSON-decode errors and timeouts independent of the underlying API's health. If an MCP tool call fails oddly, fall back to a direct HTTP call to the Pi (curl via SSH/bash, or `mcp__corporatetravel-dispatch__dispatch_get_amtrak`) rather than retrying the relay repeatedly. `dispatch_get_amtrak`'s raw output can exceed the tool result size limit (63 trains ≈ 52K chars) — prefer a direct `curl http://100.x.x.x:8000/api/v1/amtrak | python3 -c "..."` filtered to the train(s) you actually need, rather than pulling the whole feed through the MCP tool.

---

## Step 1: Identify target trains

If a specific train number is given, track that train only.

If no number given, use the full watched NEC list:
- **Acela:** 2121, 2155, 2159, 2163, 2167, 2171, 2173
- **NE Regional:** 137, 173, 175

(2026-10-15: the deployment's own list is `watchlists/permanent_trains.json`, 304 permanent train entries; 2121, 2163 and 2173 above are not in it. Treat that file, or `GET /api/v1/watchlist` (Tier 1), as the authority rather than this hand list.)

**Primary key is always the numeric train number**, not service name. "Acela" covers dozens of departures; "2163" is one specific departure.

---

## Step 2: Fetch Amtrak data — real field schema (corrected 2026-07-29)

```
GET http://100.x.x.x:8000/api/v1/amtrak
Authorization: (none — Tier 0)
```

Response shape: `{"available": bool, "summary": str, "fetched_at": ..., "trains": [...]}`.

> **Corrected 2026-10-15 — the live feed now has a different, smaller schema.** Every `amtrak_status` row written in the last 24 h comes from the separate `amtrak-tracker` container (`src/amtrak_tracker/main.py`, polls amtraker.com every 5 min, keeps only trains serving `FILTER_STATION`, default WAS). Its records carry exactly 13 fields: `train_num`, `route`, `origin`, `destination`, `status`, `delay_minutes`, `scheduled_arr`, `estimated_arr`, `scheduled_dep`, `estimated_dep`, `lat`, `lon`, `train_id` (live read 2026-10-15: 28 trains, every one with those 13 keys and no others). `status`, `delay_minutes` and the four times are **at WAS**. `station_code`, `station_name`, `dest_*`, `serving_watched`, `is_watchlist` and `is_regional` below are absent from the live payload; they belong to the ingest-core writer, which wrote no row in the last 24 h. Report the destination ETA as unavailable rather than inventing it.

**The field names below are what the live feed actually returns — verified 2026-07-29 against a real response (63 records) after this skill's previously-documented field names (`train_number`, `route_name`, `scheduled_time`, `estimated_time`, `platform`) turned out to not exist anywhere in the real payload.** Don't trust the old names if you see them referenced anywhere else (older chat history, etc.) — these are the correct ones:

| Field | Meaning |
|---|---|
| `train_num` | Train number, as a string (e.g. `"2171"`) — match on this |
| `route` | Service name (`"Acela"`, `"Northeast Regional"`, `"Crescent"`, etc.) |
| `origin` / `destination` | Station codes for the full run |
| `status` | Live status **at the reference station** (`"Enroute"`, `"Station"`, `"Departed"`) |
| `station_code` / `station_name` | The **reference station** used for delay math and the `status` field above — priority order is watchlist station > regional station > primary station (WAS by default). **This is not necessarily "where the train currently is."** For current physical position use `lat`/`lon` instead — a train `Enroute` with `station_code: "WAS"` can still be hours away and hundreds of miles north; `station_code` just says "WAS is the station this record's status/timing is pinned to," not "the train is at WAS right now." |
| `delay_minutes` | Minutes late at the reference station (0 = on time) |
| `scheduled_arr` / `estimated_arr` | ISO timestamps, arrival at the reference station |
| `scheduled_dep` / `estimated_dep` | ISO timestamps, departure at the reference station |
| `dest_scheduled_arr` / `dest_estimated_arr` / `dest_delay_minutes` | **Added 2026-07-29.** Scheduled/estimated arrival and delay at the train's actual **final destination** (`destination` field) — not the reference station. Report this alongside the reference-station status, not instead of it: `status`/`delay_minutes` answer "is it running on time right now," `dest_estimated_arr`/`dest_delay_minutes` answer "when does it actually get where it's going." For a train whose reference station happens to be its final stop (WAS-bound Acela/Regional service, most of the time) these will be identical — the distinction matters for trains that continue past a watched station to somewhere else. |
| `lat` / `lon` | Current live position — use this for "where is it right now" |
| `train_id` | amtraker.com's own ID, often `"<train_num>-<day-of-month>"` (e.g. `"2171-29"`) — NOT something dispatch derives, just passed through |
| `serving_watched` | List of watched station codes this train touches |
| `is_watchlist` / `is_regional` | Booleans — whether the train touches an operator-watchlist or regional station |

**Filtering note:** the feed already filters to only trains touching a watched/regional/primary station — a train irrelevant to the DC-area/NEC won't appear at all, regardless of its own on-time status. So "train X isn't in the response" means either it's genuinely on time/normal *or* it simply doesn't touch a station this deployment cares about (e.g. a long-haul train not near DC yet) — don't assume absence always means "on time" without a quick sanity check on whether the train should be relevant right now.

**Multi-day entries:** overnight/long-distance trains (e.g. the Crescent, the Floridian) can appear twice for the same `train_num` — once for yesterday's still-in-progress run and once for today's — distinguished by the `train_id` suffix or by comparing `scheduled_arr`/`estimated_arr` dates. For same-day NEC service (Acela/Regional) this is rare, but if you get more than one match for a `train_num`, pick the one whose `scheduled_arr`/`estimated_arr` date is closest to today, not just the first match.

**~~Two~~ Three possible writers (corrected 2026-10-15: the third, `src/amtrak_tracker/main.py`, is the one live now, see the note above Step 2's table), ~~two~~ three possible schemas — know this exists even if you don't need to handle it every time:** ~~`src/ingest/amtrak.py` is the primary source (the schema documented above)~~ (corrected 2026-10-15: `src/amtrak_tracker/main.py` is the primary, push:amtrak writer with the 13-field schema; `src/ingest/amtrak.py` (ingest-core) wrote no row in the last 24 h); `src/poller/fetchers/amtrak.py` is a fallback that ~~only fires if the ingest heartbeat goes stale~~ (2026-10-15: fires when push:amtrak, stamped by amtrak-tracker, is unhealthy; `push_is_healthy("amtrak", 90)`), and it writes an **older, different schema** (`train_number`, `train_name`, `delay_minutes`, `train_state`, `orig_code`, `dest_code`, `event_name`, `_raw`) into the same `trains_json` field. Both call the same `db.insert_amtrak_status()`, so whichever ran most recently is what `/api/v1/amtrak` returns — there's no signal in the API response itself telling you which writer produced it. If a query ever comes back with fields that don't match the table above, that's the fallback path having fired, not a bug in this skill — check for both `train_num` and `train_number` before assuming a parse failure. This doesn't affect the second-brain daily digest, which only reads the pre-built `delay_summary` string, never individual per-train fields.

---

## Step 3: Check the runsheet (informational, not blocking)

```
GET http://100.x.x.x:8000/api/v1/runsheet
Authorization: Bearer <cert-tier token or higher>
```

(corrected 2026-10-15: this route is Tier 1, `require_tier(Tier.T1)` in `src/web/main.py`; a call with no token returns 403, checked live. Without a cert-or-better token, skip this step and say so.)

As of 2026-07-29, the runsheet is empty by default — it's ingest/pattern-recognition fodder for the second brain, not a live source of matched client trips yet (waiting on a LimoAnywhere/RingCentral/3CX tie-in). An empty result is expected, not an error; mention it in passing, don't treat it as noteworthy unless it actually has a matching trip.

---

## Step 4: Fire train-alerts push (short form)

```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer $CTDC_ADMIN_TOKEN
Content-Type: application/json

{
  "topic": "train-alerts",
  "priority": 3,
  "title": "NEC HIFI [<N> trains]",
  "message": "<short summary — see format below>"
}
```

**Short form format** (single line, phone lock screen; corrected 2026-10-15: every time is UTC + ET or UTC-only, never ET alone, so `est WAS 02:10Z / 22:10 ET`, not `est WAS 22:10`. amtraker times are offset-bearing ISO strings; convert, never relabel):
- If one train, on time: `Acela 2171 — ON TIME, est WAS 02:10Z/22:10 ET | Enroute`
- If one train, delayed: `Acela 2163 — +22min, est WAS 18:54Z/14:54 ET | Enroute`
- If multiple: `NEC HIFI: 7 tracked, 2 delayed. Worst: 2163 +22min. Regional 175 on time.`
- If feed error: `NEC HIFI: Amtrak feed unavailable — <error>`

---

## Step 5: Fire dispatch-debriefs push (full table)

```
POST http://100.x.x.x:8000/admin/push-alert
Authorization: Bearer $CTDC_ADMIN_TOKEN
Content-Type: application/json

{
  "topic": "dispatch-debriefs",
  "priority": 2,
  "title": "NEC HIFI DEBRIEF",
  "message": "<full table — see Step 6 format>"
}
```

---

## Step 6: Report full table in chat

Always report the full table in chat AND as the dispatch-debriefs push body. No "Platform" column — it's not in the live schema. Use `status` + reference station, `lat`/`lon` for actual current position when it's useful (e.g. the train is still hours out), and the destination ETA fields (added 2026-07-29) for when it actually gets where it's going, not just its current on-time status:

```
Train   Route              Ref. station   Status     Delay    Dest ETA          Position (if useful)
2121    Acela BOS-WAS      WAS            Enroute    0min     WAS 17:45Z/13:45 ET  —
2163    Acela BOS-WAS      WAS            Enroute    +22min   WAS 18:54Z/14:54 ET  ~41.0N 73.6W
175     NE Regional BOS    —              —          —        —                 Not in feed (may not touch a watched station right now)
```

Include at bottom:
```
Feed timestamp: <fetched_at>
Watched trains: <N> | Delayed: <D> | Feed source: dispatch /api/v1/amtrak
Runsheet: <empty / N matching trips>
```

---

## Design notes

**Why train number over service name:**
"Acela" covers dozens of departures across a day; a train number is one specific departure. amtraker.com (the upstream feed) keys on train number too.

**No OOOI watchlist for trains:**
Trains don't have Out/Off/On/In milestones the way flights do. The equivalent is the per-station `status`/delay tracking already in the feed — no separate watchlist entry is needed for a one-off query. (The `is_watchlist` field in the schema refers to Amtrak-side station watchlisting, i.e. `AMTRAK_WATCHLIST_STATIONS`, not the same concept as the flight/train/vessel route-lock watchlist system — don't conflate the two.)

**ntfy channel split (mirrors flight-hifi-track):**
- `train-alerts` → short, glanceable on phone lock screen (train# + delay + status)
- `dispatch-debriefs` → full table, same channel as OPS brief reload and flight debriefs

**Schema correction history:** originally documented `train_number`/`route_name`/`scheduled_time`/`estimated_time`/`platform`, none of which exist in the live feed. Corrected 2026-07-29 after a live query for Acela 2171 came back empty against those field names — verified the actual schema against `src/ingest/amtrak.py`'s `parse_feed()` (the real, current primary writer) rather than guessing, per the standing rule to verify parsers/schemas against real samples before trusting them.

**Destination ETA, added 2026-07-29** (2026-10-15: not emitted by the live `amtrak-tracker` writer, see Step 2)**:** both writers (`src/ingest/amtrak.py` primary and `src/poller/fetchers/amtrak.py` fallback) now also emit `dest_scheduled_arr`/`dest_estimated_arr`/`dest_delay_minutes` for the train's actual final stop, not just the reference station. Also live in the PWA itself — `src/runner/frontend/src/components/TrainMapView.jsx`'s train panel now shows a destination-ETA line under each train row (`tDestEta()` helper, `.train-row-dest-eta` CSS class), separate from the existing live reference-station status line. Rebuilt/redeployed same day: `corporatetraveldc-ingest-core`, `corporatetraveldc-poller`, `corporatetraveldc-runner`.

---

## Token resolution (added 2026-09-22) — `$CTDC_ADMIN_TOKEN`

This skill no longer carries a plaintext token. The old inline
`ctdc_cowork_…` literal was **revoked 2026-08-16** (auth_tokens id 3, cowork
downgraded admin -> shares), so every admin call here had been 403-ing for
five weeks before anyone noticed.

Resolve `$CTDC_ADMIN_TOKEN` at call time:

- ~~**Shell on the Pi:** read `~/.secrets/remote-admin-agentic.token` — active
  admin token, auth_tokens id 23, prefix `ctdc_remote-admin_`, device
  `cowork`, expires 2027-09-20.~~ (corrected 2026-10-15: `auth_tokens` id 23
  was **revoked 2026-10-05 19:41Z** (live row, and
  `docs/CREDENTIALS_REGISTER.md`). The file still exists but holds that
  revoked token, so reading it gets a 401/403. Do not use it.)
- **Shell on the Pi, now (2026-10-15):** the platform's own admin callers get
  `DISPATCH_ADMIN_TOKEN` (`auth_tokens` id 24, active) from
  `scripts/with-dispatch-env.sh` (run from the repo root), which loads the env
  files without a shell `source`. Keep the token off every command line (no `curl -H "Bearer
  $TOK"`): run the call inside the wrapper so the value stays in the
  process environment, e.g.
  ```bash
  scripts/with-dispatch-env.sh python3 - <<'EOF'
  import os, requests
  h = {"Authorization": "Bearer " + os.environ["DISPATCH_ADMIN_TOKEN"]}
  r = requests.get("http://100.x.x.x:8000/admin/healthz", headers=h, timeout=10)
  print(r.status_code)   # never print the headers
  EOF
  ```
  Whether an interactive skill session should use that service token or be
  issued its own is an operator decision; nothing here mints one.
- ~~**MCP / no shell:** the client must supply it as `DISPATCH_TOKEN`.~~
  (corrected 2026-10-15: the `corporatetravel-dispatch` MCP server that read
  `DISPATCH_TOKEN` was archived 2026-08-17; no MCP path takes an admin token
  today. Cloud agents use the agent gateway tools, which are not admin.)

Never construct a call whose error output could echo the Authorization
header. A 403 here means check `auth_tokens.revoked_at` first — a revoked
token is indistinguishable from an unprovisioned one at the client.
