# Gate / baggage history backfill (phase D)

Written 2026-10-20 against the guard worktree (phase B/C/E landed in `3103b419`,
2026-10-18). Phase D fills `gate_baggage_observations` for past legs so the
assignments route, the time-fidelity research and later analytics have history.
It goes through the same code as the live path (`common/gate_assignments.py`),
so every phase B rule holds: source precedence, placeholders never count,
LADD-listed legs dropped before storage (fail closed), no FIDS time, status or
tail, and pushes only for live changes. A backfill never pushes.

## The 24 h validation window (`BACKFILL_MIN_AGE_S = 86400`)

Operator decision 2026-10-20. No leg whose scheduled departure is within the
last 24 h is touched, for two reasons:

1. **Push safety.** The poller pushes a resolved change only for active watches.
   A leg older than 24 h is no active watch's current leg, so a backfilled value
   can never fire an errant push.
2. **LADD validation.** The weekly CUI import (`docs/LADD_CUI_HANDLING.md`) and
   the LADD sweeps (push-time tripwire, transcript leak scan, demo scrub gate)
   get a full day to run and be confirmed valid before any leg becomes eligible,
   so history is only written against a LADD list that has already been checked.

Each script turns the window into a row cut by adding how far its binder can
reach ahead: TDES binds a clearance to a leg scheduled up to 6 h later
(`tdes_leg`, [-2 h, +6 h]), so D1 takes rows with `event_time <= now - 30 h`;
an ACARS message may precede departure by 3 h (`bind_acars`), so D2 takes
messages at least 27 h old. Newer legs were recorded live: `record_tdes` has run
in the STDDS ingest since 2026-10-18 23:17Z (first `swim_tdes` observation;
8 gated TDES rows since then have no observation -- consistent with the live
path's LADD drop or an ingest restart, not checked row by row), and the watcher's gate forwarder covers ACARS.

## D1 -- SWIM TDES departure gates (`scripts/gate-backfill-tdes.py`)

Local only, no network. Keyset-paged over `tdes_departure_events`
(`(event_time, airport, callsign)`, never OFFSET; every row once). Per page the
callsigns go through `db.faa_ladd_listed_batch` before anything is stored;
listed rows are skipped and counted, a lookup failure stops the run (exit 5,
nothing more stored). Each remaining row goes through `record_tdes(rec,
reason="backfill:tdes")`, whose observation key is the TDES row's own primary
key -- the same key the live path writes -- so reruns and overlaps with live
writes store nothing twice. Waits before each page while load1 >=
`GATE_BACKFILL_MAX_LOAD` (default 24). Output: counts only.

- `--dry-run`: counts only (pages, rows, would_store, already_stored,
  skipped_ladd, skipped_recent, placeholders, no_gate, unbound); stores nothing.
- `--g-prefix-report`: per-airport counts of G-prefixed / plain / placeholder
  gates (the open question on TDES's `GC33` vs `C33` form; values stay raw and
  flagged until equivalence is proven).

## D2 -- airframes.io arrival gates / baggage (`scripts/gate-backfill-airframes.py`)

Network, bounded. One pass per search phrase (gate, carousel, bag belt, arrival gate: `PHRASES` in the script)
over 1 h `since`/`until` slices, offset paging (100 per page), >= 2.2 s between
calls; a 429 or 401 stops the run (exit 4), a 404 is an empty window. Text is
parsed in-process with `common.acars_gate_parser` and only the parsed fields go
to `ingest_acars(items, reason="backfill:airframes")` (binding, DC/watched-leg
scope, LADD on the leg's own identifiers, fail closed). `AIRFRAMES_TOKEN` comes
from the environment through `scripts/with-dispatch-env.sh`, is sent as a header
and never printed; the call log is phrase / slice / page / status / count only.
`--dry-run` makes no network call. The API's history reaches back only about
three days, so D2 loses data every day it is not run. Untested against the live
API (the `text=` filter and paging follow the 2026-10-18 design note).

## Not backfilled

FIDS has no history and its times may never be stored; AeroAPI is budgeted for
live legs only; Jumpseat keeps 24 h, already covered live.

## Running

Both runs are heavy (`scripts/relay-lint.py` requires `scripts/relay-wait-load.sh`
first) and run as their own transient user units, never inside a relay's own
process, after the batch that ships them is committed and signed:

```
scripts/relay-wait-load.sh
systemd-run --user --unit=corporatetraveldc-gate-backfill-tdes --nice=19 -p CPUQuota=100% -p Slice=production.slice --collect /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/with-dispatch-env.sh python3 /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/gate-backfill-tdes.py
```

```
scripts/relay-wait-load.sh
systemd-run --user --unit=corporatetraveldc-gate-backfill-airframes --nice=19 -p CPUQuota=100% -p Slice=production.slice --collect /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/with-dispatch-env.sh python3 /opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/gate-backfill-airframes.py --hours 72
```

Follow with `journalctl --user -u corporatetraveldc-gate-backfill-tdes -f`; the
final line is the counts. History revisions are traceable by reason
(`backfill:tdes`, `backfill:airframes`) in `gate_baggage_assignment_history`.

Tests: `tests/common/test_gate_backfill_tdes.py`, `tests/common/test_gate_backfill_airframes.py`,
`tests/scripts/test_relay_lint.py::test_gate_backfill_runs_need_load_wait`.
