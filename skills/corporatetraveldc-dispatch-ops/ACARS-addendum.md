## ACARS data via airframes.io (addition to dispatch-ops skill)

The flight-hifi-track skill now queries airframes.io for ACARS data
to confirm destination and wheels-up after the ICAO hex is resolved.

### Endpoint

```
GET https://api.airframes.io/messages?aircraft=<ICAO_HEX>
```

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

The `acars_watcher.py` v3.0 on the Pi is a triple-source watcher
(local UDP, airframes.io REST, ACARS Drama Jumpseat REST). It does NOT
currently expose an API endpoint. The airframes.io REST query above
is the direct external equivalent usable from Claude chat.

ACARS Drama Jumpseat token is at `~/.secrets/acarsdrama.token` on the Pi
and is NOT accessible from Claude chat sessions.
