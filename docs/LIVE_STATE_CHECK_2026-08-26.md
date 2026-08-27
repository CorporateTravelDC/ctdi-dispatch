# Live-state / doc-drift check — 2026-08-26 (~00:00–00:15 EDT), post-commit 22a66a6

Scope: commit `22a66a6` ("Allowlist 2 more illustrative test values quoted
in LIVE_STATE_CHECK doc prose" — `scripts/scrub-public-tree.py` +2
`ALLOWED_IPV4` entries, manifest re-sign). Tenth pass in this series;
second-brain search ran first, per convention. Prior lineage: pass 8
(`521ba5b`, vault note `01-Sources/manual/20260826T032252Z.md`) and pass 9
(`7423835`, vault note `20260826T040047Z.md`, whose report addendum is the
still-uncommitted edit to `docs/LIVE_STATE_CHECK_2026-08-25.md`). No prior
vault note covers `22a66a6`; this pass builds on 8/9 rather than
re-deriving them. Read-only except this file and one second-brain note.
Nothing staged, committed, or signed by this pass.

**Important context for reading this file:** another session was actively
editing the working tree while this check ran (details in finding 2 —
that's a feature of tonight, not an error in either pass). Every claim
below carries its observation time; re-derive anything time-sensitive
rather than trusting this snapshot.

## The commit itself: verified, achieves its stated purpose, invalidates no doc

- Ran the scrubber end-to-end against HEAD live (`scrub-public-tree.py
  HEAD`, 13.6s): `verify_scrubbed()` passes clean, scrubbed tree
  `2d741d74`. So the two new allowlist entries (`2.49.0.1`, `10.x.x.x`)
  do exactly what the commit message claims for the committed tree.
- The allowlist comment's claim that both values were "since renamed in
  the actual test files" is true — a repo-wide grep finds them only in
  the scrubber itself, the 08-25 LIVE_STATE_CHECK prose, and CLAUDE.md's
  checkpoint-3 notes (the latter two both ship in the public tree, both
  now covered by the same entries).
- No doc anywhere documents `ALLOWED_IPV4`'s contents, so an additive
  allowlist change invalidates nothing. README.md, docs/*,
  `src/ingest/README.md`, `src/shared/watchlist_README.md`: no claims
  touched by this commit's scope. Pass 9's demo-status drift wall is
  unchanged and not re-derived here.

## Finding 1 (REAL, forward-looking, empirically confirmed): the next commit of pass 9's addendum pre-stages a public-push block

The uncommitted pass-9 addendum quotes its own spoofed-CF probe value
`203.0.113[.]9` (defanged here deliberately — writing the contiguous
dotted-quad in this file would add a second instance of the exact
problem). That value is NOT in `ALLOWED_IPV4`; only `203.0.113.50` is,
and `verify_scrubbed()`'s IPv4 check is exact-match (scrub-public-tree.py
line ~757), no TEST-NET range exemption. Git-tree-based scrubbing can't
see uncommitted files, which is why tonight's HEAD run passes — so this
lands the moment the addendum is committed.

Confirmed empirically, not just by code-reading: built a temporary-index
tree (HEAD + the addendum, real index/refs untouched, dangling objects
only) and ran the scrubber against it → `VERIFICATION FAILED`, exactly
one violation, that value in `docs/LIVE_STATE_CHECK_2026-08-25.md`. A
safe failure (push blocked, nothing leaks — the value is RFC 5737
TEST-NET-3, documentation-only, not sensitive), but it would stall the
next `push-public.sh` run, plausibly mid-investor-docs push.

Fix is one line: allowlist it (same rationale as the existing
`203.0.113.50` entry) in whichever signed commit picks up the addendum.
This is the third occurrence of the "doc prose quoting probe/fixture
values trips the scrubber later" class (checkpoint-3 fixtures →
`22a66a6`'s two values → this). Standing suggestion for future passes:
quote already-allowlisted illustrative values (e.g. `203.0.113.50`) or
defang, rather than minting new allowlist entries per pass. Flag-only,
not edited by this pass; persisted to second-brain.

## Finding 2 (REAL regression, observed being fixed concurrently — snapshot only): checkpoint 1's C-0c fix self-blocked push-public.sh

Observed mid-check, timestamped: at ~00:03 EDT `scripts/push-public.sh`
gained an uncommitted edit; by ~00:10 it and `scripts/pre-push` were
**staged** (another session's work, left strictly untouched).

What the staged diff shows: the C-0c fix (`ef6b798`, checkpoint 1) made
`pre-push` block pushes whose URL argument matches the public remote's
resolved URL — but `push-public.sh`'s own sanctioned push is exactly that
shape by design (it force-pushes the already-scrubbed commit via the raw
resolved URL, never the named remote). So since checkpoint 1, the ONE
legitimate public-push path was self-blocking. The concurrent fix adds a
`CTDI_PUSH_PUBLIC_INTERNAL=1` escape hatch set only at that call site;
the staged `pre-push` gates only the public-destination intercept on it,
not the credential-pattern scanner — the hatch is properly narrow.

Doc-drift angle (the part that belongs to this pass): CLAUDE.md's
checkpoint-1 C-0c entry claims "Verified by direct hook invocation:
raw-URL push now blocked, normal `origin` push unaffected." Both halves
were true, but the verification missed the third case — the sanctioned
scrubbed push is itself a raw-URL push. Worth remembering as a test-gap
pattern: when adding a bypass-blocker, verify the legitimate path that
shares the bypass's shape. Persisted to second-brain (attributed as
observed-in-flight, so a future pass doesn't double-fix or contradict
the concurrent session's own writeup).

## Finding 3 (REAL, closes a standing item): docs-drift-weekly root-caused — 0-for-2 on scheduled fires

CLAUDE.md's known-bad list has carried `corporatetraveldc-docs-drift-weekly`
as "failed, not re-investigated" since 08-25; passes 8/9 deferred it.
Checked its journal and its own logs (`/var/lib/corporatetraveldc/docs-drift-check/`):

- **2026-08-17 09:00 (scheduled): failed** — `nohup: failed to run
  command 'claude': No such file or directory` (PATH gap in the unit;
  per the 08-19 log this was subsequently fixed).
- **2026-08-19 09:49 (manual start): succeeded** — full drift report
  produced; confirms the PATH fix works.
- **2026-08-24 09:00 (scheduled): failed** — log contains exactly
  "You've hit your session limit · resets 10:40am (America/New_York)".
  The headless `claude` run fired while the operator's Claude usage
  window was exhausted; exit 1, no retry.

So the timer-triggered run has never once succeeded; the only clean run
was manual. The 08-24 failure mode is environmental and will recur
whenever a Sunday-09:00 fire coincides with an exhausted usage window —
and it applies equally to the brand-new
`corporatetraveldc-second-brain-weekly-dump.timer` (Sun 02:00, same
headless-claude pattern, first fire 2026-08-30). Options for the
operator (not implemented here): retry-with-delay in the scripts (e.g.
re-fire after the stated reset time), or `OnFailure=` notification so a
silent Sunday failure pings ops instead of sitting in `--failed` for two
days. Persisted to second-brain.

## Checked, not drift (explanations worth keeping)

- **`verify-manifest.sh` passing at 00:02 despite the uncommitted pass-9
  addendum is by design**, not an anomaly: `sign-manifest.sh` deliberately
  excludes `docs/LIVE_STATE_CHECK_*.md` from the manifest (2026-08-18
  decision, documented in the script ~line 110) precisely so drift-pass
  addenda don't break integrity between signs. (Distinct from CLAUDE.md,
  which IS in the manifest — that's the known chicken-and-egg.)
- **`integrity-sweep` failed at 00:04:11** — NOT the pre-sign-window
  failure pass 9 described (that was 23:49:11, and the 22a66a6 sign did
  clear it: my 00:02 verify was rc 0, all 786 files matched). The 00:04
  failure's cause is finding 2's concurrent `push-public.sh` edit
  (mtime 00:03), caught by the very next sweep fire — the sweep working
  exactly as intended on not-yet-signed work. Self-clears after the
  concurrent session's next sign.
- **Failed units, full snapshot at 00:00**: only `integrity-sweep`
  (above) and `docs-drift-weekly` (finding 3). Every
  LOCKDOWN/rebuild-window casualty from CLAUDE.md's known-bad list
  (pusher, poller, board-sweep, ops-brief, etc.) has self-cleared as
  predicted.

## Footprint

This file and one second-brain note. Nothing staged, committed, signed,
or deployed; the concurrent session's staged `pre-push`/`push-public.sh`
work and modified manifest were left untouched; temp-index scrub test
used a throwaway `GIT_INDEX_FILE` and produced dangling objects only.

---

# Addendum: pass 11 — post-commit `c7c0f8e` (2026-08-26 ~00:39–00:55 EDT)

Scope: commit `c7c0f8e` ("Fix push-public.sh self-block from C-0c
pre-push URL check; sync quadlet tracked mirrors; add drift-check 11 +
weekly-timer OnFailure notify infra"). Second-brain search ran first:
pass 10's note (`01-Sources/manual/20260826T040816Z.md`, the section
above) is the direct predecessor — this commit is essentially the
remediation of pass 10's findings 1–3, so this pass verifies that
remediation live rather than re-deriving it. Read-only except this file
and one second-brain note; nothing staged, committed, or signed.

## The commit's own claims: all verified live

- **Self-block fix (pass-10 finding 2, closed):** installed
  `.git/hooks/pre-push` is byte-identical to tracked `scripts/pre-push`;
  the `CTDI_PUSH_PUBLIC_INTERNAL` marker is set at exactly one call site
  (`push-public.sh` line 78, immediately before its own push) and gates
  only the public-destination intercept, not the credential scanner —
  narrow as claimed.
- **Allowlist entry (pass-10 finding 1, closed):** the TEST-NET-3 probe
  value is in `ALLOWED_IPV4` (scrub-public-tree.py line 488), and a full
  `scrub-public-tree.py HEAD` run completed with `verify_scrubbed()`
  passing (scrubbed tree `676384cc` printed — the script exits 1 before
  printing on any violation). The predicted next-push block is gone.
- **Quadlet/unit mirror sync:** tracked
  `runner-demo`/`demo-api.container` and all three systemd unit files
  (`docs-drift-weekly`, `second-brain-weekly-dump`,
  `unit-failure-notify@`) are byte-identical to their live installed
  copies. `check-claude-md-drift.sh` (now including checks 10–11) passes
  end-to-end: "CLAUDE.md matches live state."
- **OnFailure wiring (pass-10 finding 3's recommendation, implemented):**
  both weekly units show
  `OnFailure=corporatetraveldc-unit-failure-notify@….service` live, the
  template instances load cleanly, and the notify script reads
  `NTFY_OPS_TOPIC` (ops-health). Timers confirm next fires: weekly-dump
  Sun 2026-08-30 02:00, docs-drift Mon 2026-08-31 09:00.
- **Manifest:** `verify-manifest.sh` → OK, signature valid, all 788
  files match.
- **C-22 still live:** `DEMO_MODE=true` confirmed in the running
  `runner-demo` container's environment.
- Failed units at 00:40: only `docs-drift-weekly` (root-caused in pass
  10, unchanged — its state will persist until the next fire or a manual
  reset; the new OnFailure wiring covers future failures, it does not
  retroactively alert on this standing one).

## Finding 4 (REAL, the main one): checkpoint-5 code is committed and signed but NOT deployed — every live probe still shows the pre-fix behavior

All main service images were built 21:52–22:01 EDT on 08-25 (runner
`20260826T015213Z`-era, web/poller/pusher labeled `20260826T020129Z`) —
that's the checkpoint-4 full-stack rebuild. Checkpoint 5 (`521ba5b`)
committed at 23:15 EDT, 74 minutes AFTER the last image build, and no
rebuild has happened since. Confirmed empirically against the running
system, not inferred from timestamps:

- `GET /openapi.json` returns **200 on both :8004 and :8005** (C-12 —
  and :8005 is demo-api, the "most exposed of the three"; its image is
  dated **2026-08-02**, so it predates far more than checkpoint 5).
- Web :8000 CORS preflight still answers
  `access-control-allow-origin: *` with all methods (C-11).
- `shared/ssrf_guard.py` does not exist in the web container (C-13 —
  the unauthenticated SSRF path is still live).
- `poller/skills/retention_prune.py` absent from the running poller and
  its schedule entry absent from the in-image `main.py` (C-33 — the
  daily retention prune silently isn't scheduled at all; same
  image-predates-file trap INFRA_MAP documents for
  semantic-compile-daily). Pusher likewise predates C-21's dedup
  eviction, and the C-17 `SCHEMA_V38` index migration only runs when the
  new code starts, so the live DB presumably still lacks the expression
  indexes.

**Not a regression and not a CLAUDE.md contradiction** — checkpoint 5
never claimed deployment (only C-22, which is quadlet-env and IS live) —
but it is a live-state gap with teeth: the sign step just completed
(`c7c0f8e`), so the pipeline is stalled exactly at
`build-images.sh` + stack restart. Until that runs, (a) the fixed
SSRF/CORS/openapi/rate-limit/nonce-race behaviors are still exploitable
on the running services, and (b) the planned combined blind
Opus-adversarial + Fable-pentest re-audit would probe the LIVE system
and re-find C-11/C-12/C-13/C-26 as "still present," polluting the
re-audit with already-fixed findings. **Deploy before the re-audit.**
Persisted to second-brain.

## Finding 5 (doc drift, good-news direction): CLAUDE.md's checkpoint-4 "NOT yet deployed" flag is stale — both pending items are live

CLAUDE.md checkpoint 4 says items 3–4 (runner
`--forwarded-allow-ips=127.0.0.1`, nginx `X-Forwarded-For $remote_addr`)
are "staged in the tracked repo but NOT yet deployed live," pending
operator sudo + a runner restart. Both are now deployed: the running
runner container's command line carries
`--forwarded-allow-ips=127.0.0.1` (the 21:52 EDT rebuild picked up
Containerfile.runner, and the runner image postdates checkpoint 4's
21:45 EDT commit — confirmed `_CLOUDFLARE_FRONTED_HOSTNAMES` is in the
in-container `main.py`), and the live
`/etc/nginx/conf.d/tailscale-dispatch-runner.conf` line 25 reads
`proxy_set_header X-Forwarded-For $remote_addr;` with nginx active. The
full C-2 fix (all four parts) is deployed; CLAUDE.md's pending-action
note can be cleared next time it's edited (not edited by this pass —
CLAUDE.md is in the signed manifest).

## Finding 6 (doc drift, minor/additive — flag only)

- `docs/INFRA_MAP.md` (~line 495): the semantic-compile-daily
  "image-predates-file trap … NOT yet been cleared" claim is stale — the
  22:01 EDT poller rebuild cleared it;
  `/app/src/poller/skills/semantic_compile_daily.py` exists in the
  running poller. (Ironically the same rebuild that cleared this one
  opened finding 4's instance of the identical trap for
  `retention_prune.py`.)
- `docs/ALERT_REFERENCE.md`: the ops-health publisher catalog (the
  table at line ~176 and the shell-script section) does not include the
  new `scripts/unit-failure-notify.sh` — by that doc's own
  every-publisher standard ("Publishers this catalog previously
  missed"), that's an omission to add on its next edit. Its "genuine
  current gap" note about ntfy.container's dangling
  `OnFailure=ntfy-container-alert.service` remains accurate and is NOT
  solved by the new template — `unit-failure-notify@` alerts *via* ntfy,
  so it cannot cover ntfy's own death.
- `docs/INFRA_MAP.md` timers list: no mention yet of
  `second-brain-weekly-dump.timer` (Sun 02:00) or the OnFailure notify
  wiring on the two weekly units.

## Footprint (pass 11)

This addendum and one second-brain note. Nothing staged, committed,
signed, deployed, or restarted; no doc other than this file edited (this
file is deliberately outside the signed manifest, so this edit breaks no
integrity sweep).

---

# Addendum: pass 12 — post-commit `887a246` (2026-08-26 ~07:50–08:05 EDT)

Scope: commit `887a246` ("Deploy checkpoint-5 fixes (were committed+signed
but never rebuilt); clean up stale runner comment…"). Second-brain search
ran first: pass 11's note (`01-Sources/manual/20260826T044409Z.md`, the
section above) is the direct predecessor — this commit is the remediation
of pass 11's finding 4 (the checkpoint-5 deploy gap), so this pass
verifies that remediation live rather than re-deriving it. Read-only
except this file and one second-brain note; nothing staged, committed,
or signed.

## Pass-11 finding 4 (checkpoint-5 deploy gap): CLOSED, every probe re-run live

All of pass 11's "still shows pre-fix behavior" probes now show the
fixed behavior:

- **Images/services:** web/poller/pusher/runner/demo/amtrak rebuilt
  01:03–01:14 EDT (post-`521ba5b`); ingest rebuilt again 04:38 EDT with
  the SWIM trust-store fix. All corporatetraveldc app containers running.
- **C-12:** `/openapi.json` → **404 on :8000 (web) and :8004 (demo-api)**.
  :8001/:8005 (runner/runner-demo) answer 200 but with
  `text/html` — that's the SPA catch-all serving `index.html` for any
  unknown path, not a leaked schema (pass 11's ":8005 → 200" probe,
  taken pre-rebuild, was status-code-only; the schema route itself is
  disabled in the running runner code).
- **C-11:** CORS preflight from a disallowed origin → 400 with no
  `access-control-allow-origin` header.
- **C-13:** `shared/ssrf_guard.py` present in the running web container.
- **C-33:** `poller/skills/retention_prune.py` present in the running
  poller and referenced (scheduled) in its in-image `main.py`.
- **C-17:** all three `SCHEMA_V38` expression indexes
  (`idx_faa_registry_mode_s_hex_lower`, `idx_opensky_registry_icao24_lower`,
  `idx_opensky_registry_registration_upper_nodash`) exist in the live
  `corporatetraveldc.db` — pass 11's "presumably unapplied" resolved.
- **C-21:** the dedup-eviction code (`dedup_secs * 10` cutoff in
  `push_dedup.py`) is in the running pusher image.
- **Comment deletion deployed:** the stale `_WATCHLIST_PATHS` NOTE is
  absent from the running runner's `/app/main.py` (and from source).
- **demo-api crash-loop cleared:** active since 01:16 EDT, running
  `corporatetraveldc-demo:latest` (built 01:14). Clarification to pass
  11: the `corporatetraveldc-demo-api:latest` image dated 2026-08-02 is
  an *orphan* no container uses — the demo-api quadlet runs the `demo`
  image, so "demo-api's image predates everything" overstated the gap
  even then.
- **SWIM recovery holding:** `/healthz` → `status: ok`, no stale-feed
  reason; ingest containers run the 04:38 image with
  `solace.messaging.tls.trust-store-path` set in `swim_client.py`.

## Finding 7 (REAL, the main one): HEAD fails its own signed manifest — integrity-sweep goes red every 15 minutes until the operator signs

`scripts/verify-manifest.sh` at HEAD: **`CLAUDE.md`,
`src/ingest/swim_client.py`, `src/swim_test.py` — FAILED** (3 of 788).
Root cause is timeline, not tampering: the manifest files committed in
`887a246` were signed ~01:14 EDT (covering the runner comment deletion),
but the SWIM trust-store fix and the final CLAUDE.md updates were written
~04:10–04:40 EDT, and the two sign attempts at ~04:38 hit approval-gate
TTL expiry (operator asleep — CLAUDE.md's own "sign still outstanding"
note is accurate and stays accurate). The commit then folded those
post-sign edits in alongside the pre-sign manifest.

Teeth, observed live: `corporatetraveldc-integrity-sweep` fails on every
15-minute fire (07:34 and 07:49 confirmed in its journal, next fires
ongoing) and will stay red until `sign-manifest.sh` runs. Second-order
risk: any rebuild of the verified-exec-gated demo image while the
manifest is stale reproduces last night's demo-api crash-loop exactly.
And the planned combined blind Opus-adversarial + Fable-pentest re-audit
should not start against a tree that fails its own integrity check.
**ACTION (operator): run `sign-manifest.sh` — that alone clears the
sweep; nothing else is out of place.** One nit for the record: the
commit message's "Manifest re-signed and verified (788 files)" was true
of the 01:14 state it described, but is not true of the commit's own
final content — worth remembering when reading `git log` later.

## Finding 8 (doc drift, closed-by-code direction): `docs/dispatch-runner-design.md` still instructs deleting the comment `887a246` already deleted

That doc's "Live-code finding for the operator, not fixable from a docs
pass" block (~lines 96–105) says `src/runner/main.py:1511-1513` "still
carries a stale `NOTE:` comment … The comment is what should be
deleted; the code is correct." As of `887a246` the comment is deleted
(confirmed in source and in the running runner image), so the block
describes a resolved condition. On that doc's next edit, mark the
finding resolved-by-`887a246` rather than deleting the paragraph — it
documents why the doc's own older wording was wrong, which is worth
keeping. Flag-only; not edited by this pass.

## Finding 9 (carried forward, second consecutive pass): CLAUDE.md checkpoint-4 "NOT yet deployed" note still stale

CLAUDE.md line 273 still says checkpoint-4 items 3–4 are "staged in the
tracked repo but NOT yet deployed live." Pass 11 (finding 5) verified
both ARE deployed; `887a246`'s +71-line CLAUDE.md edit did not clear the
note. Not re-derived here — carrying the flag so it's cleared on
CLAUDE.md's next signed edit (which finding 7 requires anyway).

## Minor / checked, not drift

- **`transport-pattern-digest` failed-unit attribution is off:** CLAUDE.md's
  known-bad list files it under the 01:00–01:10 EDT rebuild kills, but
  its journal shows `Failed with result 'timeout'` at 00:53:10 after a
  28-minute run that began at 00:25 — before the rebuild window, and a
  different failure mode (runtime timeout; 52s CPU over 28 min wall —
  it was stalled waiting, plausibly Ollama contention). Next fire is
  12:25 EDT today; if the timeout recurs on an idle system it's a real
  item, not a casualty. Flag-only.
- **Failed units at 07:50 EDT:** `integrity-sweep` (finding 7),
  `docs-drift-weekly` (standing, root-caused in pass 10 — the new
  OnFailure wiring covers future fires, not this pre-existing state),
  `transport-pattern-digest` (above). Every other known-bad entry has
  self-cleared, including demo-api.
- **Working tree:** `docs/CLAUDE_MD_DRIFT_REPORT.md` modified — the
  05:15 daily generator refreshed only its "Generated" timestamp line;
  finding list unchanged. Benign; left untouched and uncommitted.
- **No doc invalidated by the SWIM fix itself:** nothing in README.md,
  `src/ingest/README.md`, or docs/ makes TLS/trust-store claims about
  the SWIM client; CLAUDE.md's own postmortem paragraph remains the
  accurate account.
- **Pass 11's minor additive drifts still open** (untouched by
  `887a246`): INFRA_MAP's stale semantic-compile-daily trap note,
  INFRA_MAP's timers list missing `second-brain-weekly-dump.timer` +
  OnFailure wiring, ALERT_REFERENCE's publisher catalog missing
  `scripts/unit-failure-notify.sh`. Carried, not re-derived.

## Footprint (pass 12)

This addendum and one second-brain note. Nothing staged, committed,
signed, deployed, or restarted; no file other than this one edited
(this file is deliberately outside the signed manifest — finding 7's
3-file failure list is unaffected by this edit).

---

# Addendum: pass 13 — post-commit `b894b3d` (2026-08-26 ~08:44–08:50 EDT)

Scope: commit `b894b3d` ("Re-sign manifest to cover the SWIM fix that
rode along in 887a246; NWWS-OI password rotation; close out stale
doc-drift notes"). Second-brain search ran first: pass 12's note
(`01-Sources/manual/20260826T115618Z.md`, the section above) is the
direct predecessor — this commit is the remediation of pass 12's
finding 7, so this pass verifies that remediation live rather than
re-deriving it. No vault note yet covers the NWWS rotation or the SWIM
trust-store fix specifically (searched both; the only coverage is
CLAUDE.md's own narrative — expected, Gate 2's weekly dump hasn't fired
yet). Read-only except this file and one second-brain note; nothing
staged, committed, or signed.

**Clean pass. No new non-trivial drift.** Every claim the commit makes
verified live; the two residuals below are minor flag-onlys.

## Pass-12 finding 7 (stale manifest / red integrity-sweep): CLOSED, verified live

- `verify-manifest.sh` at HEAD: **OK — signature valid, all 788 files
  match** (rc 0, run directly this pass).
- `corporatetraveldc-integrity-sweep` journal: failed at 08:04 and 08:19
  (still the 3-file mismatch), then **"sweep OK" at 08:34:24** — first
  green fire after the re-sign, exactly the predicted self-clear. Unit
  no longer in the failed set.

## The commit's other claims: all verified

- **NWWS-OI rotation:** ingest-core journal shows `NWWS-OI joined MUC
  nwws@conference.nwws-oi.weather.gov as corporatetraveldc` at 08:26:36
  (post-rotation restart) and again at 08:44:12 (see restart-cycle note
  below), no auth failures. CLAUDE.md's "only ingest container with
  `NWWS_ENABLED=true`" claim re-derived from the live quadlets: correct —
  all six SWIM-feed containers set it `false` explicitly; ingest-core
  alone leaves it enabled. No secret value appears in the commit, the
  tracked tree, or this file.
- **Pass-12 finding 9 (stale checkpoint-4 "NOT yet deployed" note):
  CLOSED** — the commit's CLAUDE.md diff replaces it with "Items 1-4 all
  confirmed deployed live," and both underlying facts re-verified this
  pass, independently of passes 11/12: live nginx conf line 25 is
  `proxy_set_header X-Forwarded-For $remote_addr;`, running runner
  cmdline carries `--forwarded-allow-ips=127.0.0.1`.
- **transport-pattern-digest correction:** accurately transcribed into
  CLAUDE.md's known-bad list (timeout at 00:53, pre-rebuild, next fire
  12:25 EDT). Not yet re-fired as of this pass — the 12:25 recheck
  stands.
- **`docs/CLAUDE_MD_DRIFT_REPORT.md`:** commit diff confirms the claim —
  timestamp line only, findings unchanged.
- **`check-claude-md-drift.sh`** (all checks incl. 10–11): passes
  end-to-end, "CLAUDE.md matches live state." Working tree clean at
  08:47 (before this file's edit).
- **Failed units at 08:45:** only `docs-drift-weekly` (standing,
  root-caused pass 10) and `transport-pattern-digest` (above). demo-api
  active/running since 01:16 EDT.

## Observation (not drift, timestamped for future passes): rolling restart 08:34–08:46 EDT

Concurrent with this pass, runner + all seven ingest containers +
poller/pusher were stopped ~08:34 and started 08:44–08:46 (runner down
~11 minutes). **No rebuild** — every container came back on the same
images (main stack 01:03–01:14 builds, ingest 04:38). Presumably the
operator's post-re-sign restart; `/healthz` → `status: ok`, CPS GREEN
immediately after. Recorded so that (a) any scheduled unit that lands
in `--failed` having fired during 08:34–08:46 gets attributed to this
window, not to a code regression, and (b) nobody hunts for a phantom
image rebuild.

## Minor flag-onlys (not persisted — CLAUDE.md's known-bad section self-disclaims)

- CLAUDE.md's known-bad list still carries `corporatetraveldc-demo-api`
  as "currently crash-looping … re-sign + second rebuild (in progress)"
  — stale (healthy since 01:16 EDT, confirmed again this pass), survived
  a commit whose subject includes "close out stale doc-drift notes." The
  section's own header says re-derive rather than trust it, so
  flag-only.
- CLAUDE.md's "Follow-up (~07:50)" paragraph still reads as if the
  3-file manifest failure is ongoing ("will keep failing every 15
  minutes until sign-manifest.sh runs") — the very commit that added the
  paragraph is the follow-up that resolved it. Technically consistent
  (the "until" condition occurred) but worth a one-line "resolved by
  this commit's re-sign" on CLAUDE.md's next edit.

## Carried forward, untouched by `b894b3d` (not re-derived)

Pass 11's additive drifts (INFRA_MAP: stale semantic-compile-daily trap
note, timers list missing `second-brain-weekly-dump.timer`/OnFailure
wiring; ALERT_REFERENCE: publisher catalog missing
`unit-failure-notify.sh`) and pass 12's finding 8
(`dispatch-runner-design.md` still instructs deleting the
already-deleted runner comment) — all still open; the commit touched
none of those files.

## Footprint (pass 13)

This addendum and one second-brain note (recording finding 7's closure
so a future search doesn't act on it as still-open). Nothing staged,
committed, signed, deployed, or restarted by this pass; the 08:34–08:46
restart cycle above was not mine.

---

# Addendum: pass 14 — post-commit `dc7e21a` (2026-08-26 ~10:52–11:05 EDT)

Scope: commit `dc7e21a` ("Second blind Opus audit: fix C-7 registry-wipe
(shared guard), extend scoped secrets to 9 third-party containers (C-4),
fix C-8 ACARS watcher (stale token + airframes API bug)"). Second-brain
search ran first and this time it *mattered*: vault note
`01-Sources/manual/20260824T011337Z.md` (the 08-23/08-24 full CLAUDE.md
dump) already contains an empirically-derived root cause for the exact
airframes.io 404 behavior this commit's C-8 fix addresses — and it
contradicts the commit's diagnosis. Finding 10 below builds on that
prior art instead of starting cold. Read-only except this file and one
second-brain note; nothing staged, committed, or signed.

## Verified: most of the commit's claims hold live

- **Manifest:** `verify-manifest.sh` at HEAD → OK, signature valid, all
  796 files match (rc 0, run this pass).
- **C-4 (scoped secrets), fully confirmed:** all 6 new
  `/etc/corporatetraveldc/<name>-secrets.env` files exist, mode 0600,
  written 10:32 EDT; all 9 third-party containers up (~22 min at check
  time, restarted ~10:31); canary probe `NWWS_PASSWORD` (a
  dispatch-secrets.env-only key) absent from `fr24feed`, `ntfy`, and
  `acarshub` environments — the ~95-key file is genuinely out of the
  third-party containers. All 9 tracked quadlet mirrors byte-identical
  to live installed copies; `check-claude-md-drift.sh` (all checks incl.
  10–11) passes end-to-end: "CLAUDE.md matches live state."
- **C-8 half 1 (stale token), confirmed fixed:** `Watchlist HTTP 403`
  warnings stop at 10:34 EDT (last pre-restart poll); the post-fix
  process (started 10:41:48) loads `Watchlist: 13 reg(s)` cleanly every
  cycle. Running container's `/app/acars_watcher.py` is byte-identical
  to tracked `src/acars_watcher/acars_watcher.py` — the podman-commit
  patch and the committed source do match.
- **C-7 (registry-wipe guard), code-level:** `db._safe_mark_and_sweep()`
  present in tracked source with 3 new tests; both registries intact
  live (`faa_aircraft_registry` 316,222 rows, `opensky_aircraft_registry`
  519,991 — exactly the counts the audit put at risk). But see finding
  12: the guard is NOT in the running poller.
- **Failed units at 10:55:** only the two standing entries —
  `docs-drift-weekly` (root-caused pass 10) and
  `transport-pattern-digest` (pass-12 flag; its 12:25 EDT re-fire hadn't
  happened yet at check time). Nothing new from the 10:09–10:45 EDT
  third-party restart wave.

## Finding 10 (REAL, the main one): C-8's airframes-404 diagnosis is wrong, the 404s continue after the fix, and the correct root cause has been in the vault since 08-23

Three independent confirmations, all this pass:

1. **The 404s did not stop.** CLAUDE.md claims "no more 404 warnings in
   the journal after restart." The post-fix process (PID started
   10:41:48, running the patched limit-less code — verified in-container)
   logged `REST airframes: HTTP 404` at 10:43:55, 10:53:17, and 10:54:17.
2. **`limit` is not the driver.** Reproduced directly against the API:
   `?since=<now-60s>` with NO limit → **404**
   (`"Cannot GET /messages?since=…"`); `?since=<now-3600s>` → **200**
   (263 KB of real data); `?since=<now-3600s>&limit=500` → **200**. The
   commit's controlled test ("`?since=...&limit=500` → 404, `?since=...`
   alone → 200") almost certainly compared different window widths, not
   the limit param — last night's manual `since` test would have used a
   wide window (watcher had been failing for days), which returns 200
   with or without `limit`.
3. **The vault already had this.** Note `20260824T011337Z.md` (08-23
   docs pass) reproduced the real contract: the API answers **404, not
   an empty 200, when the `since` window is short/empty** (`-30s`/`-60s`
   → 404 while `-300s`/`-3600s` → 200), the watcher polls at exactly the
   width that 404s (60s), treats non-200 as failure, and drops the
   cycle — "lossy, not dead." Its recommended fix (widen/overlap the
   window; treat 404 as "no rows") was never applied. The same note's
   second finding is ALSO still live, re-verified this pass: the watcher
   reads `AIRFRAMES_API_KEY` (empty in the container, 0 bytes) while the
   real credential `AIRFRAMES_TOKEN` (present, 68 bytes) is read only by
   runner/common — it polls unauthenticated to this day.

Net: dropping `limit` was harmless but fixed nothing; the watcher is
still lossy on every empty-window cycle; the new code comment in
`acars_watcher.py` (~line 355) now bakes the wrong causal claim into the
source; and CLAUDE.md's C-8 entry + `dc7e21a`'s commit message carry the
same wrong claim. Actual fix remains the 08-23 recommendation, ideally
plus the auth env-var unification. This is precisely the failure mode
the "search the second brain BEFORE re-deriving" convention exists to
catch — the diagnosis was one FTS query away. Persisted to second-brain.

## Finding 11 (REAL): "No Containerfile exists anywhere on disk" is false — `src/acars_watcher/Containerfile` is tracked, valid, and documented

CLAUDE.md's C-8 entry (and the commit message) state no Containerfile
exists to rebuild the acars-watcher image from, justifying the
`podman cp` + `podman commit` workaround and flagging a "repo-hygiene
gap." Wrong on all counts: **`src/acars_watcher/Containerfile`** exists
on disk, is git-tracked (since the v3.0 commit `561ceb0`), and is a
complete build source (`COPY acars_watcher.py .` — the only file the
image needs). `docs/SDR_SERVICES.md:79` documents the exact rebuild
command (`podman build -t localhost/corporatetraveldc-acars-watcher:latest
src/acars_watcher/`), and `TAILNET_MIGRATION_INVENTORY.md:171` cites the
file by path. Last night's search evidently looked only for the
root-level `Containerfile.<name>` naming convention the 7 main services
use. The claim about `scripts/deploy-acars-stack.sh` IS accurate
(its `SRC_DIR=/opt/corporatetraveldc/src/acars_watcher` doesn't exist on
disk — but that's a deploy script's stale path, not a missing build
source). Cleanup suggestion, not urgent: rebuild the image properly from
the Containerfile so the live image isn't a hand-committed mutant —
low-risk since the in-container source is byte-identical to tracked.
No repo-hygiene follow-up needed; the flag should be dropped on
CLAUDE.md's next edit. Persisted to second-brain.

## Finding 12 (REAL, deploy gap — same class as pass 11's finding 4): the C-7 registry-wipe guard is committed and signed but NOT running

The running poller (`localhost/corporatetraveldc-poller:latest`, built
01:12 EDT — 9½ hours before `dc7e21a` at 10:50) has **zero occurrences
of `_safe_mark_and_sweep`** in its in-image `db.py`. No image rebuild has
happened since the commit. The FAA registry refresh (upsert + the
unguarded sweep) runs on a **daily** interval (`FAA_REGISTRY_INTERVAL =
1 * 86400`, `src/poller/main.py:338`), so the exact wipe scenario the
audit called Critical — a 200-OK non-CSV response zeroing the
316,222/519,991-row tables — remains live once per day until the poller
image is rebuilt and restarted. Registries are intact right now (counts
above). CLAUDE.md never claimed C-7 was deployed (unlike C-4/C-8, which
were), so this is a live-state gap, not a doc contradiction — but the
same argument as pass 11 applies: **rebuild the poller before the
combined blind re-audit**, or it will re-find C-7 "still present" and the
daily wipe window stays open meanwhile. Persisted to second-brain.

## Finding 13 (REAL, forward-looking — 4th occurrence of the scrubber-prose class): the committed blind-review doc blocks the next public push

`scrub-public-tree.py HEAD` (run this pass, 25s): **VERIFICATION
FAILED** on `docs/investor-materials/v1.5/research/
OPUS_BLIND_REVIEW_2026-08-26.md` — 5 distinct unrecognized values:
`git@github.com`, `203.0.113.55` (TEST-NET-3 probe value), `33.0.6.2`
(actually the Nextcloud version string `33.0.6.2` matching the
IPv4-shaped regex), and — the two that matter — **`10.x.x.x` and
`192.168.x.x`, which are this box's real LAN addresses** (confirmed
against `ip -4 addr`), quoted in the report's dual-homed-network and
host-inventory sections. Unlike pass 10's instance this file is already
committed at HEAD, so the next `push-public.sh` run WILL fail (safe
direction, nothing leaks — but it stalls exactly the investor-docs
pipeline this research directory feeds, and prior research reports at
scrub lines ~491/499 confirm this doc class ships in the public tree).
Recommendation differs by value: the three inert ones fit the allowlist
pattern; the two real LAN IPs should be SUBSTITUTIONS/defanged in the
doc (or the file added to DROP_FILES), not allowlisted — they're
RFC1918-private but still real internal addressing published alongside a
security-posture writeup. Bigger operator question flagged, not decided
here: whether a blind security review enumerating live, partially-unfixed
weaknesses should ship to the public mirror at all, even scrubbed.
Persisted to second-brain.

## Minor / additive (flag-only)

- `docs/INFRA_MAP.md`'s secrets-file table (§ around line 864) lists
  only `dispatch-secrets.env` — none of the now-seven scoped
  `*-secrets.env` files (demo + the 6 new third-party ones). Joins the
  carried additive-drift list (INFRA_MAP timers/trap notes,
  ALERT_REFERENCE publisher catalog, dispatch-runner-design resolved
  finding — all still open, none touched by `dc7e21a`).
- `docs/SDR_SERVICES.md:40` still calls acars-watcher "Dual-source"
  (code is triple-source v3.0) — pre-existing cosmetic drift, first
  noted in the 08-23 vault note for the unit `Description=`; same fix
  whenever either is edited.

## Footprint (pass 14)

This addendum and one second-brain note. Nothing staged, committed,
signed, deployed, or restarted. The scrubber run wrote dangling git
objects only (same as passes 10/11); the two airframes.io API probes and
one 3600s-window fetch were plain GETs of a public endpoint the watcher
itself polls every 60s.
