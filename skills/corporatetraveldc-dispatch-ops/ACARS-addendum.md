## ACARS data via airframes.io (addition to dispatch-ops skill)

The flight-hifi-track skill now queries airframes.io for ACARS data
to confirm destination and wheels-up after the ICAO hex is resolved.

### Endpoint

```
GET https://api.airframes.io/v1/messages?aircraft=<ICAO_HEX>
```

**The `/v1` prefix is required** — corrected 2026-08-23 after testing both
forms live; the un-versioned `https://api.airframes.io/messages?aircraft=…`
this file used to document returns `404`, while the `/v1` form returns `200`.

No auth required. Returns a JSON array of recent ACARS messages.
Always filter client-side: keep records where `airframe.icao` matches
the target hex (case-insensitive) -- the endpoint may return a global
feed if the filter returns no matches.

### Key response fields

| Field | Notes |
|---|---|
| `airframe.icao` | ICAO hex (use to filter) |
| `airframe.tail` | Registration |
| `flight.flight` | Callsign e.g. AAL1557 |
| `flight.status` | in-flight, landed, etc. |
| `label` | ACARS label code (H1 = position/status report) |
| `text` | Raw message text -- parse for route and OOOI events |
| `sourceType` | acars, vdl, hfdl, aero-acars, iridium-acars |
| `timestamp` | ISO datetime of message |

### Route parsing from text

H1-label messages commonly embed route in text:
- Pattern: `<ORIGIN>,<DEST>,<FLIGHT>` e.g. `KSFO,KDFW,2325`
- Or: `<ORIGIN><DEST>` as 8-char block
- Or: ABS messages like `KOAKKMDW197`

Extract the pair of 4-letter ICAO airport codes. First = departure, second = arrival.

### OOOI parsing from text

Look for keywords: `OFF`, `OUT`, `ON`, `IN` near time fields.
- OFF = wheels up
- ON = wheels down
- OUT = pushed from gate
- IN = at gate

### Source labels in debrief

- `ACARS (VHF/airframes.io)` -- sourceType: acars
- `VDL2 (airframes.io)` -- sourceType: vdl
- `HFDL (airframes.io)` -- sourceType: hfdl
- `ACARS (Satellite/airframes.io)` -- sourceType: aero-acars or iridium-acars

### Pi-side ACARS

`src/acars_watcher/acars_watcher.py` v3.0 on the Pi
(`corporatetraveldc-acars-watcher.service`, `active running`) is a
triple-source watcher — local UDP (`ACARS_UDP_PORT=5005`), airframes.io
REST, and ACARS Drama Jumpseat REST. It does NOT expose an API endpoint of
its own. The airframes.io query above is the direct external equivalent
usable from a chat session.

> ⚠️ **Verified live 2026-08-23 — two things about the Pi-side watcher are
> not what its own comments say. Don't rely on it as an ACARS source
> without checking these first.**
>
> 1. **Its airframes.io poller logs a 404 on most cycles — but the base URL
>    is fine and the poller is not dead.** An earlier revision of this note
>    blamed `AIRFRAMES_API_BASE`'s `https://api.airframes.io/v1` default and
>    claimed the un-versioned form was the working one. **That was backwards
>    and is corrected here.** Tested directly:
>
>    ```
>    https://api.airframes.io/v1/messages?aircraft=N123AB   → 200
>    https://api.airframes.io/messages?aircraft=N123AB      → 404   (no /v1)
>    ```
>
>    The real cause is the **poll window**, not the path. The API answers
>    `404` — not an empty `200` — when a `since` window contains no rows:
>
>    ```
>    /v1/messages?since=<-30s>&limit=500   → 404  {"message":"Cannot GET …","statusCode":404}
>    /v1/messages?since=<-60s>&limit=500   → 404
>    /v1/messages?since=<-300s>&limit=500  → 200  (270 KB)
>    /v1/messages?since=<-3600s>&limit=500 → 200  (250 KB)
>    ```
>
>    The watcher runs at `POLL_INTERVAL=60` and passes `since = last_poll`,
>    i.e. a ~60-second window — exactly the width that 404s. Its handler
>    treats any non-200/401 as a failure and logs
>    `WARNING REST airframes: HTTP 404`, dropping the cycle. So the feed
>    works only when a 60-second window happens to be non-empty, which does
>    genuinely happen: real `MATCH [AIRFRAMES]` hits are in the same journal
>    (e.g. G-VIIS at 01:00:44 on 2026-08-23) alongside ~90 404s in 6 hours,
>    plus an occasional genuine `HTTP 500`. Treat it as a **lossy** source,
>    not a dead one. The fix would be to widen the window (or overlap it) and
>    to treat 404 as "no rows this cycle" rather than an error.
> 2. **Its airframes auth never engages — an env-var name mismatch.** The
>    watcher reads `AIRFRAMES_API_KEY`, which is **absent entirely** from
>    both env files (`grep -c AIRFRAMES_API_KEY /etc/corporatetraveldc/dispatch.env
>    /etc/corporatetraveldc/dispatch-secrets.env` → `0` and `0`), so it
>    falls back to its `""` default; the real 67-char credential is under
>    `AIRFRAMES_TOKEN`, which only `src/common/acars.py` and
>    `src/runner/main.py` read. The watcher therefore polls unauthenticated.
>
> Both are live-code issues, not doc issues — flagged here so a reader
> doesn't treat this service as a working feed. Separately, the systemd
> unit's `Description=` still says "dual-source (local UDP + airframes.io
> REST)" while the code is triple-source v3.0.

### The platform's OTHER ACARS path is a separate reader, and it has zero data

Added 2026-08-23. Don't confuse `src/acars_watcher/` (above) with
`src/ingest/local_airspace.py`, which is a *different* reader on a
*different* source: a persistent TCP connection to the local acarsrouter
(`host.containers.internal:9080`), running inside `ingest-core`, writing to
the `acars_messages` table. **That table has 0 rows, ever**
(`sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db "SELECT COUNT(*) FROM acars_messages;"`
→ `0`, confirmed 2026-08-23), despite a continuously-fresh
`/var/lib/corporatetraveldc/feed_state/acars.heartbeat` — the third instance
of the "heartbeat proves the socket is up, not that data is flowing" trap in
this deployment.

The 2026-08-22 fix added real instrumentation, and it has now answered its
own question. Live `ingest-core` journal, 2026-08-23:

```
ACARS connected but zero messages parsed in 3610s
(lines_received=0 parse_failures=0) — router may be silent or emitting
a format this reader can't parse; connection alone is not proof of data flow
```

`lines_received=0` **and** `parse_failures=0` together mean the router is
genuinely silent — it is not sending unparseable data the reader is
dropping. So the remaining fix is upstream of this box (acarsrouter's own
configuration or its feed source), not in the reader. Practical consequence
for this skill: **do not query `acars_messages` or
`GET /api/acars/messages` expecting Pi-sourced ACARS** — the airframes.io
path documented above is the only ACARS source actually producing data
today, and it is lossy (see the poll-window note). The idle warning fires
on `ACARS_IDLE_WARN_S` (default 1800 s).

**Jumpseat credential — the path in an earlier revision of this file was
wrong.** There is no `~/.secrets/acarsdrama.token` (confirmed absent), and
no `~/.secrets/jumpseat.key` either. `_resolve_jumpseat_token()` reads, in
order: `ACARSDRAMA_JUMPSEAT_TOKEN` (canonical), `JUMPSEAT_API_KEY` (legacy
alias), then `~/.secrets/jumpseat.key`. On this box the value is supplied
via **`ACARSDRAMA_JUMPSEAT_TOKEN` in
`/etc/corporatetraveldc/dispatch-secrets.env`** (present, 51 chars), which
is what the container gets — `~/.secrets/` is not mounted into it at all.
The token is not accessible from chat sessions.
