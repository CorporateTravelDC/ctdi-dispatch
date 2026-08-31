# Live-state check 2026-08-31 post-2999633 (disruption detectors, UTM/drone, report-1 on-demand, CPU rework)

Post-commit doc-drift pass for `2999633`, run ~06:30–07:00 EDT 2026-08-31.
Scope per operator directive: does anything the current docs claim get
invalidated by THIS commit — not a from-scratch rewrite. Verified against
live state (`systemctl --user show`, `podman ps`/`image inspect`, installed
quadlet diffs, `curl`, poller journal), not against the docs themselves.

Prior art consulted first (second brain): `20260831T065821Z.md` (report-1
on-demand rollout, pre-commit, one open item: ep-advance 3600s recalibration
"STAGED, needs re-sign + poller rebuild") and `20260831T102515Z.md` (the
post-commit session consolidation — records that *everything* from the
day's passes, including RVR-into-CPS, was deliberately deployed, with the
CFR operator-verification still flagged open). This check builds on both.

## Drifted — invalidated by this commit

1. **`src/shared/watchlist_README.md` — "Three entry types" is now four.**
   Line 13 ("Three entry types: flight, train, vessel"), the file-map
   table (lines 24–28), the id-convention list (line 62), and the ntfy
   topic table (lines 173–177) all predate the `drone` entry type this
   commit added (`shared/watchlist.py` `EntryType` + `permanent_drones.json`
   in `_FILE_MAP`, verified in code). The REST/API tables in the same file
   remain accurate — no drone add/remove endpoints were created
   (`web/routes/watchlist.py` untouched; utm_watcher only *reads*
   `/api/v1/watchlist`), so only the entry-type enumeration drifted, not
   the API surface.

2. **`docs/GUARDRAILS_JUSTIFICATION.md` §3 — ingest CPU numbers stale.**
   Lines 128–129 claim "The seven ingest containers carry `CPUWeight=30`";
   this commit raised all seven to **5000** (verified live: all seven units
   report CPUWeight=5000). Three rows of the CPUQuota table also drifted:
   tbfm 80% → **100%**, tfms 90% → **110%**, stdds 120% → **140%**
   (verified live: 1s/1.1s/1.4s CPUQuotaPerSecUSec). Still correct:
   itws/notam 60%, core 80%, fdps 150%. (The section's Ollama-era
   `ollama.service.d` narrative was already on the 08-29/30 passes' open
   edit list — pre-existing, not this commit's.)

3. **`docs/ALERT_REFERENCE.md:164` — fdps-alerts family undercounts its
   contributing feed_names.** The row enumerates exactly two (`fdps`,
   `fdps_notam`); this commit added two more `fire_family_alert("fdps", …)`
   call sites, both new in 2999633 (absent from 385a5f6):
   `fdps_alt_saturation` (`fdps_parser.py:1173`) and
   `fdps_diversion_continuation` (`fdps_parser.py:1380`) — both base
   priority 4, `escalating_only=False`, `isolate=True`. Same doc's
   `flight-alerts` publisher row lists the undeployed `ais_watcher` default
   topic but not the new (equally undeployed) `utm_watcher`, whose
   `NTFY_TOPIC` also defaults to `flight-alerts` (`utm_watcher.py:64`).

4. **`CLAUDE.md` — the RVR-into-CPS "deliberately NOT deployed" paragraph
   is now false.** The 2026-08-30 SWIM-audit entry says the RVR scoring
   half was "staged, deliberately NOT deployed … (image not rebuilt)" and
   "needs operator verification against the actual CFR text before this
   scoring half goes live." This pass's poller image rebuild (10:14 UTC,
   for the report-1/detector deploy) carried the RVR code along: the
   running poller container is on that image, `cps_recompute.py`'s RVR
   path has no feature gate, and the skill runs hourly. **The 14 CFR
   91.175(h) correlation values are therefore live while the flagged
   operator verification remains open.** This is *known and intentional*
   per the post-commit consolidation note (20260831T102515Z: "everything
   above is deployed … operator-verification flagged, not independently
   confirmed") — the drift is CLAUDE.md's stale text, not a rogue deploy.
   Mitigating by design: RVR can only ever tighten a score, never loosen
   one. CLAUDE.md is a write-only scratchpad per its own header, so this
   is recorded here rather than edited there. Note the daily
   `CLAUDE_MD_DRIFT_REPORT.md` ("No drift found", generated 05:15 EDT —
   pre-commit) cannot see this class of semantic drift.

## Watch items (not drift)

- **First `cps-recompute` run on the new image failed**: 06:18:29 EDT,
  `compute error: database is locked` (poller journal). Every prior run
  on the old image was `rc=0`; the failure coincided with the daily-watch
  herd (4 poller-image containers running). Looks like transient SQLite
  contention, not the RVR change — but it means the RVR scoring path has
  **not yet completed a live run**. Verify the next hourly fire.
- ep-advance's in-flight run (started 05:35 EDT) predates the 10:14 UTC
  image rebuild, so it still carries the old 2800s timeout. The
  consolidation note's backlog item 1 ("watch the next real ep-advance
  fire end-to-end") stands; the *next* fire is the first real test of
  3600s. Trivial: `ep_advance_brief.py:1000`'s comment still says
  "2800s" though the constant at :119 is 3600.
- `docs/dispatch-runner-design.md` doesn't list `/api/utm/drones` or the
  `/utm` view, and its undeployed-hardware list (line 204) predates
  utm-watcher — but that doc self-describes as covering "roughly half the
  current API surface," so this is noted, not counted as drift.

## Checked, still accurate

- **report-1 on-demand wiring**: tracked == installed for all three
  consumer quadlets (ep-advance/dispatch-desk-memo/second-brain-weekly),
  `production.slice`, and `corporatetraveldc-llama-report-1.service`.
  Hooks point at the repo's `scripts/llama-report-ondemand.sh` (no
  /usr/local/bin copy expected). report-1 was *active* during this check —
  correctly: ep-advance was `activating` (mid-run), the exact case the
  stop-if-idle guard exists for. Unit is `disabled` (on-demand posture) ✓.
- **The 06:58 UTC note's open item is CLOSED**: manifest re-signed in the
  commit (verify-manifest OK, 837 files) and the poller image rebuilt
  10:14 UTC with `OLLAMA_TIMEOUT=3600` inside (verified in-image);
  ep-advance quadlet installed with `TimeoutStartSec=5000`.
- **`src/ingest/README.md`** (rewritten by this commit): spot-verified new
  claims against code — `EntryType` drone + `permanent_drones.json`
  hot-reload, `db.init_db_v43()`/`update_watchlist_uas_phase()` dedicated
  uas_phase columns, utm_watcher UDP :5007 default, `UTM_STATIC_IDS` pins,
  quadlet shipped `.disabled`. All accurate. (Its line-58 LOCKDOWN "host
  `ollama.service`" row remains on the pre-existing I4 open edit list.)
- **UTM posture matches docs**: no utm quadlet installed live (ships
  `.disabled` — same class as ais-watcher), `permanent_drones.json` is an
  empty watchlist, and `/api/utm/drones` on the live runner (:8001)
  returns the honest fallback shape
  (`{"source":"none","drones":[],"count":0,"detail":"no_source_configured"}`) ✓.
- **Ingest CPU rework deployed**: live CPUWeight/CPUQuota on all seven
  ingest units match the tracked quadlets exactly (see item 2 values).
- **`README.md`**: nothing this commit touched invalidates any claim not
  already on the standing I4 (Ollama-era) edit list. Model/skill table at
  :663 and governed-`ollama.service` note at :643 are pre-existing items.

Net: three real doc-file drifts (watchlist_README entry types,
GUARDRAILS ingest CPU numbers, ALERT_REFERENCE fdps family/utm topic) plus
one safety-relevant CLAUDE.md staleness (RVR scoring live with CFR
verification still open — already vault-recorded as intentional). Live
deploy state itself matches the tracked tree everywhere checked.

---

# Part 2 — post-2bdf60d (nwws heartbeat fix + SIGMET-archiver signing pass)

Second post-commit drift pass, run ~07:55–08:10 EDT 2026-08-31, scoped to
commit `2bdf60d` (nwws.py push:nws heartbeat fix, bundled with the signing
of the previously-staged SIGMET archiver / airsigmet factor-out /
watermark / index_db hyphen-fix set). Prior art consulted: Part 1 above,
vault notes `20260831T102515Z.md` (day consolidation) and
`20260824T110837Z.md` (drift-checker blind spots) — nothing there covers
this commit's changes; this pass starts from the CLAUDE.md open items.

## Drifted — invalidated by this commit

1. **The commit's headline fix is NOT deployed — committed only.** The
   running `systemd-corporatetraveldc-ingest-core` container is on the
   ingest image built **20260831T032718Z** (23:27 EDT Aug 30) — hours
   before the nwws fix was written. Verified inside the live container:
   `/app/src/ingest/nwws.py:434` still has the bare, unwrapped
   `failover.mark_push_healthy("nws")`. The container restarted 07:42 EDT
   today, but a restart on the old image deploys nothing. `push:nws`
   currently reads fresh (heartbeat 11:53:36Z) **only because of that
   restart** — the next transient `database is locked` re-kills the
   heartbeat task exactly as before, until the ingest image is rebuilt
   and ingest-core restarted on it. CLAUDE.md's closing "Investigating,
   not yet resolved" paragraph is simultaneously stale in the other
   direction (the investigation IS resolved, root cause found and fixed
   in-repo) — recorded here per the Part 1 precedent rather than edited
   there (write-only scratchpad per its own header). **Action needed:
   ingest image rebuild + ingest-core restart.**

2. **`src/ingest/README.md` "2026-08-31 pass" header — "built + tested,
   staged only, same not-deployed discipline" is false as of this
   commit's signing.** The SIGMET archiver is fully deployed: timer
   installed and `enabled` (active since 06:58:50 EDT, 10-min cadence),
   integrity gate passing since the 07:03 run (the 06:58 INTEGRITY
   FAILURE was the last, exactly as CLAUDE.md predicted), and 16
   convective SIGMETs archived at 11:03:38Z. CLAUDE.md's own entry
   ("Built and deployed 2026-08-31") already says deployed — the two
   docs contradicted each other at commit time; live state settles it as
   deployed. The README's parenthetical is the drift.

## Resolved — CLAUDE.md open items closed by this pass (verified live)

- **"poller+web images will need one more rebuild after signing" — DONE.**
  Poller rebuilt 11:02:43Z, web 11:03:13Z (post-signing), and the running
  poller/web containers are on those exact images (image IDs match
  `:latest`, both up since ~07:04 EDT). No `verify-manifest` failure on
  any unit since 06:58:57 EDT. The "expected/self-resolving" integrity
  failures (daily-opsplan et al.) are over.
- **index_db FTS hyphen fix is live for host-side use** (runs from the
  checkout, no container in the loop): `second-brain-search.sh NWWS-OI`
  and `--raw 'NWWS-OI AND heartbeat'` both return results unquoted.

## Watch item (external, not drift, not a code bug)

- **The SIGMET archiver has failed 5 consecutive scheduled runs**
  (07:14 → 07:54 EDT, all `_ssl.c:1015: The handshake operation timed
  out` against `aviationweather.gov/api/data/airsigmet`). Verified
  external + intermittent: host-side probes of the same URL ranged
  0.18s–9.7s with one >120s hang, on both IPv4 and IPv6, while
  `/api/data/metar` on the same host answers in 0.3s — AWC-side
  flakiness, likely worse through the container egress path (the web
  overlay container DID fetch successfully ~11:55Z; not root-caused this
  pass). The skill's exit-0-on-failure is deliberate and commented
  (`convective_sigmet_archiver.py:88–94`), so systemd shows success —
  failures are visible only in the journal/`log_usage` status. Archive
  depth is stuck at the initial 16 rows and has missed the 11:55Z
  issuance cycle so far; ~2h SIGMET validity means it recovers with no
  permanent loss if runs succeed again within the window. If this
  persists past a few hours, Detector D's attribution history accrues
  nothing — worth a look at container-vs-host egress (MTU/pasta?) then.

## Checked, still accurate

- `README.md:163` "NWWS-OI (NWS push) ✅ Live" — true (connected,
  heartbeat fresh). `:272` `/api/v1/airmets` — verified serving live
  convective polygons on the new web image; the response's 3 new
  normalizer keys are additive/ignorable as the factor-out intended.
- `convective_sigmet_archive` live schema matches `db_swim.py` v47
  exactly (`UNIQUE(sigmet_id, valid_from)`, window index); table lives in
  the shared DB, created by the skill itself as documented.
- `src/shared/watchlist_README.md` — untouched by and unaffected by this
  commit (Part 1 item 1's entry-type drift stands, nothing new).
- Known-open, unchanged: `local_airspace.py` UltraFeeder poll still
  failing (`Connection refused`, logged up to the 07:42 restart) —
  pre-existing, still under investigation, not this commit's.

Net: one real deploy gap (the nwws fix itself — needs ingest rebuild +
core restart), one doc contradiction settled (ingest README "staged
only" → deployed), one external watch item (AWC handshake timeouts
starving the new archive), and two CLAUDE.md open items verifiably
closed. Both real findings persisted to the vault by this pass.

---

# Part 3 — weekly scheduled drift check (~09:00 EDT)

Weekly docs-drift pass, run ~09:00–09:10 EDT. No commits since `2bdf60d`
(Part 2's subject), so scope = Parts 1–2's open action/watch items plus
the current uncommitted working set (manifest re-sign 07:52 EDT,
`scripts/scrub-public-tree.py` GUFI substitution 07:59 EDT, this doc).
All claims below verified live (podman inspect/exec, systemctl, journal,
read-only sqlite3, curl), not from docs.

## Resolved since Part 2 (verified live)

1. **Part 2's headline deploy gap is CLOSED — and the fix already proved
   itself.** Ingest image rebuilt 11:56:19Z (build 20260831T115556Z),
   `ingest-core` restarted 07:56:28 EDT on that exact image ID, and
   `nwws.py` inside the running container carries the fix. Then at
   08:27:44 EDT the precise failure mode fired for real — `push:nws
   heartbeat write failed (database is locked); retrying next tick` —
   and the heartbeat task survived it: `push:nws` fresh at check time
   (13:03:17Z, ~1 min old). CLAUDE.md's closing "Investigating, not yet
   resolved" nwws paragraph is now fully stale: root-caused, fixed,
   deployed, live-verified. (The six feed-specific ingest containers
   remain on the prior 20260831T032718Z image — acceptable: the only
   in-image delta they'd care about is `nwws.py`, which only core runs;
   the `db_swim.py` v47 delta is archiver-skill/poller-side.)

2. **`local_airspace` UltraFeeder poll FIXED (CLAUDE.md's other "under
   active investigation" item).** `/etc/corporatetraveldc/dispatch.env`
   was edited 07:47:56 EDT to `ULTRAFEEDER_URL=http://100.x.x.x:8080`
   (Tailscale IP, replacing the failing `host.containers.internal:8080`
   path), picked up by the 07:56 core restart. Verified: the endpoint
   serves live `aircraft.json` (real traffic, 5.0M messages counter) and
   the ingest-core journal shows zero UF poll errors since restart —
   after 5 days of continuous failure.

3. **The ADS-B dongle is BACK — CLAUDE.md's "REAL, hardware, NOT fixable
   remotely" item is over.** Both RTL2838s enumerate on USB,
   `/dev/rtl_sdr_adsb -> bus/usb/003/002` exists (udev-stamped at the
   2026-08-30 08:23 reboot), and `corporatetraveldc-ultrafeeder.service`
   has been active *without a single restart* since 08-30 08:24:52 —
   the 541+-restart crash loop ended at that reboot (reseat/hub
   power-cycle, presumably). Note the 08-30 check's line 138–139
   ("dongle still absent — no change") was already wrong for most of
   that day. README's feed-table row 76 ("✅ Restored 2026-08-11") reads
   correct again by coincidence; it never recorded this second
   (08-29/30) absence episode — cosmetic only.

4. **Part 1's cps-recompute watch item CLOSED.** After two more
   `database is locked` failures (07:01, 07:04 — three consecutive
   total), the 08:10:10 EDT hourly fire completed `rc=0` on the
   RVR-enabled image and wrote its score row (12:10:09Z, GREEN/GO).
   The RVR scoring path has now completed a live run. The 14 CFR
   91.175(h) operator verification remains open as flagged.

## Still open / watch items

5. **SIGMET archiver: limping, not starving.** One success since Part 2
   (12:04:12Z — archive depth 16 → 28 rows, so the midday issuance
   cycle WAS captured; no permanent gap yet) but the 08:44 and 08:54
   runs failed on the same SSL handshake timeout. Re-confirmed external
   this pass: host-side `airsigmet` curl timed out at 8s while
   `api.weather.gov` answered in 1.1s, and a host-side AWC METAR probe
   failed once then answered 0.9s on retry — AWC itself is flaky, both
   endpoints, no box-egress problem. No action available in this repo.

6. **ep-advance went deterministic AGAIN — first real test of the 3600s
   recalibration is a negative datapoint.** The run finishing 08:39:44
   EDT started ~07:38 on the new poller image (build 20260831T110243Z,
   `TimeoutStartSec` 5000s confirmed live), consumed ~3699s wall, and
   still ended `Ollama unavailable, busy, or failed … returning None`
   → deterministic fallback — i.e. it appears to have ridden the full
   3600s out under this morning's daily-watch herd (pre-flight load
   gate logged load 19.10 in the next run). The consolidation note's
   backlog item 1 ("watch the next real ep-advance fire end-to-end")
   now has its answer: 3600s did not suffice under load. Next run was
   in flight during this check (started 08:39:48, load gate passed at
   08:43 with load 6.67) — that quieter run is the better test.

7. **`corporatetraveldc-integrity-sweep.service` failing every 15 min
   since 08:19 EDT on exactly one file:** `scripts/scrub-public-tree.py`
   — the uncommitted GUFI-substitution edit (07:59:38 EDT) landed
   *after* the working tree's manifest re-sign (07:52:17 EDT), so it's
   unsigned. Same expected-until-signed class as always; clears on the
   next signing pass. (This doc is not in the manifest, so Part 3's
   append does not widen the window.)

8. **NEW watch item — plain-ACARS is silent while VDL2 flows.**
   acars_router's 5-min counters: VDLM 79 msgs, ACARS/HFDL/IRDM/IMSL
   all 0; and ingest-core's (new) instrumentation warns its
   acars_router reader on :9080 received **zero lines in 3600s**
   despite that VDLM volume — either :9080's output carries only
   plain-ACARS (itself at zero, though the acars dongle is present) or
   the reader is pointed at the wrong output. Not root-caused this
   pass; worth a look if it persists.

9. Minor/transient: `push:amtrak` shows 1 consecutive failure
   (api.amtraker.com read timeout, 12:59Z) — self-healing class, noted
   only so a recurrence has a baseline.

Net: four prior open items verifiably closed (nwws fix deployed+proven,
local_airspace UF poll fixed via dispatch.env, ADS-B dongle/ultrafeeder
recovered since the 08-30 reboot, cps-recompute clean run), the SIGMET
archiver partially recovered but still AWC-limited, one negative result
recorded (ep-advance deterministic even at 3600s under load), one
unsigned-file integrity window awaiting the next signing pass, and one
new ACARS-silence watch item. Docs themselves needed no correction
beyond what Parts 1–2 already logged — the drift this week is CLAUDE.md's
open-items list running behind reality (all in the good direction),
recorded here rather than edited there per the standing convention.

---

# Part 4 — post-7763185 (push-public fetch-and-retry + scrub UUID fix)

Fourth pass, run ~09:55–10:05 EDT, scoped to commit `7763185`
(push-public.sh fetch-and-retry on missing parent object,
scrub-public-tree.py GUFI→placeholder templating, manifest re-sign).
Prior art consulted: vault hits for push-public.sh — `20260826T040816Z.md`
(post-22a66a6 drift check, RFC-5737 safe-block prediction),
`20260826T213148Z.md` (C-0c URL-check self-block incident), and the
project-knowledge synthesis rule ("public push must go through
`scripts/push-public.sh`, never raw `git push public main`"). None cover
the missing-parent-object failure class — this pass starts cold on that,
warm on the surrounding mechanism.

## REAL finding — CONTRIBUTORS silently dropped from the public mirror tip

Not doc drift; a live consequence of this commit's own fix that the
commit message doesn't surface. Evidence chain, all from local objects
plus one anonymous HTTPS `ls-remote` (SSH auth unavailable this session):

- `b099926` "Update CONTRIBUTORS", 09:20:49 EDT, committer
  `GitHub <noreply@github.com>` (web-flow signed) — the GitHub-web-UI
  commit the fix's message cites. Its tree **contains `CONTRIBUTORS`**: a
  substantive, clearly-meant-to-be-public credits file ("Living
  Document" — named collaborators, Airframes.io community members, FAA
  programs), not a throwaway test file.
- `88af21b`, 09:46:33 EDT, operator-key-signed
  `chore(public): sanitize…` snapshot, correctly parented on `b099926`
  via the new fetch-and-retry path (`.git/FETCH_HEAD` still shows the
  fetch). Its tree `61818af` has **no CONTRIBUTORS** — by design the
  snapshot tree is scrub(private HEAD), and the private tree never had
  the file.
- `ed55efa`, 09:54:16 EDT (the post-commit re-push), same tree, parented
  on `88af21b`. Anonymous `ls-remote` confirms `ed55efa` is the live
  `refs/heads/main` on github.com/CorporateTravelDC/ctdi-dispatch.

Net: 26 minutes after the operator added CONTRIBUTORS on the web UI, the
mirror push removed it from the tip. It survives only in history (under
`b099926`), invisible on the repo's front page, and every future
push-public run keeps it absent. The fix preserved history *linkage*
(that was the bug fixed) but the deeper property — any public-only file
is deleted from the tip on the next snapshot — is inherent, undocumented,
and probably not what the operator expects here. **Recommended fix
(operator decision, deliberately not applied by this no-commit pass):
adopt the file into the private tree** — `git cat-file -p 5b72c62 >
CONTRIBUTORS`, review, commit+sign. The scrubber is denylist+pattern
(DROP_FILES + email/UUID/IPv4 allowlist scan) and the file contains no
scannable patterns, so it passes through unmodified. Persisted to the
vault by this pass.

## Verified working as committed

- **Fetch-and-retry exercised live, not just written**: the parent chain
  above *is* the retry path succeeding (the 09:46 run's parent only
  existed remotely). The 09:54 run then parented on the locally-created
  `88af21b` with no fetch — the normal path still works post-fix.
- **UUID fix in effect**: both fixture files carry the real FDPS GUFI
  exactly once each (private tree keeps real data — correct);
  `SUBSTITUTIONS` and `ALLOWED_UUIDS` gained matching `…000027` entries;
  and two pushes completed after the fix, which requires
  `verify_scrubbed()` to have passed against the full tree twice.
- **Part 3 item 7 CLOSED**: integrity sweep at 09:49:08 EDT — `sweep OK
  … all 844 files match`. The unsigned-`scrub-public-tree.py` window
  ended with this commit's signing, on the first sweep after it.

## Checked, still accurate — no scoped doc invalidated by this commit

- `README.md` — no push-public/mirror claims at all. `CLAUDE.md` —
  nothing it says touches these scripts; its open `sudo-approval-gate.sh`
  item is unrelated and unchanged. `src/ingest/README.md` and
  `src/shared/watchlist_README.md` — untouched by and unaffected by this
  commit.
- `docs/INFRA_MAP.md:93` "Public repos are produced by `push-public.sh`
  (force-push, auto-sanitizing)" — still true; behavior was extended,
  not changed. `scripts/pre-commit-README.md:43` (pre-push blocks direct
  public pushes) and `SECURITY.md:65-66` (scrubber exclusion) —
  unchanged mechanisms, still accurate.
- The 08-25 check's description of parenting behavior (parent = public
  tip via ref lookup; orphan root when no tip) is a dated snapshot now
  *extended* by the fetch-and-retry case — noted here per convention,
  not retro-edited.

Net: no doc drift from `7763185` itself; the commit's two fixes verified
live end-to-end; one prior open item closed; one REAL new finding — the
public mirror's tip lost the operator's CONTRIBUTORS file to the
snapshot design, needs the file adopted into the private tree to stick.

---

# Part 5 — pre-commit check of the LADD/Tier-gate/react-router working set (~11:23 EDT)

Fifth pass, run ~11:20–11:23 EDT. Not a post-commit check like Parts 1–4
— this working set (LADD rebuild, `/api/v1/aircraft` Tier-gate fix,
demo-scrub LADD enforcement, react-router CVE fixes) is staged and
manifest-signed but not yet committed. Scope: verify the deployed state
actually matches what's staged, since three images were rebuilt off it
this session. CONTRIBUTORS finding from Part 4 remains open and
unaddressed by this pass — not this working set's concern, flagged
again here only so it isn't lost between parts.

## Verified live, matches staged working set

- **LADD list rebuilt**: `faa_ladd_aircraft` holds 70,874 rows (was 0 —
  automated fetch dead since June). No literal identifiers recorded in
  this doc, consistent with `CLAUDE.md`'s handling of the same data —
  see `docs/LADD_CUI_HANDLING.md` for the policy, `faa_ladd_aircraft`
  itself for the data.
- **Tier-gate fix deployed and functioning**: `corporatetraveldc-web`
  active since 11:00:42 EDT on the rebuilt image. Live-tested against a
  real LADD-listed identifier from the imported set: an unauthenticated
  request to `/api/v1/aircraft/{id}` returns `ladd: false` while the DB
  row for that same identifier confirms it's genuinely listed — the
  mask is real, not coincidental.
- **Demo-scrub LADD enforcement**: `find_ladd_violations()` unit-tested
  against the live imported set (not synthetic data) — a string
  containing two real listed identifiers correctly flagged both
  (2 violations), an ordinary weather-brief-shaped string correctly
  flagged zero. Not yet exercised by a real `scrub-demo-source.py` run
  this pass (no new snapshot/brief content has been promoted since the
  code changed) — the check above verifies the function, not a live
  promotion cycle.
- **react-router CVE fix**: `corporatetraveldc-runner` active since
  11:00:43 EDT on an image built from a real (non-cached)
  `frontend-builder` stage — confirmed the COPY step's output hash
  changed, not a stale cache hit. `npm audit --omit=dev` clean (0
  vulnerabilities, was 2 moderate); `npm audit fix` also cleared 4
  unrelated dev-tooling-only high-severity CVEs (never shipped).
- **`corporatetraveldc-poller`** active since 11:00:41 EDT on the image
  carrying the `scrub_rules.py`/`scrub-demo-source.py` changes — all
  oneshot skill containers sharing this image (SIGMET archiver
  included) will pick it up on their next scheduled fire.
- **Manifest integrity**: sweep at 11:19:10 EDT — `sweep OK … all 846
  files match`. No `verify-manifest` failures logged on any of the
  three restarted units.

## Still open (not this pass's to fix, restated for continuity)

- **Part 4's CONTRIBUTORS finding** — still open, still not adopted
  into the private tree. Every `push-public.sh` run since `7763185`
  keeps dropping it from the mirror's tip.
- **Part 3 item 6** (ep-advance 3600s under load) and **item 5** (SIGMET
  archiver AWC handshake flakiness) — no new data this pass, not
  re-checked.
- This working set itself is uncommitted as of this pass — signed
  manifest covers it, operator commit pending.

Net: everything staged in this working set (LADD rebuild, Tier-gate
fix, demo-scrub enforcement, react-router CVEs) verified live and
functioning as intended across all three rebuilt images. No new drift
introduced. CONTRIBUTORS remains the one real open item from Part 4.
