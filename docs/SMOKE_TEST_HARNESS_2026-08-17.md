# Full-platform smoke-test harness — 2026-08-17

Branch: `smoke-test-harness-2026-08-17` (off `main`, un-pushed, uncommitted — GPG
signing/commit authority is the operator's per standing rule, not mine).

## Scope note

The operator's initial task prompt scoped this to three named categories
(ingest parsers, OOOI confirmation paths, ntfy call sites) built around the
ntfy header-newline bug found earlier the same day. Partway through, the
operator corrected that framing directly: **"stop trying to fucking lead any
of these agents... don't try to pigeonhole it"** — the categories and the bug
class were background context, not a checklist. This report reflects a
genuine independent investigation from that point forward, not completion of
the original three-item list. The single most important finding below (a
9+ hour silent production outage) was outside all three original categories
and would have been missed under the original framing.

---

## Finding 1 (CRITICAL, was live/ongoing at investigation time): signed-manifest drift silently killed 18 of 20 scheduled skills for 9+ hours

`systemctl --user list-units --type=service --state=failed` showed **20
failed units**, including `corporatetraveldc-ep-advance.service` (the EP
brief), `corporatetraveldc-ops-brief.service`, `corporatetraveldc-second-brain-daily.service`,
and `corporatetraveldc-tbfm-arrival-enrichment.service` (the fix for the
dead-SWIM-tier bug the 2026-08-16 drift audit flagged but never landed).

Root cause, confirmed via journal: `scripts/verified-exec.sh` (the wrapper
every scheduled skill runs through) checks the running code against
`MANIFEST.sha256`/`MANIFEST.sha256.asc`, a GPG-signed whole-repo-tree
integrity manifest, and **refuses to run anything if they don't match**.
`MANIFEST.sha256` was last signed **2026-08-16 14:35**. At least two
container rebuilds happened after that (an operator-directed full
rebuild-everything pass earlier in the session, and a poller/web/pusher
rebuild I did myself at ~07:54 EDT today to deploy the ntfy title fix) —
neither was followed by `scripts/sign-manifest.sh`. Every `verified-exec.sh`
invocation since has failed with `INTEGRITY FAILURE`, each scheduled unit
failing independently at its own next trigger time (earliest observed:
`freshness-audit` at 06:00 EDT today — meaning this had already been broken
for hours before I touched anything today).

18 of the 20 failed units share this exact root cause (confirmed by
grepping each unit's journal for `INTEGRITY FAILURE`). The other 2 are
unrelated (see Finding 2 and the boot-stagger note below).

**This was completely invisible to `~/bin/dispatch-stack-guardian.sh`**, the
tool the operator has been running on a ~20-minute cron all day — it only
checks 4 HTTP endpoints (`dispatch-web`, `dispatch-runner`, `ntfy`,
`ollama`), all of which stayed healthy throughout, because the outage was
in the scheduled-skill layer, not the always-on web/API layer. Every
"all healthy" report today was accurate for what it checked and blind to
this.

**Not fixed by me, by design**: `scripts/sign-manifest.sh`'s own docstring
states this is "a DELIBERATE, human-run step... using your own GPG
passphrase" — same posture as this repo's signed commits. I did not attempt
to work around this. **The operator needs to run `scripts/sign-manifest.sh`
themselves** to restore all 18 skills.

**What I did build**: `scripts/smoke-test-platform.sh` — closes the actual
visibility gap. Checks (1) any failed systemd --user unit, (2) manifest
integrity via `scripts/verify-manifest.sh` directly, (3) the same 4 HTTP
endpoints the existing guardian checks, (4) ACARS ingest freshness (see
Finding 6). Exit 0 always, human-readable + a final PASS/FAIL summary line,
safe to run from a cron the same way the endpoint guardian is. **This does
not replace `dispatch-stack-guardian.sh`** — it's a new, separate script;
whether/how to fold it into the operator's actual monitoring cadence is
the operator's call, not made here.

Live-verified: running it right now shows `SMOKE-TEST: FAIL (2 failing
categories)` — 20 failed units, manifest mismatch — exactly matching the
real state confirmed above.

## Finding 2 (real regression, FIXED): `docs-drift-weekly.service` broken by today's own MCP archival

`scripts/weekly-doc-drift-check.sh` line 36 did `cd /home/corporatetraveldc/mcp/dispatch-mcp`
— a directory I renamed to `dispatch-mcp.archived-20260817` during today's
MCP-retirement work, earlier in this same session. Confirmed via journal
(`cd: /home/corporatetraveldc/mcp/dispatch-mcp: No such file or directory`,
09:00:00 EDT today). Fixed: removed that `run_check` call with a comment
explaining why (MCP is fully retired, nothing left to drift-check). This
was one of the 20 failed units NOT sharing the manifest root cause.

## Finding 3 (real bug since 2026-07-20, FIXED + tested against real data): FDPS silently dropping ~20% of real live traffic

While building real-sample tests for `fdps_parser.py` (see Finding 4), a
test against a real captured `HF`-source FDPS message returned `None`.
Root cause: `_KNOWN_SOURCES_FIXM30` (an explicit source-type allowlist
gating the FIXM 3.0 parser) never included `HF` or `RH`. Checked the
current 25-sample real capture batch (`/var/lib/corporatetraveldc/fdps_debug_fixm30/`):
**5 of 25 (20%)** are `HF` or `RH` source — all silently dropped
(`return None`, `log.debug` only, no warning-level trace) since the
allowlist was written 2026-07-20. The original derivation note in the code
already flagged 4 other unexpected source types (AH/BA/LH/HX) found in that
same 25-sample batch and added them — HF/RH were apparently never seen in
that original analysis pass, or appeared in a later refresh of the capture
directory.

Fixed: added `HF`, `RH` to `_KNOWN_SOURCES_FIXM30`, same generic
field-extraction path as the existing AH/BA/LH/HX entries (no new
source-specific branching, no invented semantics) — verified both real
samples now parse to sane callsign/gufi/source before landing the change.
Judged "trivially safe" per the task's ground rules: mirrors an
already-established pattern exactly, doesn't touch status-inference logic,
backed by real data.

## Finding 4 (FIXED — real test coverage added): FDPS test suite exercised only the dead legacy code path

Every pre-existing FDPS test (`tests/ingest/test_fdps_parser.py`,
`test_fdps_element_truthiness.py`) uses hand-crafted fixtures under the
FIXM **4.2** namespace, with synthetic sequential GUFIs
(`AAA01234-...-000000000001`) — clearly not real captures. `fdps_parser.py`'s
own docstring says the live feed is FIXM **3.0**; `parse_fdps_message()`
namespace-sniffs and routes 3.0 traffic to `_parse_fdps_message_fixm30`,
4.2 to the explicitly-named `_parse_fdps_message_fixm42_legacy`. **100% of
real captured samples on this box are FIXM 3.0.** So the entire pre-existing
FDPS test suite has been passing while testing a code path production
traffic never touches; the actual live path had zero coverage.

Added `tests/ingest/test_fdps_fixm30_real_samples.py` — 6 tests against 9
real captured messages (one per distinct `source` value seen in the
available 25-sample batch: AH, CL, HF, HP, HX, HZ, OH, RH, TH; no `FH`
sample existed in the captured set), copied unmodified into
`tests/ingest/fixtures/fdps_fixm30_real/`. All 6 pass (after the Finding-3
fix). Also caught, live: `write_flight_event()` doesn't raise against the
real parsed-dict shape — a check a synthetic fixture with matching field
names by construction could never meaningfully perform.

## Finding 5 (real coverage gap, documented, NOT fixed — new functionality, out of scope for a harness task): STDDS/SMES delivers 7 message types with zero parser coverage

Swept all 131 real captured STDDS/SMES samples
(`smes_debug/`, `smes_debug_priority/`) through all four real parser
functions (`parse_smes_message`, `parse_tais_message`,
`parse_safety_logic_message`, `parse_surface_movement_event_message` — the
same four `swim_client.py` actually calls on every payload). 59/131 (45%)
were handled by at least one. Of the 72 unhandled, most are legitimate
service-status/heartbeat messages with no actionable payload
(`AirportDataServiceStatus`, `STDDSStatus`,
`TerminalAutomationInformationServiceStatus`,
`TowerDepartureEventServiceStatus`, `SurfaceMovementEventServiceStatus`,
`TAStatus` — 25 samples, clearly not a gap).

**But 7 real message types with actual payload data have no parser at all**,
39 real samples total: `TATrackAndFlightPlan` (5), `TowerDepartureEventMessage`
(5), `DATISData` (5), `AssetMessage` (5), `AssetMonitorMessage` (5),
`TDLSCSPMessage` (5), `RVRDataUpdateMessage` (9). Unlike the ITWS gaps (see
Finding 7), none of these are documented anywhere in the code as an
intentional scope decision — they're just never-built. Not fixed here
(building new parsers for 7 message families is real new functionality, not
"harness-building" — the task's own ground rules say don't do side-quest
fixes beyond trivially-safe ones). Flagging for the operator to decide
whether any of these matter (`TowerDepartureEventMessage` and
`TATrackAndFlightPlan` look the most plausibly relevant to this platform's
purpose, but that's a guess, not a verified priority).

Real methodology note, in the interest of honesty about my own process:
my first pass at this swept only `smes_debug/asdexMsg*.xml` and called
only `parse_smes_message`, and reported "0/5 handled" — which looked like
a bug but was actually a **testing artifact**: those 5 samples were all
non-DC-area airports (KMSP, KBOS, KMIA), correctly filtered out by design.
I caught this before writing it up by checking the real `<airport>` values
in each sample against `SMES_AIRPORTS`. The 59/131 figure above is the
corrected, verified sweep against genuinely DC-area-relevant real traffic.

## Finding 6 (already known this session, re-confirmed, now tracked in the smoke script): ACARS pipeline producing zero data

Already found earlier in this session (`acars_messages` table: 0 rows
ever, despite `acars-watcher`/`acarsrouter`/`acarshub` all showing
"active"). Re-confirmed still true. Added as check #4 in
`scripts/smoke-test-platform.sh`, explicitly carved out of the pass/fail
count (labeled `KNOWN-FAIL`) since it's already a tracked, known issue, not
a new smoke-test finding — but now it'll show up on every future run
instead of requiring someone to remember to check.

## Finding 7 (verified clean, not a hidden gap): ITWS's apparent gaps are all deliberately documented

Initial sweep of `itws_debug/` + `itws_debug_by_product/` real samples
showed low hit rates (0/15, 3/10). Investigated rather than reported as-is:
every "empty" result traces to a `product_msg_name` already listed in
`_KNOWN_UNHANDLED_PRODUCTS` with a real, specific, dated justification
comment (e.g. "Wind Profile Product... raw instrument data with no alert
threshold", "Configured Alerts Product... genuine future work"). This is
the opposite of Finding 5 — real deliberate scope decisions, already
transparent in the code, not a silent gap. No action needed; noting this
here so it's clear I checked rather than assumed.

## Finding 8 (verified clean, genuinely unverifiable right now): TBFM

All 5 real `tbfm_debug/` samples are legitimately non-DC-area
(`KCAE`, `DEN`, `LIMC`) — correctly filtered by design, zero exceptions.
**Honest limitation**: no real DC-area TBFM sample exists in the current
capture set, so the actual DC-area metering-data path
(`_parse_air_element` for `apt="DCA|IAD|BWI"`) remains genuinely
unverified — not because it's broken, because there's nothing real to test
it against right now. Marked COULD NOT VERIFY per the task's own ground
rules, not glossed over as a pass.

## Finding 9 (real gap, not fixed — new infrastructure, not trivially safe): `aim_parser.py` has no debug-capture infrastructure at all

Confirmed via the live DB: 5,652 real rows in `notams` — `aim_parser.py`
is actively working and ingesting real data. But unlike every other
parser (`fdps_debug*/`, `tfms_debug*/`, `smes_debug*/`, `itws_debug*/`,
`tbfm_debug/`), there is no `aim_debug` (or equivalent) directory anywhere
on disk retaining raw samples. If this parser ever silently mis-handles a
real message the way FDPS did (Finding 3), there would be no raw-sample
trail to diagnose it from. Not built here (new capture infrastructure is a
feature addition, not harness-verification); flagging as a real,
concrete gap.

## Finding 10 (real, quantified): the 17 pre-existing test failures — root-caused, not just labeled "stale"

Ran the full suite fresh on this branch: **17 failed, 108 passed** (114
with the 6 new FDPS tests added). Verified via `git stash` (same
methodology the 2026-08-16 drift-audit agent used) that all 17 fail
identically on bare `main` with none of this session's changes present —
genuinely pre-existing, not something I or today's work caused. (Note:
this branch's 108/114 baseline differs from the drift-audit branch's
previously-reported 138 passing — that branch carries its own additional
fixes and 4 new test files not present here; not reconciled further, out
of scope for this task.)

Root-caused, not just re-labeled "stale":

- **11 of 17** (`tests/shared/test_watchlist.py` — 9, `tests/web/test_watchlist_batch.py`
  — 2) fail with `sqlite3.OperationalError: table watchlist_entries has no
  column named hex_id`. Cause: the test setUp manually calls
  `_db.init_db()`, `init_db_v2()` through `init_db_v5()` only.
  `hex_id` was added in schema **v18**. The real schema is currently at
  **v33** (`grep '^def init_db_v' src/common/db.py` — 33 migration
  functions exist). The test DB init chain is **28 schema versions behind
  production**. This is test-infrastructure drift, not a production bug —
  confirmed production's real `watchlist_entries` table does have
  `hex_id` (queried directly against the live DB earlier this session).
- **5 of 17** (`tests/runner/test_proxy_dispatch.py`) fail with
  `AttributeError: module 'runner.main' has no attribute
  '_dispatch_proxy_headers'` — that function no longer exists anywhere in
  `src/runner/main.py` (confirmed via grep, no similarly-named replacement
  found either). Whatever proxy-header logic exists now, these 5 tests are
  fully orphaned against it.
- **1 of 17** (`test_marine_one_detection.py::test_smes_parser_basic`) —
  not individually root-caused (likely shares the same schema-drift DB
  helper as the watchlist tests; not confirmed).

Not fixed here — the task's own ground rules say diagnose, don't
side-quest-fix beyond trivially safe, and a 28-version schema-chain repair
plus reconciling a fully-renamed function's test suite are both real work,
not one-liners.

## Coverage survey (not deep-tested, documented as a gap)

123 source files under `src/`, 16 pre-existing test files (now 17). Most
strikingly: **`src/poller/skills/` (37 files — the actual brief-generation
logic behind every scheduled skill: `ep_advance_brief.py`, `ops_brief.py`,
`second_brain_daily.py`, etc.) has zero test files.** Also untested:
`common/db.py` (the entire ~4,300-line schema/query layer),
`common/flight_resolver.py` (the file the 2026-08-16 drift audit flagged
as having a permanently-dead SWIM tier — still no test would have caught
that regression or would catch a recurrence), `common/guardrails.py`
(SR1/SR2 safety-rail logic). Not deep-tested in this pass — flagged
honestly rather than silently left uncovered.

## Branch state

19 changed/new files, uncommitted on `smoke-test-harness-2026-08-17`
(off `main`). `git diff main...smoke-test-harness-2026-08-17` for the full
diff. No push, no commit, no merge — operator's call on all of it.
