## ACARS data via airframes.io (addition to dispatch-ops skill)

~~The flight-hifi-track skill now queries airframes.io for ACARS data
to confirm destination and wheels-up after the ICAO hex is resolved.~~
(corrected 2026-10-25: superseded — the flight-hifi-track skill makes no
airframes.io call of its own. Once a flight is on the watchlist, the
poller's sweep asks `common.acars.get_latest_phase()` for OOOI: Jumpseat
when a registration is known, then airframes.io, then this station's
acarshub DB (`src/common/acars.py:347-411`, `src/poller/main.py:24,977`).
ACARS gate/baggage reach the assignments route through the acars-watcher's
gate forwarder, `POST /api/v1/gate-observations/acars` (193 audited
`gate.acars.ingest` calls in the 7 days to 2026-10-26 14:23Z).)

### Endpoint

```
GET https://api.airframes.io/v1/messages?aircraft=<ICAO_HEX>
```

**The `/v1` prefix is required** — corrected 2026-08-23 after testing both
forms live; the un-versioned `https://api.airframes.io/messages?aircraft=…`
this file used to document returns `404`, while the `/v1` form returns `200`.
(unverified 2026-10-25: no third-party call is made in this check. The
platform's own callers use `GET {AIRFRAMES_API_BASE}/messages` with
`since`/`limit` and the default base `https://api.airframes.io/v1`, never
the `?aircraft=` form: `src/common/acars.py:51,211-222`,
`src/acars_watcher/acars_watcher.py:61,435-442`.)

No auth required. Returns a JSON array of recent ACARS messages.
Always filter client-side: keep records where `airframe.icao` matches
the target hex (case-insensitive) -- the endpoint may return a global
feed if the filter returns no matches. (unverified 2026-10-25: external
API behaviour, not re-checked. The platform's callers send
`AIRFRAMES_TOKEN` as a Bearer header whenever it is set:
`src/common/acars.py:211-216`, `acars_watcher.py:99-102`.)

### Key response fields

(unverified 2026-10-25: external response schema, not re-checked. The
platform's parsers read top-level `tail`/`registration`, `flight`,
`label`, `cleanedText`/`text` and `timestamp`, or the nested `acars.*`
form: `src/common/acars.py:80-123`.)

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
> 1. **Its airframes.io poller ~~logs a 404 on most cycles~~ (corrected
>    2026-10-25: superseded — a 404 is now a DEBUG-level "no rows", see the
>    handler note below) — but the base URL is fine and the poller is not
>    dead.** An earlier revision of this note
>    blamed `AIRFRAMES_API_BASE`'s `https://api.airframes.io/v1` default and
>    claimed the un-versioned form was the working one. **That was backwards
>    and is corrected here.** Tested directly:
>
>    ```
>    https://api.airframes.io/v1/messages?aircraft=Q43CH   → 200
>    https://api.airframes.io/messages?aircraft=Q43CH      → 404   (no /v1)
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
>    ~~treats any non-200/401 as a failure and logs
>    `WARNING REST airframes: HTTP 404`, dropping the cycle.~~ (corrected
>    2026-10-25: superseded — a 404 is now logged at DEBUG as "0 msg(s)" and
>    is not an error; a 401 idles the thread for an hour, a 429 sleeps for
>    `Retry-After` (default 120 s), and any other code logs
>    `WARNING REST airframes: HTTP <code>`:
>    `src/acars_watcher/acars_watcher.py:444-470`. In the 24 h to
>    2026-10-26 14:20Z its journal held 214 rate-limit (429) warnings, no
>    `HTTP 404` warning and one `MATCH [AIRFRAMES]`.) So the feed
>    works only when a 60-second window happens to be non-empty, which does
>    genuinely happen: real `MATCH [AIRFRAMES]` hits are in the same journal
>    (e.g. G-VIIS at 01:00:44 on 2026-08-23) alongside ~90 404s in 6 hours,
>    plus an occasional genuine `HTTP 500`. Treat it as a **lossy** source,
>    not a dead one. The fix would be to widen the window (or overlap it) ~~and
>    to treat 404 as "no rows this cycle" rather than an error~~ (corrected
>    2026-10-25: superseded — the 404 half is done, above; the window is
>    still `since = last_poll` every 60 s, `acars_watcher.py:417-436` and the
>    quadlet's `Environment=POLL_INTERVAL=60`).
> 2. ~~**Its airframes auth never engages — an env-var name mismatch.** The
>    watcher reads `AIRFRAMES_API_KEY`, which is **absent entirely** from
>    both env files (`grep -c AIRFRAMES_API_KEY /etc/corporatetraveldc/dispatch.env
>    /etc/corporatetraveldc/dispatch-secrets.env` → `0` and `0`), so it
>    falls back to its `""` default; the real 67-char credential is under
>    `AIRFRAMES_TOKEN`, which only `src/common/acars.py` and
>    `src/runner/main.py` read. The watcher therefore polls unauthenticated.~~
>    (corrected 2026-10-25: superseded — the watcher now reads the
>    environment variable `AIRFRAMES_TOKEN` (its Python name is still
>    `AIRFRAMES_API_KEY`, `acars_watcher.py:68`) and sends it as a Bearer
>    header when set (`:99-102`). Its env file
>    `/etc/corporatetraveldc/svc/acars-watcher.env` carries a non-empty
>    `AIRFRAMES_TOKEN` (key check 2026-10-26 14:20Z), so it polls
>    authenticated. `AIRFRAMES_TOKEN` is read by
>    `src/acars_watcher/acars_watcher.py`, `src/common/acars.py` and
>    `src/runner/main.py`.)
>
> ~~Both are live-code issues, not doc issues — flagged here so a reader
> doesn't treat this service as a working feed.~~ (corrected 2026-10-25:
> superseded — both were changed in code; what remains is the 60-second
> window and the 429 rate-limiting noted in item 1.) Separately, the systemd
> unit's `Description=` still says "dual-source (local UDP + airframes.io
> REST)" while the code is triple-source v3.0.

### The platform's OTHER ACARS path is a separate reader~~, and it has zero data~~ (corrected 2026-10-25: superseded — it now carries VDL2 data, below)

Added 2026-08-23. Don't confuse `src/acars_watcher/` (above) with
`src/ingest/local_airspace.py`, which is a *different* reader on a
*different* source: ~~a persistent TCP connection to the local acarsrouter
(`host.containers.internal:9080`)~~ (corrected 2026-10-25: two persistent
TCP connections to the local acarsrouter, plain ACARS on
`host.containers.internal:9080` and VDL2 on port 15555 of the same host,
`local_airspace.py:55-65,772-798`), running inside `ingest-core`, writing to
the `acars_messages` table. ~~**That table has 0 rows, ever**
(`sqlite3 /var/lib/corporatetraveldc/corporatetraveldc.db "SELECT COUNT(*) FROM acars_messages;"`
→ `0`, confirmed 2026-08-23), despite a continuously-fresh
`/var/lib/corporatetraveldc/feed_state/acars.heartbeat` — the third instance
of the "heartbeat proves the socket is up, not that data is flowing" trap in
this deployment.~~ (corrected 2026-10-25: superseded — the table is in
Postgres and holds about 188,000 rows (pg_stat estimate 187,989 at
2026-10-26 14:21Z), `received_at` from 2026-10-04 01:23Z to 2026-10-26
14:14Z; the 8,214 rows of the 24 h to 14:21Z all carry `mode` '2'. No
plain-ACARS decoder container runs; `dumpvdl2` does (`podman ps`
14:40Z). `/var/lib/corporatetraveldc/feed_state/acars.heartbeat` is still
stamped every 30 s (14:33:44Z at 14:34:03Z), and now data flows behind it.)

The 2026-08-22 fix added real instrumentation, and it has now answered its
own question. Live `ingest-core` journal, 2026-08-23:

```
ACARS connected but zero messages parsed in 3610s
(lines_received=0 parse_failures=0) — router may be silent or emitting
a format this reader can't parse; connection alone is not proof of data flow
```

`lines_received=0` **and** `parse_failures=0` together mean the router is
genuinely silent — it is not sending unparseable data the reader is
dropping. ~~So the remaining fix is upstream of this box (acarsrouter's own
configuration or its feed source), not in the reader.~~ (corrected
2026-10-25: superseded — the VDL2 reader above now delivers rows.)
~~Practical consequence
for this skill: **do not query `acars_messages` or
`GET /api/acars/messages` expecting Pi-sourced ACARS** — the airframes.io
path documented above is the only ACARS source actually producing data
today, and it is lossy (see the poll-window note).~~ (corrected 2026-10-25:
superseded — Pi-sourced VDL2 data now reaches `acars_messages`, and the
runner's `GET /api/acars/messages` reads this station's acarshub DB first,
then Jumpseat, then airframes.io (`src/runner/main.py:1589-1625`); the
acarshub `messages.db` was last written 2026-10-26 14:33Z.) The idle warning fires
on `ACARS_IDLE_WARN_S` (default 1800 s).

**Jumpseat credential — the path in an earlier revision of this file was
wrong.** There is no `~/.secrets/acarsdrama.token` (confirmed absent), and
no `~/.secrets/jumpseat.key` either. `_resolve_jumpseat_token()` reads, in
order: `ACARSDRAMA_JUMPSEAT_TOKEN` (canonical), `JUMPSEAT_API_KEY` (legacy
alias), then `~/.secrets/jumpseat.key`. On this box the value is supplied
via **`ACARSDRAMA_JUMPSEAT_TOKEN` in
`/etc/corporatetraveldc/dispatch-secrets.env`** (present, 51 chars), ~~which
is what the container gets~~ (corrected 2026-10-25: superseded — no live
quadlet loads `dispatch-secrets.env`; each container gets the key from its
per-service file, e.g. the acars-watcher quadlet loads
`/etc/corporatetraveldc/dispatch.env` plus
`/etc/corporatetraveldc/svc/acars-watcher.env`, which carries
`ACARSDRAMA_JUMPSEAT_TOKEN`; key names checked 2026-10-26 14:41Z) — `~/.secrets/` is not mounted into it at all.
The token is not accessible from chat sessions.
