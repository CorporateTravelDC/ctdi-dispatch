# Live State Check — 2026-09-03 (post-commit 457c61d)

Doc-drift check scoped to commit `457c61d` ("Forward-only content-hash
dedup redesign for SWIM alerts + watchlist path"), run ~03:10–03:25 EDT,
minutes after the commit landed. Checked README.md, CLAUDE.md, docs/
(ALERT_REFERENCE.md, ALERT_ARCHITECTURE.md, INFRA_MAP.md,
REFERENCE_INFRA.md), src/ingest/README.md, src/shared/watchlist_README.md
— against the diff, the current source, and the live system (systemctl
--user, podman ps/images, dedup state files in /var/lib/corporatetraveldc).

Prior art consulted first (second-brain search: `push_dedup`, `dedup`,
`forward-only`, `content-hash`, `UAL1369`): vault notes
`corporatetraveldc/01-Sources/manual/20260817T015317Z.md` and
`20260817T015915Z.md` document the 2026-08-16 slot/content_key-inversion
fix across seven call sites that this redesign directly builds on — the
"windows were never the problem, slot identity was" finding. Nothing in
the vault yet documents the forward-only redesign itself (the newest
session note, `20260903T031114Z.md`, predates this commit), so this
check's real-drift findings are persisted there as well.

## Live-state verification (all healthy)

- All 5 platform images (`poller`/`pusher`/`ingest`/`web`/`runner`)
  rebuilt 2026-09-03 04:38 UTC (00:38 EDT) — after the night's source
  edits and manifest signing, before the git commit itself (the usual
  sign → rebuild → deploy → commit sequence). All containers restarted
  ~01:10 EDT and running; **0 failed user units** — the 2026-09-02
  "expected/self-resolving verified-exec" CLAUDE.md entries have indeed
  resolved as predicted.
- The redesigned code is live: dedup state files actively written
  post-deploy (`pusher-tfms_alerts` 03:12, `pusher-tfms_gadv_alerts`
  03:09, `pusher-stdds_taxi_alerts` 03:12, `pusher-notam` 02:04 EDT).
  `pusher-watchlist-event-dedup.json` holds the UAL1369 slot
  (`wl-flight-ual1369-20260902:fids_update`) that motivated the redesign.
- Caller split audited against source — matches the commit message
  exactly. `should_push_periodic()` (deliberate time-based semantics):
  `ntfy_push.py:210` + `watchlist.py:1040` (90s ambiguous-status TTL),
  `fdps_parser.py:1891/:1957` (proximity episode gates, 600s),
  `itws_parser.py:766` (severe-wx 20-min heartbeat, with an explicit
  stay-periodic comment at :729), `ingest_feed_watch.py:171`,
  `route_impact.py:195`, `tfr_enrichment.py:54`, `pusher/main.py:123`
  (VIP TFR) and `:463/:495` (flight-landing). Every other call site is
  forward-only `should_push()`.

## Drift found (real — docs invalidated by this commit)

None of these were updated by the commit (its only ALERT_REFERENCE.md
change was the daily-watch line-number table). Report-only: no doc edits
made this pass — fixing them means another sign cycle, operator's call.

### 1. src/shared/watchlist_README.md:190–192 — the headline drift

> "**Deduplication:** the same `entry_id` + `event_type` (content-aware —
> detail hashed with timestamps bucketed to 10-minute windows) will not
> re-fire within 5 minutes (`_DEDUP_WINDOW_SECS = 300`)."

This describes exactly the behavior the commit was written to kill, on
exactly the path (watchlist `_check_dedup`) the UAL1369 TMI re-page
incident hit. Now: unchanged content never re-fires on elapsed time —
suppression lasts as long as PushDedup retains the slot (floor 7 days);
only a genuine content change (or the fca_id sub-identity split) fires
again. `_DEDUP_WINDOW_SECS = 300` still exists at `watchlist.py:87` and
is still passed as `dedup_secs`, but no longer governs re-firing for this
caller. The 10-minute timestamp bucketing claim is still accurate
(`_TS_BUCKET_MINUTES = 10`).

### 2. docs/ALERT_REFERENCE.md — four per-parser dedup descriptions now wrong

The canonical alert-path doc still describes time-window semantics for
paths that are now forward-only:

- **GADV** (~line 296): "on the advisory number (**not content hash** — …
  the number itself is the correct 'have we shown this one' key, 1-hour
  window)". Now content-keyed on `advisoryTitle|advisoryText`
  (`tfms_parser.py:1495`) precisely so in-place revisions under the same
  advisory number re-fire; there is no 1-hour re-fire window anymore.
- **NOTAM** (~line 328): "Deduped via `_NOTAM_DEDUP` on the NOTAM ID — a
  NOTAM re-transmitted unchanged won't re-fire; an amended NOTAM (**new
  ID**) will." Mechanism is now slot=ID, content=classification +
  effective window + full text (`aim_parser.py:285`), so an amendment
  under the **same** ID re-alerts. Note the doc's "unchanged won't
  re-fire" was actually false before this commit (~daily repeats) and is
  only now true. Operational side note: the code's own migration comment
  says each already-alerted live NOTAM re-alerts once on next rebroadcast
  post-deploy, then goes quiet — expect one bump, not a regression.
- **APTC** (~line 293): "on `aptc:{airport}:{rate}:{weather}` (15-min
  window)". Doubly stale — the key was split into slot=airport /
  content=rate:weather back on 2026-08-16 (this doc never caught up),
  and the 15-min window no longer applies.
- **TBFM** (~line 260): "on `tbfm:{fix}:{seq_count}`". Now
  slot=`tbfm:{fix}`, content=**band-bucketed** count
  (`bucket_count(seq_count)`, ±1 jitter hashes identically). The doc's
  conclusion ("holding steady won't re-fire; a genuine change will") is
  directionally still right, but a ±1 wiggle no longer counts as change.
- **TFMS amendment header note** (~lines 104–109): "against the 30-min
  `_TFMS_ALERT_DEDUP` window … previously … no dedup beyond the generic
  5-minute window". The window framing is obsolete; ironically its
  "suppressed indefinitely" claim only became true with this commit.

### 3. src/ingest/README.md — same window framing, two spots

- Lines 513–524 (amendment dedup): "against the shared 30-minute
  `_TFMS_ALERT_DEDUP` window", "generic 5-minute watchlist window" —
  same obsolete framing as above.
- Lines 212–213 (REROUTE → watchlist): "6 h dedicated dedup … window so
  a real revision fires immediately" — `_REROUTE_WATCHLIST_DEDUP` is a
  forward-only caller now (`tfms_parser.py:2393`); revisions still fire
  immediately, but there is no 6-hour re-fire cadence.

### 4. Top-level one-liners: README.md:412, docs/INFRA_MAP.md:648, docs/REFERENCE_INFRA.md:173

"5-minute content-aware dedup" (README, INFRA_MAP) / "A short dedup
window prevents re-firing the same event repeatedly" (REFERENCE_INFRA).
The honest one-liner is now "content-change-only dedup" — five minutes
plays no role on the watchlist path.

## Verified still accurate (checked, no drift)

- ALERT_REFERENCE.md's **ITWS** section (20-min window, content change
  fires immediately) — that path deliberately stayed
  `should_push_periodic()`; the doc happens to remain exactly right.
- **FDPS proximity / meter-fix proximity** (600s episode gating), **VIP
  TFR** (1-hour window + `hot=True` bypass in pusher/route_impact/
  tfr_enrichment), **feed-health** watch, **flight-landing** dedup — all
  periodic by design, docs unaffected.
- Marine One "no dedup window, every detection fires" — unchanged.
- STDDS incursion path's previous_bitmask change-gate description —
  unchanged (and its "a time window would be the weaker gate" rationale
  now describes the whole platform's default).
- docs/ALERT_ARCHITECTURE.md — its "window" references are sector-trend
  comparison windows, not PushDedup; clean.
- `src/ingest/README.md:459` "50 NM approach alerts (10-min dedup)" —
  still true (periodic caller).
- Dated audit/investor documents (DRIFT_AUDIT_2026-08-16, OPUS blind
  reviews, investor-materials v1.5 "5-minute content-aware dedup"
  claims) — frozen historical snapshots, not current-state claims; noted,
  not counted as drift.

## Pre-existing drift observed in passing (NOT from this commit)

The banned airplanes.live flight-monitoring chain (purged from all code
2026-08-27/28; skills/flight-hifi-track/SKILL.md caught 2026-08-31) is
still documented as current in three places nobody swept:

- README.md:413–415 — "Flight monitoring source chain: FlightAware
  AeroAPI (if key set) → airplanes.live (free) → local UltraFeeder…"
- docs/REFERENCE_INFRA.md:174 — "Flight tracking defaults to the free
  `airplanes.live` API, with an optional FlightAware AeroAPI fallback…"
- src/shared/watchlist_README.md:200–202 — "…local FAA/OpenSky
  registries before airplanes.live's flaky `/v2/reg/`" (describes the
  pre-purge chain as current).

Code verified clean: `watchlist.py`'s remaining references are historical
comments plus the allowed `globe.airplanes.live/?icao=` click-through URL.
These three doc spots pre-date this commit and were not previously
recorded in the vault (searched `airplanes.live`, `README AND airplanes`).

---

# Pass 2 — post-commit e7bb241 (docs parity pass), ~09:50–10:05 EDT

Scoped to commit `e7bb241` ("Docs parity pass: reconcile living reference
docs against a fresh codebase audit" — README + 21 docs/ + the new
1,122-line CODEBASE_REFERENCE_DRAFT). Since the commit under check IS a
docs commit, this pass verifies (a) whether its new claims hold against
the live system and current source, (b) whether it actually closed Pass
1's findings, and (c) live-system health. Prior art consulted first
(second-brain: `docs parity`, `LIVE_STATE_CHECK`, `top_p`,
`rtl_sdr_adsb`; plus the in-repo 09-01/09-02 check files).

## Parity claims spot-checked against live system — all TRUE

- **LOCKDOWN corrections** (ALERT_ARCHITECTURE et al.): verified against
  `scripts/thermal-ingest-guard.py` — fallback trigger is indeed
  "DEMOTED TO INFORMATIONAL-ONLY" (script's own words), LLM units out of
  scope. Matches the docs' new text exactly.
- **llama.cpp tier claims** (README:701 report-1 on-demand): all three
  units active right now; today's 06:10 venues fire's journal shows the
  `llama-report-ondemand.sh` ExecStartPre/ExecStopPost hooks working
  exactly as README describes (including the "leaving report-1 running —
  ep-advance is activating" idle-check path).
- **Second-brain semantic layer "confirmed real and live"**
  (SECOND_BRAIN_STATUS/INFRA_MAP): `--semantic` queries run the concept
  layer live (graceful literal fallback for unknown concepts) — the
  "pending build"→"live" correction is right.
- **SDR_SERVICES additions** (acars-watcher triple-source, utm-watcher
  stub row): match the quadlet tree; no stale crash-loop-era claims.
- **DEDICATED_MODELS_PLAN superseded banner**: present and accurate.
- **runner-demo restored to docs**: container up 9h. **Amtrak
  push-primary** (DATA_SOURCES): amtrak-tracker up, unchanged since the
  08-30 verified redeploy.
- **Pass-1 findings #2 (partially), #4, and the pre-existing
  airplanes.live doc drift — CLOSED** by this commit: README:412 /
  INFRA_MAP:648 / REFERENCE_INFRA:173 "5-minute" one-liners gone;
  README:443 now correctly describes airplanes.live as purged;
  REFERENCE_INFRA's pre-purge chain description gone.

## Residual drift (real, small — the parity pass's addendum doesn't cover these)

The pass handled ALERT_REFERENCE's per-parser dedup-window framing with a
global "2026-09-03 addendum" (lines ~113–130) telling readers to
reinterpret *window* language via the periodic/forward-only split — a
legitimate fix for the window claims. But two per-parser entries are
factually inverted in ways the addendum's caveat does not reach:

- **GADV, ALERT_REFERENCE.md:319–322** — still says "on the advisory
  number (**not content hash** — … the number itself is the correct
  'have we shown this one' key, 1-hour window)". Code
  (`src/ingest/parsers/tfms_parser.py:1484–1498`): slot = advisory
  number, change-detector = content hash of
  `advisoryTitle|advisoryText`, forward-only. The doc asserts the
  opposite of the live mechanism, not merely a stale window.
- **NOTAM, ALERT_REFERENCE.md:348–350** — still says "a NOTAM
  re-transmitted unchanged won't re-fire; an amended NOTAM (**new ID**)
  will". Code (`src/ingest/parsers/aim_parser.py:285–301`): slot = NOTAM
  ID, content = classification + effective window + full text — an
  amendment under the **same** ID now re-alerts, the doc's central
  distinction.
- Lesser: APTC (:313) and TBFM (:279) still print pre-split key shapes
  (`aptc:{airport}:{rate}:{weather}`, `tbfm:{fix}:{seq_count}`) — the
  addendum's window caveat applies, but the key shapes themselves predate
  the 2026-08-16 slot/content split and the 09-02 band-bucketing.

Report-only, same rationale as Pass 1 (doc edits = another sign cycle).

## Pass-1 findings NOT closed — deliberately out of the parity pass's scope

`src/ingest/README.md` and `src/shared/watchlist_README.md` were not
touched (the commit scoped itself to README + docs/). Verified still
present: watchlist_README:190–192's headline "will not re-fire within 5
minutes" paragraph, :200–202's pre-purge airplanes.live chain described
as current; ingest README's 30-min `_TFMS_ALERT_DEDUP` / "generic
5-minute window" / REROUTE "6 h dedup" framing. All Pass-1 findings #1
and #3 carry forward unchanged — next doc-fix cycle should include these
two files explicitly, since repo-wide parity passes keep scoping to
docs/ and missing them.

## Known bug still live: ep-advance-venues — 3rd consecutive daily crash

`corporatetraveldc-ep-advance-venues.service` failed today's 06:10 fire
(exit 1, 06:11:44) on the **known** `TypeError: generate() got an
unexpected keyword argument 'top_p'` — found 09-01 (this file's sibling,
LIVE_STATE_CHECK_2026-09-01.md F1; vault `20260902T015337Z.md`), fix
deliberately deferred to an operator sign cycle that hasn't happened.
Journal confirms failures 09-01, 09-02, 09-03; the skill has **never**
produced a venue advisory. New wrinkle from this commit: the fresh
CODEBASE_REFERENCE_DRAFT (:449) and README (:701/:736) now list
`ep-advance-venues` as a live daily skill with no caveat — technically
accurate as *design* documentation (the draft was written from source,
where a call-time TypeError is invisible), but a reader would infer an
operating skill. `ep_advance_venues.py:63` (`top_p=0.9`) and `:65`
(`max_retries=0`) both still need removal + sign + poller rebuild.

## Live-state resolution not previously recorded anywhere: ADS-B dongle is BACK

CLAUDE.md's 2026-08-29/30 "REAL, hardware, NOT fixable remotely" entry
(ultrafeeder crash-looping 541+ restarts, `/dev/rtl_sdr_adsb` absent
from the USB bus) is **resolved**: the device node exists (udev symlink
`/dev/rtl_sdr_adsb -> bus/usb/003/002`, created at the 08-30 08:23
reboot — physical intervention + reboot evidently fixed it),
`corporatetraveldc-ultrafeeder` is stably up 9h, and a live poll of
`100.x.x.x:8080/data/aircraft.json` returns 15 aircraft with a
current timestamp. Searched the vault (`rtl_sdr_adsb`) and the 09-01/
09-02 check files — nowhere recorded; persisted to the vault this pass.

## Live-system health

- Failed user units: exactly 1 (`ep-advance-venues`, the known bug
  above). Everything else clean — including all units CLAUDE.md's
  09-02 entries predicted would self-resolve.
- All platform containers up; poller image build-date `20260903T043438Z`
  (00:34 EDT, pre-dating the 09:46 EDT parity commit — fine: the commit
  touched no `src/`, so `verified-exec` is unaffected and no rebuild is
  owed for it). Uncommitted
  working-tree state: only `docs/CLAUDE_MD_DRIFT_REPORT.md` (the
  auto-regeneration the commit message deliberately excluded) plus this
  file's Pass-2 edit.

---

# Pass 3 — post-commit 6b5d46c (client-demo template), ~11:20–11:30 EDT

Scoped to commit `6b5d46c` ("Generalize the client-demo preview pattern
into a reusable template"). Prior art consulted first (second-brain:
`client-demo`, `ccw-demo`, `webdev`, `--raw 'client AND demo AND
preview'`): vault note `20260824T011337Z.md` (the 08-24 full CLAUDE.md
dump) is the canonical prior investigation of this exact area — it
documents the original `ccw-demo` as deliberately untracked, its Quadlet
comment's false Cloudflare-Tunnel claim, and an open "NEEDS OPERATOR
DECISION: track it or record deliberately as out-of-scope." This commit
is the direct follow-on: the *pattern* is now tracked for all future
demos, and the original instance's untracked status is now explicitly
recorded as deliberate ("stays running exactly as it was, not migrated")
in the commit message, `docs/CLIENT_DEMO_PATTERN.md`, and the template's
own header — the 08-24 open decision is effectively answered by
direction rather than by migration.

## Commit claims verified against the live system — all TRUE

- The three unit files (`client-demo@.container`,
  `client-demo-webdev-expiry@.service`/`@.timer`) are installed live
  under `~/.config/` and byte-identical to the tracked copies (diff'd).
- The `zzz-validation-test` instance is fully gone: no instance symlink,
  no `.container.d/` drop-in dir, no `~/demos/zzz-*` dir, no timer
  instance in `list-timers --all` or `list-units --all`.
- Original `ccw-demo` genuinely untouched: Up 4 days, still publishing
  `127.0.0.1:8085` + `100.x.x.x:8085`, Basic Auth live (curl → 401).
- `docs/CLIENT_DEMO_PATTERN.md`'s functional claims all match the real
  files: no `PublishPort=` in the base template ✓; scaffold writes the
  symlink + `10-instance.conf` drop-in ✓; enables-but-does-not-start the
  expiry timer ✓ (see finding below for the caveat); doesn't create
  `.htpasswd`/site content/Tunnel route ✓; nginx template's
  `/.well-known/` allow + cache headers ✓; removal steps match the
  artifact set the scaffold creates ✓.

## Existing docs checked for invalidation — no drift

- `README.md:32` and `docs/INFRA_MAP.md` (§4 62-vs-63 gap analysis,
  §11 item 11 live-only-untracked list, the misc-container list at
  ~:451): every "ccw-demo is untracked" claim is still true — the commit
  deliberately did not migrate or track it. The Quadlet counts those
  passages cite move in lockstep (+1 repo, +1 live from the template),
  so the "exactly one extra live `.container`" structure still holds;
  README explicitly says to re-run the commands rather than trust the
  numbers anyway.
- No doc index references `docs/CLIENT_DEMO_PATTERN.md` (grep repo-wide:
  zero mentions outside the file itself) — but docs/ has no master index
  file, so there is no index to have drifted. Nothing in README, docs/,
  src/ingest/README.md, or src/shared/watchlist_README.md makes any
  claim this commit invalidates.

## Real finding — the new expiry timer's 7-day guarantee does not survive reboots

The one non-trivial issue, in the commit's own new code/doc rather than
in pre-existing docs (persisted to the vault this pass):

`corporatetraveldc-client-demo-webdev-expiry@.timer` uses
`OnActiveSec=7d` + `Persistent=true`. Two problems, verified against
this box's systemd.timer(5) man page:

1. **`Persistent=true` is a no-op here** — the man page is explicit that
   `Persistent=` "only has an effect on timers configured with
   `OnCalendar=`". It was carried over from the original one-off's
   `OnCalendar=`-based timer, where it was load-bearing.
2. **The 7-day countdown restarts from zero on every reboot.**
   `OnActiveSec=` is monotonic, relative to the moment the timer unit is
   activated — and because the scaffold `enable`s the timer
   (`WantedBy=timers.target`), every boot re-activates it and resets the
   clock. A box that reboots more often than every 7 days **never fires
   the expiry**, so the time-limited `webdev` credential on a
   Cloudflare-Tunnel-exposed preview lives indefinitely. This is not
   theoretical cadence: this box rebooted 2026-08-23 and 2026-08-30
   (~weekly, right at the boundary). The failure direction is open
   (credential persists), on an auth-limiting control. The original
   one-off's absolute `OnCalendar=` date + `Persistent=true` had exactly
   the right semantics (survives reboots, catches up if missed); the
   generalization traded that away for parameterlessness.
   - Lesser corollary: because the timer is *enabled*, after the next
     reboot it auto-activates even for an instance the operator never
     manually started (scaffold step 5 skipped) — harmless in effect
     (`client-demo-webdev-expire.sh` no-ops cleanly when the credential
     or `.htpasswd` is absent), but it makes the "enables (does not
     start)" doc claim true only until the next boot.
   - Fix direction (report-only; a fix is a new sign cycle): have
     `new-client-demo.sh` render a per-instance timer drop-in with an
     absolute `OnCalendar=` (now + 7d) — restores the original's correct
     reboot-proof semantics while keeping the template generic. Cosmetic
     nit in the same file: the `systemctl --user enable ... || true`
     swallows enable failures, so scaffolding reports success even if
     the timer never got enabled.

## Observed in passing — active deploy in flight (NOT this commit's scope)

During this pass a second commit landed (`2dddf7d`, 11:22 EDT,
"/board/refresh token rotation + manual-notes vault scope") and its
deploy cycle was mid-flight: `poller`/`pusher` deliberately stopped
11:16–11:17 (the known clean-stop-then-SIGKILL-under-load pattern from
CLAUDE.md's 09-02 entries — both landed in `failed`, exit 137),
`ingest-core` stopped (`inactive`), and the earlier
`integrity-sweep`/daily-watch failures were the usual `verify-manifest`
refusals against `src/common/db.py`/`src/web/main.py` while those edits
awaited the 11:22 signing. As of 11:24 no image rebuild had started yet
(all images still 00:36 EDT builds). **Deliberately not touched** — the
committing session owns that cycle; the failed units are its to restart
post-rebuild. If `poller`/`pusher`/`ingest-core` are still down well
after that deploy completes, that's the known needs-manual-`reset-failed`+
`start` pattern, not a new bug. (`ep-advance-venues` remains failed from
its known, separate `top_p` TypeError — see Pass 2.)

---

# Pass 4 — post-commit 2dddf7d (/board/refresh grace relay + manual-notes vault scope), ~11:23–11:35 EDT

Dedicated drift check for `2dddf7d` ("Harden /board/refresh token
rotation + open manual-notes vault scope to Cowork"): new
`board_refresh_grace` table + 120s grace-relay in
`board_refresh_token()` (`src/common/db.py`), `relayed: true/false` in
the `/api/v1/board/refresh` response, and `01-Sources/manual/` added to
`_VAULT_RESEARCH_EXTRA_PREFIXES` (`src/web/main.py`).

Prior art consulted first (second-brain: `board-refresh`, `--raw 'board
AND refresh AND token'`, `_VAULT_RESEARCH_EXTRA_PREFIXES`,
`vault-research`, `--raw 'manual AND vetted'`): vault note
`corporatetraveldc/01-Sources/manual/20260902T135256Z.md` (yesterday's
ebb5b7c check) confirmed the COMPLIANCE_SECURITY board_refresh section
accurate as of 2026-09-02 — this commit is what invalidates part of it
(below). Its finding 3 (the 7d presence reminder fires ~10h after the
2026-09-09 attestation expiry) is NOT affected by this commit: the grace
relay only re-serves an already-minted token for 120s; a fresh rotation
still 403s on stale presence exactly as before.

## Drift found (real, both fixed in place this pass)

1. **docs/COMPLIANCE_SECURITY.md:280** claimed `board_refresh_token()`
   has "three call sites as of 2026-08-23" for the `board_refresh`
   audit action. The grace-relay path added a fourth
   (`src/common/db.py:637`, `"relayed": True`). Corrected to four /
   dated 2026-09-03.
2. **docs/CODEBASE_REFERENCE_DRAFT_2026-09-03.md** — committed only ~2h
   before this commit in e7bb241 — drifted the same morning: "63
   `CREATE TABLE` statements in db.py ≈ 75 tables" is now 64/≈76, and
   its board-table inventory (`board_messages`, `board_enroll_nonces`,
   `board_tokens`, `board_presence`) was missing `board_refresh_grace`.
   Both corrected in place.

## Checked, NOT invalidated

- README.md, src/ingest/README.md, src/shared/watchlist_README.md,
  CLAUDE.md: no claims about board auth, token rotation, or the
  vault-research prefix scope (grep-verified) — unaffected.
- docs/REFERENCE_INFRA.md:182 ("only a SHA-256 hash is stored
  server-side") is about `ctdc_` bearer tokens — untouched and still
  true. `board_tokens` itself also still stores only hashes; the
  plaintext lives solely in the new grace table for ≤120s, exactly as
  the commit message frames it.
- Dated point-in-time records left unedited per convention, but two are
  now consciously superseded rather than silently wrong:
  - `docs/investor-materials/v1.5/research/PENTEST_2026-08-24.md`
    recommended keeping `01-Sources/{daily,manual,rss}` out of the
    research-read scope. `manual/` is now deliberately in scope by
    operator directive (the commit's stated rationale: the 2026-08-24
    X-Board-Key gate on the whole endpoint removed the
    "unauthenticated" premise of the original 2026-08-16 exclusion).
    `daily/`, `rss/`, `transport-patterns/`, `06-AI-Memory/` stay
    excluded.
  - `due-diligence-faq.md`'s "the token table has no plaintext column
    (hashes only)" stays literally true, but the next investor-materials
    reverification pass should disclose the bounded exception: a
    just-minted board token's plaintext now sits at rest in
    `board_refresh_grace` for up to 120s per rotation.
- Nuance noted, not drift: the grace lookup runs BEFORE both the
  validity and presence-attestation checks, so a relay retry inside the
  120s window skips the presence gate. Only reachable within 120s of a
  rotation that itself passed that gate, so exposure is bounded by
  design; recording it so nobody rediscovers it as a "bypass."

## Live verification

- `verify-manifest: OK — 887 files` at 11:26 (the 11:22 signing is
  complete and clean). This pass's own doc edits re-open the usual
  unsigned-docs window until the next signing — expected pattern.
- **The commit is NOT deployed.** All platform images still carry
  build-date 20260903T043438Z (00:35–00:38 EDT, pre-commit); the
  running `corporatetraveldc-web` container's source has zero
  occurrences of `board_refresh_grace` or `01-Sources/manual/`.
  Matches the commit's own closing note ("had not shipped yet",
  board reply brd-a9abbd70f8a2). Until web is rebuilt+redeployed, a
  dropped refresh response still strands the session and Cowork still
  gets 400 on `01-Sources/manual/` reads. **Rebuild+redeploy of `web`
  (and the usual poller/pusher/ingest rebuild for the shared
  `common/db.py`) is the outstanding action.**
- Live DB: `board_refresh_grace` already exists (created by the
  pre-commit validation) with **0 rows** — the commit's "test token
  and grace-cache row removed" cleanup claim verified true.
- **Deploy cycle judged abandoned; services restarted by this pass.**
  Pass 3 (11:24) deferred to the committing session, but by 11:27 no
  rebuild had started and the stop set turned out to be wider than
  Pass 3 saw: not just poller/pusher/ingest-core but ALL SEVEN ingest
  units (core + fdps/itws/notam/stdds/tbfm/tfms) were down — a total
  SWIM/NWWS/Amtrak feed outage on a forward-only pipeline with no
  backfill. This pass restarted poller + pusher (`reset-failed` +
  `start`, the documented remediation for the clean-stop-SIGKILL
  pattern) and started all 7 ingest units; **all 9 confirmed active by
  ~11:30**, on the existing internally-consistent 00:35 images (no
  integrity refusals). Net unrecoverable feed gap: ~11:16–11:29 EDT
  (~13 min). If/when the pending rebuild happens these simply restart
  again onto the new image.
- Remaining failed units, all classified, none from this commit, left
  in `failed` deliberately (the daily runs genuinely didn't produce
  output; clearing the flag would hide that from the operator sweep):
  `integrity-sweep` (11:12 pre-signing refusal — will next fail on this
  pass's doc edits instead, same expected pattern),
  `executive-protection-daily-watch` / `trains-yachts-daily-watch` /
  `second-brain-daily` (report-tier LLM stall under this session's
  sustained load — the known llama-report-1 degradation, tracebacks
  after "Ollama unavailable" fallback-disabled returns, ~05:00–08:00),
  and `ep-advance-venues` (4th consecutive daily `top_p` TypeError,
  known since Pass 2 / 09-01 — still unfixed, still zero successful
  runs ever; every hourly EP brief continues to ship the "Not yet
  generated" venue placeholder).

Working-tree note for the next signing pass: besides this pass's three
doc edits, `docs/CLAUDE_MD_DRIFT_REPORT.md` carries a pre-existing
1-line modification (its generated-at timestamp, rewritten 05:15 EDT by
`corporatetraveldc-claude-md-drift-daily`) — automated, not from this
pass and not from 2dddf7d.

Real findings persisted to the vault this pass (deploy-gap/feed-outage
+ the two doc corrections): `01-Sources/manual/20260903T153246Z.md`.
The top_p bug was NOT re-persisted (already in the vault from the
09-01/Pass-2 checks).

---

# Pass 5 — post-commit 422bd15 (GPG keys /keys/ + weekly external-image
# timer + delta docs), ~19:12–19:30 EDT

Scoped to commit `422bd15` ("Publish GPG keys via blog /keys/ + weekly
external-image-update timer; delta docs pass", 19:12 EDT). Prior art
consulted first (second-brain: `weekly-external-image-update`, `GPG`,
`executive-standard`): vault note
`corporatetraveldc/01-Sources/manual/20260903T214620Z.md` (tonight's
session reconciliation, written 17:46 EDT by the committing session
itself) is the canonical record of the work behind this commit — the
client-demo pattern, board grace-relay, GPG publishing, the consolidated
Cloudflare token, and the weekly-update standing rule. This pass builds
on it rather than re-deriving; it also turned out to be the tiebreaker
for the first drift finding below.

## Commit claims verified against the live system — all TRUE

- **Weekly external-image timer**: installed, `enabled`, `active`, next
  fire Sun 2026-09-06 04:15 EDT; tracked copies
  (`.config/systemd/user/…`) byte-identical to the installed ones (the
  research-board-mirror never-installed / wrong-Exec-path bug class does
  NOT recur here — ExecStart path exists and is executable).
  `Persistent=true` is correctly paired with `OnCalendar=` (with an
  explicit `America/New_York`), unlike the ep-advance-venues timer nit —
  right semantics this time.
- **"~11 external/registry-policy containers"**: exactly 11 live
  containers carry `io.containers.autoupdate=registry` right now.
- **/keys/ live on the blog**: `executivestandard…/keys/developer.pub`
  → 200, sha256 identical to the repo copy. `build_site()`'s keys loop
  verified in source (`executive_standard_sync.py:288–290`); confirmed
  it writes in place without wiping `site_dir`, so a sync from a
  pre-commit poller image cannot drop the already-published keys.
- **All 5 fingerprints** in `docs/GPG_KEYS_PUBLISHED.md` match
  `gpg --show-keys` on the actual tracked `.pub` files.
- **Manifest**: `verify-manifest: OK — 896 files`, matching the commit
  message. `db.py` spot-checks hold: 64 `CREATE TABLE`, four
  `board_refresh` `audit()` call sites (`db.py:637/:645/:650/:680`).
- README's new external-vs-own-images paragraph and INFRA_MAP's timer
  entry match reality; INFRA_MAP deliberately keeps no timer total
  (defers to `list-timers`), so no count drifted.

## Drift found — 2 factual errors in the new doc itself (both FIXED in place)

Both in `docs/GPG_KEYS_PUBLISHED.md`, born stale/wrong at commit time:

1. **Main site claimed "staged pending `./deploy.sh`" — it was already
   live.** `www.example.com/keys/developer.pub` → 200,
   byte-identical to the repo copy (and to the blog's). The vault note
   written 1.5h before the commit already said "confirmed live after
   the deploy-pipeline fix" — the doc contradicted the committing
   session's own earlier finding.
2. **`developer-legacy.pub` "created 2026-07-03, five days before
   `developer.pub`'s 2026-07-08" — contradicted by the key material.**
   The `.pub` itself says created 2025-10-25 (epoch 1761378009; same
   day as `operator_sheldon`/`operatorwsheldon`). `developer.pub`'s 2026-07-08
   is correct; the legacy key's date and the "five days before"
   narrative are not.

Fixed in place (uncommitted, per this pass's no-commit rule); these
edits re-open the usual unsigned-docs integrity window until the next
signing — expected pattern, same as Pass 4.

## Second abandoned deploy cycle today — full feed pipeline down 41 min (restarted by this pass)

Same shape as Pass 4's finding, second occurrence in one day:
`poller`/`pusher`/`runner` were deliberately stopped 18:36–18:37 EDT
(each hit the known clean-stop-then-SIGKILL-under-load pattern, exit
137 → `failed`) and ALL SEVEN ingest units were left `inactive` — then
the committing session signed, committed at 19:12, and **exited without
rebuilding or restarting anything** (verified: no build process, no
newer images than 15:32 UTC, and the only live claude process on the
box was this drift-check session). Total SWIM/NWWS feed outage
18:37→19:18, ~41 min, forward-only pipeline, no backfill. This pass
applied the documented remediation (`reset-failed` + `start`): all 10
units confirmed `active` by 19:18 on the existing internally-consistent
15:32 UTC images. `web` was never down (up 3h, carries the 2dddf7d
grace-relay fix).

**Still owed: one poller image rebuild** for this commit's
`executive_standard_sync.py` change (the only `src/` file it touched).
Not urgent — the 15:32 image is internally consistent (no verified-exec
refusals), the blog's /keys/ is already live, and stale-image syncs
can't remove it (see above) — but until the rebuild, the poller's
executive-standard-sync skill runs pre-/keys/ code. Standing rule says
rebuild after every major code change; the committing session didn't.

## Checked, NOT invalidated

README.md (its only GPG claim, :62 "public releases are GPG signed", is
untouched), CLAUDE.md, docs/COMPLIANCE_SECURITY.md +
docs/CODEBASE_REFERENCE_DRAFT (both updated by the commit itself,
claims verified above), src/ingest/README.md,
src/shared/watchlist_README.md (no claims about keys, timers, or
external images — grep-verified). The Pass-1/-2 residual dedup-doc
drift in the two src/ READMEs carries forward unchanged (this commit
didn't touch them; still waiting on the next doc-fix cycle).

## Live-system health at close

Failed units after this pass's restarts: `website-integrity-sweep`
(KNOWN, per CLAUDE.md — re-fails each fire until the operator's
human-only re-sign of the website repo; don't re-diagnose) and
`executive-protection-daily-watch` (left `failed` deliberately by
Pass 4's rationale — though note its container was seen freshly
restarting at 19:15, so it may clear itself on this run;
`ep-advance-venues`'s `top_p` fix remains undone). Working tree at
close: this file, `docs/GPG_KEYS_PUBLISHED.md` (the two corrections),
and the automated `docs/CLAUDE_MD_DRIFT_REPORT.md` timestamp rewrite —
all uncommitted, nothing staged.

Real findings persisted to the vault this pass (abandoned deploy #2 /
41-min feed outage + the two GPG-doc corrections):
`corporatetraveldc/01-Sources/manual/20260903T231926Z.md`.

---

# Pass 6 — post-commit b623db9 (docs drift corrections + drift-report
# regen), ~19:45–20:10 EDT

Scoped to `b623db9` (19:45 EDT — Pass 2–5's own corrections committed).
Prior art consulted first (second-brain: `drift-report`,
`GPG_KEYS_PUBLISHED`, `--raw 'manifest AND re-signed'`): Pass 5's vault
note `20260903T231926Z.md`, plus `20260830T131615Z.md` — the prior
instance of the signed-against-stale-content manifest class that commit
`54efc2a` existed to fix. That class recurred tonight (finding 1).

## Finding 1 (REAL): b623db9 does NOT contain the re-sign its own
## message claims — the committed tree fails verify-manifest

- The commit message says "MANIFEST re-signed against current tree (896
  files)", but the commit's stat is 3 docs files only — `git diff
  422bd15..HEAD -- MANIFEST.sha256 MANIFEST.sha256.asc` is empty. HEAD
  carries 422bd15's manifest unchanged (signed 17:54:41 EDT), which
  records the PRE-correction hash (`2a5ee968…`) for
  `docs/GPG_KEYS_PUBLISHED.md`.
- Net effect: a clean checkout of HEAD fails `sha256sum -c` on exactly
  the file the commit fixed (verified: 1 mismatch, signature itself
  Good — the pair is internally consistent, just stale vs. the tree).
  Any consumer of the committed tree — public-mirror push, audit
  checkout — sees an integrity failure.
- The REAL 19:40–19:41 EDT re-sign (correct hash `a96b5a0c…`, Good
  signature) exists only as the uncommitted working-tree
  `MANIFEST.sha256`/`.asc` this pass found on entry. The live box
  therefore verifies clean (`OK — 896 files`), and the 19:43–19:46
  image rebuilds baked the good manifest — zero verified-exec
  exposure. Purely a committed-state problem.
- Likely mechanics: selective `git add` of the three docs files at
  19:45 that forgot the two manifest files the 19:41 re-sign had just
  rewritten. Second occurrence of the `54efc2a` bug class in four
  commits.
- Fix owed (report-only — this pass is barred from committing): the
  next commit must include the working-tree MANIFEST pair, or the next
  signing (owed anyway for this pass's edits) supersedes it. Also
  noted: `docs/LIVE_STATE_CHECK_2026-09-03.md` appears in NO manifest
  yet (untracked at both signings; sign-manifest covers tracked files)
  — the next sign picks it up now that b623db9 tracked it.

## Finding 2 (REAL, new failure class): never-expiring Nextcloud Text
## locks blocked all vault synthesis writes from 12:17 — four daily
## watches lost today's output

- `concierge-travel`/`executive-protection`/`gig-economy`/
  `trains-yachts-daily-watch` all `failed` between 19:18–19:39 EDT:
  each ran its full watch (24–56 min wall) and died at the final
  WebDAV PUT with `423 Client Error: Locked` on its own
  `04-Syntheses/daily/*-2026-09-03.md`. Journal shows 423s from 12:17
  EDT onward, 10–12 attempts per file across the afternoon (aviation's
  file included).
- Root cause (Nextcloud Postgres, read-only queries): 9 rows in
  `oc_files_lock` — owner `Text` (the collaborative-editor app),
  created sequentially 03:20–03:29 EDT (pattern of each file being
  opened in the Nextcloud web editor in one sitting), `ttl = -60` —
  negative, never honored as expired, and never cleaned despite
  background cron being verifiably healthy. The transactional
  `oc_file_locks` table is empty; this is purely the files_lock app
  layer. Prime suspect for the broken TTL: the unplanned Nextcloud
  33→34 major jump ~00:45 this morning (per 422bd15's commit message)
  — plausible, unproven.
- Locked files: today's five daily watch notes, THREE weekly W35 files
  (`04-Syntheses/weekly/2026-W35.md`, `dispatch-desk-2026-W35.md`,
  `aam-watch-2026-W35.md`), and one personal-voice-profile note. So
  beyond today's lost dailies, the weekend's weekly writers
  (`dispatch-desk-memo`, `second-brain-weekly`, aam weekly, ~2026-09-06)
  WILL hit the same 423 unless the locks are cleared first.
- Tomorrow's dailies are NOT blocked (new dated filenames).
  `aviation-daily-watch`, re-running during this pass, will fail again
  at write time — same lock, expected.
- Deliberately NOT remediated here: clearing means deleting rows from
  the live Nextcloud DB (or unlocking via the files_lock app) —
  destructive against state possibly owned by still-open operator
  editor tabs. Operator action: close any open Text tabs on those
  files, delete the 9 `oc_files_lock` rows (ids 1–9 as of this pass),
  then re-run the four watches to recover today's output. The four
  units were left `failed` deliberately (they genuinely produced
  nothing).
- Disclosure: while diagnosing, this pass meant to LIST background-job
  mode but ran `occ background:cron`, which SETS it. Verified a no-op:
  25 background jobs had already run earlier today, i.e. cron mode was
  already the active mode before the command.

## Finding 3: CLAUDE.md's website-integrity-sweep entry is RESOLVED

The operator ran the human-only re-sign in the website repo at 19:11
EDT (`ab4c97c` "chore: re-sign manifest after csexec-pages deploy
fixes") and the 19:36 sweep fire passed clean ("sweep OK … all 64
files match"; unit now `inactive`, not `failed`). CLAUDE.md's "WILL
re-fail on each sweep fire" prediction no longer holds — annotated
RESOLVED in place this pass.

## Checked, NOT invalidated / carried forward

- b623db9's committed `GPG_KEYS_PUBLISHED.md` corrections are intact
  (byte-identical to Pass 5's fix); the CLAUDE_MD_DRIFT_REPORT regen
  is the benign timestamp rewrite the message claims.
- The commit message's follow-ups list (expiry-timer reboot hole,
  ALERT_REFERENCE GADV/NOTAM inversions, abandoned-deploy pattern) —
  accurate; all carried forward, none re-derived here.
- Feed pipeline: Pass 5's 19:18 restarts are holding — all 9
  poller/pusher/ingest units + web active; no third abandoned stop
  today.
- Pass 5's "owed poller rebuild": DONE — poller/pusher/ingest/web
  images all rebuilt 19:43–19:46 EDT (post-commit, good manifest
  baked) by the still-live operator session. The running containers
  (started 19:18) predate those images; restarting onto them belongs
  to that session's deploy cycle — deliberately not touched. If
  they're still on the old (internally consistent) images much later,
  that's the known finish-the-deploy pattern, not a new bug.
- `ep-advance-venues`: no longer `failed` (now `inactive` — presumably
  reset during the rebuild cycle), but the `top_p`/`max_retries` bug
  is STILL in source (`ep_advance_venues.py:63`/`:65`) — expect the
  5th consecutive failure at tomorrow's 06:10 fire unless fixed in the
  interim.

Working tree at close: this file (Pass 6 append), CLAUDE.md (one
RESOLVED annotation), plus the pre-existing uncommitted MANIFEST pair
from the 19:41 re-sign (finding 1 — deliberately left for the next
signing/commit to pick up). Nothing staged, nothing committed by this
pass. Separately, observed at close (~20:03 EDT): the live operator
session had concurrently STAGED edits to `docs/ALERT_REFERENCE.md`,
`src/ingest/README.md`, `src/shared/watchlist_README.md`, and
`scripts/new-client-demo.sh` — i.e. the long-carried Pass 1/2/3
findings (dedup inversions, the two src READMEs, the expiry-timer
reboot hole) appear to be being fixed in-flight by that session; not
this pass's work, deliberately untouched.

Real findings persisted to the vault this pass (stale committed
manifest + Nextcloud Text-lock write outage + sweep resolution):
`corporatetraveldc/01-Sources/manual/20260904T000100Z.md`.
