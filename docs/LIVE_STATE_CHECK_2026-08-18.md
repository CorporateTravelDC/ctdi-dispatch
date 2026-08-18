# Live State Check — 2026-08-18

Written 01:47–01:55 EDT. Started against `e2bddca` ("Final manifest re-sign
+ live-state notes after 19/20 model rebuild pass", branch
`fable-timing-artifact-sweep-2026-08-17`, 01:46:28) and HEAD moved three times
under the check: `6d691a1` (01:46:54, `main`, merge of
`drift-audit-2026-08-16` = `713f82d`, 19 files / +1801), `76114cc`
(01:49:26, `main`, "Fix merge regression: restore plain-text ep-advance
scaffold") and `4953c68` (01:53:48, manifest re-sign only). All are
covered below; the merge is the one with real
surface area. Same rules as the 08-12…08-17 checks: does anything README.md,
CLAUDE.md, docs/, `src/ingest/README.md`, or `src/shared/watchlist_README.md`
currently claim no longer match the live box? Verified live, not against
prior docs. Nothing staged, committed, or changed live by me. The operator
was deploying concurrently throughout (re-sign 01:51, `build-models.sh
corporatetraveldc-pi5-secondbrain-weekly` running at 01:52).

## Live snapshot verified

- **HEAD `4953c68` on `main`** (see manifest bullet), 10 ahead of
  `origin/main` (un-pushed);
  `fable-timing-artifact-sweep-2026-08-17` still points at `e2bddca` and is
  fully contained in `main`; `drift-audit-2026-08-16` at `713f82d`, merged.
- **Manifest: broken by the merge, re-signed, and this time committed.** `6d691a1` merged
  12 changed files (10 under `src/`, `corporatetraveldc.ep-advance`,
  `docs/LIVE_STATE_CHECK_2026-08-16.md`) against `e2bddca`'s 670-file
  manifest → `verify-manifest.sh` = **INTEGRITY FAILURE, 12 mismatches**
  from 01:46:54 until the operator re-signed at **01:51:10/01:51:18**
  (`OK, 677 files`) and — unlike the four earlier re-signs — **committed it
  within three minutes as `4953c68`** (01:53:48, "Re-sign manifest after
  ep-advance merge-regression fix"; `git diff HEAD -- MANIFEST.sha256`
  empty). So HEAD is now `4953c68` and its committed manifest matches its
  tree; `76114cc` and `6d691a1` remain unverifiable as standalone
  checkouts, which only matters for bisects. The 01:42:26 integrity-sweep
  passed (pre-merge); the 01:57 one will pass. No skill container was
  affected — they verify their own baked `/app` copy, and no image was
  built in the 4-minute window.
- **Ollama models: the rebuild happened.** `ollama list`: 20 of 21
  `corporatetraveldc-pi5-*:latest` are 2 min–2 h old; **20/21 carry the
  "Never write code" persona rule** (all except `secondbrain-weekly`, still
  the 2-day-old build — matches the commit's "19/20 brief-class + smoke
  timeout" exactly; `BRIEF_MODELS` is 20 entries, `chat` is the 21st
  model). `ollama show --parameters`: ops-brief `num_predict 900`,
  ep-advance `1000`, weekly-summary `700`, secondbrain-weekly `500` (old).
  A `secondbrain-weekly:candidate` was created 01:51 by the operator's
  standalone retry, in progress at 01:52. The 08-17 addendum-3 item "models
  NOT rebuilt" is **closed** for 20/21.
- **Live ep-advance model = `e2bddca`'s Modelfile**, i.e. persona rule ✓,
  venue closed-set rule ✓ (`may ONLY name venues` present), `num_predict
  1000` ✓, no `(( double parens ))` scaffold. See drift item 1 for why that
  now differs from HEAD.
- **None of the 8 drift-audit fixes are live.** Every running image
  predates the merge: `ingest:latest dbbeb8fc2676` (08-17 12:20 EDT, all 7
  ingest containers), `poller 2eaf83fda734` / `web 27ab4a345492` / `pusher
  1468b440cd94` (08-17 21:03–21:04, restarted 21:04:29). `podman exec` grep
  inside each: `ingest-tfms` has no `_tfms_program_metric` (0), `pusher`
  still has `should_push(key, h, hot=True)` (1), `poller` `push_dedup.py`
  has no `flock` (0) and `watchlist.py` no `tzinfo is None` (0), `web`
  `webhooks.py` no `compare_digest` (0), `routes/watchlist.py` no
  route-order fix (0). Host tree has them all. So the HIGH items in
  `docs/DRIFT_AUDIT_2026-08-16.md` — VIP TFR re-firing ntfy p5 + Pushover
  Emergency every 30 s while active, cross-process PushDedup clobbering,
  TFMS "+?min" text — are still the live behaviour until `build-images.sh`
  + core restart + `ingest-feed-ctl.sh restart all`. (The doc's
  "Operator-confirmed live" on the TFMS row is the *bug* confirmed live,
  not the fix — reads fine.)
- Units: **118** `corporatetraveldc-*` loaded (115 on 08-17); **39**
  containers (`podman ps`; ~8 in-flight skill containers). Failed units:
  **1** — `disruption-weather-digest` (the 08-17 15:57 SIGKILL at the old
  1 700 s ceiling; stays `failed` until its 04:35 run under the new 3 000 s
  ceiling — unchanged from addendum 3). `integrity-sweep` cleared.
- Endpoints: `:8000/healthz` 200; `:8083/openapi.json` 200 (mcpo still up);
  `:8005/healthz` connection refused (runner-demo still looping,
  `NRestarts=6175`).
- Test suite on `76114cc` (tree identical to `4953c68` except the
  manifest pair): **17 failed, 144 passed** (3.2 s). Same 17
  pre-existing failures (11 watchlist schema-chain, 5 `_dispatch_proxy_headers`,
  1 `test_smes_parser_basic`); the merge's 4 new test files (+30 tests) all
  pass on the merged tree.
- **brief-fallback-monitor fired MAX-PRIORITY at 01:50:07** — `ep-advance:
  last 6=[LLM LLM FALLBACK FALLBACK FALLBACK FALLBACK] consec_fb=4`,
  `ops-brief: fb=2/6`. Causes, from the journals: ep-advance 23:09 = the new
  `sanitize_llm_response()` **repetition-loop guard discarding** the model
  output ("'EP: Historic resort; private airfield…' x3 — discarding, caller
  falls back") — the sweep's guard working as designed; 00:00 and 01:00 =
  "Ollama slot unavailable — could not acquire Ollama lock within 1800.0s"
  — contention with the model-rebuild smoke tests (900 s each, 20 models,
  23:00–01:45) plus the 00:00 ops-brief+trend, which itself hit its **3 600 s
  ceiling at 01:00:42** (`Failed with result 'timeout'`, brief 1619 archived
  as "narrative unavailable (Ollama offline)"); the 01:00 ops-brief then ran
  01:00:43–01:43:05 and generated via Ollama. Rebuild-night contention, not
  a regression in the code that shipped — but it is the CLAUDE.md line
  "`brief-fallback-monitor` (hourly) alerts loudly when that happens"
  verified live, and the streak clears only after 2 clean ep-advance runs
  (02:30/03:30 if the slot is free).
- **Ops-brief prose fixes — the 08-17 addendum-3 acceptance test, run.**
  Post-deploy LLM briefs 1615 (22:36), 1617 (23:31), 1621 (01:43): raw
  METAR/NAS/ATCSCC appendix **gone** (3.0–3.7 k chars vs 10.8 k for 1613,
  the last pre-deploy one); `=== ADVANCED AIR MOBILITY WATCH ===` block
  **gone** (present only in 1619, the deterministic-fallback one, which is
  the old template path); NWS present in 1617/1621, Amtrak in 1617/1621,
  neither in 1615. **Two things the addendum's criterion "no `=== ` blocks"
  got wrong:** (a) `=== DISRUPTION/WEATHER (30d) ===` is a *deliberate*
  post-hoc capsule (`ops_brief.py:1018`, 2026-08-10, "short truncated
  capsule" — the `…facil...` tail is by design), so it will always be
  there; (b) phi3 **echoes the prompt header** `=== OPS BRIEF DATA PULL
  2026-08-18 05:00 UTC ===` as line 3 of 1621 and writes markdown
  (`**CPS Status:**`, `- ` bullets, `---` in 1615) despite the persona's
  "Plain text only. No markdown, no bullet symbols". Not doc drift; recorded
  so the next reader doesn't count the capsule as a regression and does
  count the header echo + markdown as the residual quality gap.

## Drift found

### 1. `76114cc` restored the wrong revision of `corporatetraveldc.ep-advance` — drops the venue closed-set rule + `num_predict 1000` that its own message says it preserves

Commit message: "Every real EP-advance fix from this session (the 'never
write code' persona rule, the geographic closed-set constraint for
hotel/venue sections) was applied to the plain-text format, which is also
what the just-rebuilt … model was actually built from. Restored verbatim
from `b3a914b`." But `b3a914b` (14:48 08-17) **predates `675b0c2`** (21:03),
which is the commit that added the closed-set rule and the 750→1000
parity to that file. Verified: `git diff b3a914b HEAD --
corporatetraveldc.ep-advance` is empty; `git diff e2bddca HEAD -- …` is
−31 lines: the `PARAMETER num_predict 1000` block (+ its parity comment)
→ `750`, and the whole "HOTEL RECOMMENDATION, DINING RECOMMENDATION,
EXTENDED OPERATIONS, and VENUE ADVISORY may ONLY name venues, cities, and
regions that literally appear in the EXTENDED VENUE MATRIX … never to any
other US region or state" paragraph deleted. HEAD now: persona rule ✓
(1), venue rule ✗ (0), scaffold ✗ (0, correctly removed), `num_predict
750`. The **live model has the venue rule and 1000** (built from
`e2bddca`), so repo Modelfile ≠ live model, in the wrong direction: the
next `build-models.sh` run rebuilds ep-advance *without* the geographic
constraint and with a Modelfile cap of 750 against `ep_advance_brief.py:986`'s
`num_predict: 1000` (the request option wins at runtime, but
`docs/PHASE4_VALIDATION_2026-08-16.md` §1's "num_predict matching each
call site's max_tokens exactly (all 21 pairs)" and the sweep doc's parity
claim become false for this pair). Fix: `git checkout e2bddca --
corporatetraveldc.ep-advance` (that file has persona + venue + 1000 and no
scaffold — it *is* the plain-text version the message describes), re-sign.
Docs that would go stale if left as-is: `docs/FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md`
(venue-scope rule + parity), `docs/PHASE4_VALIDATION_2026-08-16.md` §1,
`docs/LIVE_STATE_CHECK_2026-08-17.md` addendum 3.

### 2. `docs/DRIFT_AUDIT_2026-08-16.md` — stale on arrival, three lines

Lines 3–6: "Not committed … review the working tree via `git diff
main...drift-audit-2026-08-16` … `main` was never touched" → committed
`713f82d`, merged to `main` `6d691a1`. Line 14: "138 passed, 17 failed" →
**144 passed, 17 failed** on the merged tree. Line 63: a stray `"""` closes
the file (copy artifact). Also worth a one-line "deployment status" note
at the top: as of this check every fix in the table is repo-only (see
snapshot) — the doc's tone ("Fixes applied") reads as live.

### 3. `docs/LIVE_STATE_CHECK_2026-08-17.md` addendum 3 — three of its "open" bullets have closed, one criterion was wrong

Models "NOT rebuilt (again)" → rebuilt 20/21 (above); "manifest re-sign
uncommitted for the fourth time" → committed in `e2bddca`, then re-broken
by the merge, re-signed 01:51 and committed 01:53 (`4953c68`); "First real evidence
… no `=== ` blocks" → capsule is by design (above). Annotated here, not
edited. Everything else it lists as open is still open (see below).

### 4. `docs/ALERT_REFERENCE.md:218` — literally accurate, and it describes the exact bug the merge fixed elsewhere

"tfr_enrichment.py … stable VIP-TFR-ID dedup key, 1-hour window, VIP
pushes always bypass suppression (`hot=True`)". `tfr_enrichment.py:49-50`
is untouched by the merge and still does `hot = "vip=True" in stable_key
…; should_push(key, h, hot=hot)` — so the sentence is true, but "1-hour
window" is void on the VIP path for the same reason drift-audit finding 2
removed `hot=True` from `pusher/main.py` and `route_impact.py`
(`push_dedup.py:130` — `hot` returns True before any window check). Not
drift; flagging because the merge closed 2 of the 3 `hot=True` VIP call
sites and left this one, and the doc line is where the next reader would
look. Its route_impact description (`:210-215`, "stable dedup key … doesn't
defeat the dedup") is now *actually* true in the repo for the first time
(it was unreachable before the fix) — no edit needed.

## Still accurate (checked because these commits could have touched them)

- `src/shared/watchlist_README.md:87` `DELETE /api/v1/watchlist/batch |
  admin | Batch remove` — the merge makes this reachable in code
  (`routes/watchlist.py:446` `/batch` now registered before `:490`
  `/{entry_id}`); previously 404'd. Live web image still has the old order.
  Nothing else in that README describes the landed-sweep timestamp handling
  the naive-datetime fix touched.
- `src/ingest/README.md:94` "50 NM approach alerts (10-min dedup)" and
  `:119-127` (amendment dedup "mirrors `_handle_track_information`'s …
  keys on entry-id only") — still correct after the merge's key/content
  swap in both parsers (`_FDPS_PROX_DEDUP = PushDedup("fdps_prox",
  dedup_secs=600)`; tfms_track now `should_push(entry_id, "approach")`).
  No doc reproduces the TFMS "avg delay +Nmin" alert text, so nothing to
  update for `_tfms_program_metric`. (`HF`/`RH`/`DH` list gap unchanged.)
- No doc in the named set describes the pusher's VIP TFR cadence, PushDedup
  file semantics, the webhook secret compare, vault path decoding, or
  `entity_tracking.save_state` — nothing for those fixes to contradict.
  `README.md:222-224` (webhook `X-Webhook-Secret`, 503 until configured)
  still right.
- `CLAUDE.md:138-160` (llm.py single entry point, `_abandon_ollama_generation`,
  cloud-fallback default, deterministic templates + `brief-fallback-monitor`)
  — all still true and the monitor line was exercised live at 01:50.
- `docs/RUST_REWRITE_ASSESSMENT_2026-08-16.md` (new via merge) — its
  push_dedup "now flock+mtime-merge" references match the merged code.

## Pre-existing, unchanged (one line each)

- MCP half-retirement: both mcpo units active, `:8083` 200, dead `ExecStart`
  wrapper path. runner-demo crash-loop (`NRestarts=6175`), `CLAUDE.md:99-100`.
- `CLAUDE.md:17`/`README.md:14` "145 units" → 118; `CLAUDE.md:165`/`README.md:475`
  "V31" → `SCHEMA_V33`; `CLAUDE.md:147-151`/`README.md:54,492,528` "16
  models / gemma3:4b" → 21, all `phi3:mini` (now the freshly rebuilt set).
- `docs/INFRA_MAP.md:164` watch cadence; `:175-178` + `weekly-doc-drift-check.sh:3-5`
  "both repos"; `src/ingest/README.md:94` `HF`/`RH`/`DH`;
  `SECOND_BRAIN_STATUS.md:289`; `SMOKE_TEST_HARNESS` "uncommitted";
  `brief-fallback-monitor.sh:65` alert text; `SINGLE_EDGE_UNIT_ASSUMPTIONS.md:17-18`.
- `docs/FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md` — `<!-- ABC_RESULTS -->`
  (`:160`) and `<!-- SCORECARD_ADDENDUM -->` (`:198`) still unfilled; no
  status-as-of note added; §7 step 3 (`build-models.sh`) is now done for
  20/21.

## Bottom line

The merge (`6d691a1`) invalidated nothing that the docs claim — the eight
drift-audit fixes touch code paths no doc describes in that detail, and the
two docs it brought in are self-describing dated records. What it did do
live: break the signed manifest for ~4 min (re-signed 01:51, committed
01:53 as `4953c68`), and land eight fixes that are **not running anywhere yet** (all
images predate it — the pusher is still re-firing VIP TFRs every 30 s).
The one real drift is the follow-up commit `76114cc`: it restored
`corporatetraveldc.ep-advance` from `b3a914b` instead of `e2bddca`, so the
repo Modelfile lost the venue closed-set rule and the 1000-token parity that
the live model was just rebuilt with and that the commit message says it
kept — one `git checkout e2bddca -- corporatetraveldc.ep-advance` + re-sign
before the next `build-models.sh`. Three one-liners in
`DRIFT_AUDIT_2026-08-16.md`, and the 08-17 addendum's model/manifest bullets
have closed. Operator to-do implied: fix the ep-advance restore (+ one more re-sign),
`build-images.sh` + core restart + `ingest-feed-ctl.sh restart all
--order=lightest-first --stagger=15` to make the audit fixes live, then
watch ep-advance at 02:30/03:30 for the fallback streak to clear.

---

## Independent cross-check (second session, 01:50–01:58 EDT) — delta only

Ran concurrently against `76114cc` without sight of the section above until
it was written; every finding above was reached independently and is
confirmed live (ep-advance over-restore incl. `ollama show --system` diffing
clean against `675b0c2`/`e2bddca` and missing lines 96–104 vs HEAD; the
01:50:28 12-file manifest failure and 01:51:10/18 re-sign; no merged `src/`
fix in any running container; 118 units / 1 failed / 144-17 tests;
`tfr_enrichment.py:49-50` still `hot=`; `DRIFT_AUDIT` header stale). Two
additions and one nuance:

- **`docs/RUST_REWRITE_ASSESSMENT_2026-08-16.md:6`** (new via the merge)
  says "Raspberry Pi 5 (aarch64, **8 GB**)". The box is 16 GB (`free -g` →
  15 GiB total, 4 cores), as CLAUDE.md and
  `SINGLE_EDGE_UNIT_ASSUMPTIONS.md` state. Cosmetic to its argument; wrong.
- **`76114cc`'s other claim checks out.** "Verified the other two
  auto-merged files (fdps_parser.py, shared/watchlist.py) did NOT have this
  problem — both correctly union both sides" — true in HEAD:
  `fdps_parser.py:155` `"HF", "RH"` + `:1015` `should_push(dedup_key,
  "prox")`; `watchlist.py:215`/`:527-528` title newline collapse + `:307`/
  `:439` `tzinfo is None` skip. Only the ep-advance half of the message is
  wrong.
- Nuance on the manifest gap: `build-models.sh` for secondbrain-weekly
  started 01:51:39 — 21 s *after* the re-sign — so its whole-tree GUARD-0
  passed legitimately; the 01:51 signature nonetheless vouches for the
  regressed ep-advance Modelfile, so GUARD-0 will not stop a rebuild from
  it. The `git checkout e2bddca -- corporatetraveldc.ep-advance` + re-sign
  above is the only thing that does.

---

## Addendum — third pass, 02:20–02:33 EDT (delta only)

HEAD still `4953c68`, working tree clean apart from this file, **no image
rebuilt and no commit since the passes above** — so the "deploy" under check
is the same `6d691a1`/`76114cc`/`4953c68` trio. Re-verified every claim
above live rather than trusting it; only differences and new facts recorded.
Nothing staged, committed, or changed live.

### Re-verified unchanged (02:20)

`verify-manifest.sh` OK 677 files (integrity-sweeps 01:57 and 02:12 both
OK); 118 units, 1 failed (`disruption-weather-digest`, next 04:35 under
`TimeoutStartSec=3000`); 37 containers, all core/ingest images still the
pre-merge IDs (`web 27ab4a345492`, `poller 2eaf83fda734`, `pusher
1468b440cd94`, `ingest dbbeb8fc2676`); `:8000` 200, `:8001` 200, `:8083`
200, `:8005` refused (`runner-demo` `NRestarts=6405`, still auto-restart);
tests **17 failed / 144 passed** (3.3 s); **drift item 1 still open** —
`git diff b3a914b HEAD -- corporatetraveldc.ep-advance` empty, HEAD has
`num_predict 750` and 0 hits for `may ONLY name venues`, while the live
`corporatetraveldc-pi5-ep-advance` (built **00:32:18**) has `num_predict
1000` and the venue rule (1). `DRIFT_AUDIT_2026-08-16.md:3-6,14` header
and `RUST_REWRITE_ASSESSMENT_2026-08-16.md:6` "8 GB" (box: `free -g` 15 GiB)
still as flagged.

### New since 01:58

1. **Models: 21/21 now.** `secondbrain-weekly:latest` finished rebuilding at
   **02:20:16** (`ollama show --system` × 21 → all 21 carry "Never write
   code"). The 08-17 addendum-3 "models NOT rebuilt" item is fully closed;
   the "20/21" residual above is gone.
2. **ep-advance 01:30 run — first completed generation from the 00:32
   model, discarded.** 43 m 59 s wall; the model answered, `sanitize_llm_response()`
   discarded it as a repetition loop ("The Jefferson Hotel [NW] 1200 16th St
   NW (EP-historic Beaux-…" ×3) → `brief generated (deterministic fallback)`
   at 02:13:57. That makes **consec_fb=5** on ep-advance; `brief-fallback-monitor`
   (next 02:50) will re-alert MAX priority — expected, not a new fault.
   Worth recording: The Jefferson Hotel *is* in `EXTENDED_VENUES_50MI`
   (`ep_advance_brief.py:345`), so the venue closed-set rule the live model
   carries **held** on its first real output while the repetition loop
   **recurred** — same shape as 1597/1598 in
   `FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md` §1, now on an in-scope venue.
   Sample size 1; not doc drift, but it is the first live datapoint for that
   doc's still-empty `<!-- SCORECARD_ADDENDUM -->` (`:198`), and it means
   the 00:32 rebuild has produced zero LLM ep-advance briefs so far (00:00 /
   01:00 lost the slot, 01:30 discarded).
3. **Vault traversal fix — bypass confirmed live behaviourally, not just by
   grep.** Read-only probes against the running (pre-merge) web on the T0
   `/api/v1/vault/research` route with a scoped, nonexistent target:
   `…/Series/..%2Fzz` → 400 `invalid path`; `…/%2e%2e%2Fzz` → 400;
   `…/%252e%252e%2Fzz` → **404 `not found in vault`** (passed the old guard,
   reached WebDAV); plain `zz` → 404. So the double-decode gap
   `_vault_path_is_safe()` closes (`web/main.py:223`) is the live behaviour
   until `build-images.sh web` + restart. `/api/v1/vault/file` is T1 (403
   anon), `research` + `research/list` are T0 — the merged docstring's "two
   of them are Tier-0/unauthenticated" is right. Two dated docs say the guard
   "is intact / works (`..`/leading-`/` → 400)" —
   `LIVE_VALIDATION_AND_PENTEST_2026-08-13.md:222`,
   `LIVE_STATE_CHECK_2026-08-12.md:357`. Literally true for what they
   probed (single-encoded); the multi-encoded case they didn't try is what
   the merge fixes. Dated records — annotated here, not edited.
4. **Container images bake the host `__pycache__`.** Inside the live web
   container `/app/src/web/__pycache__/main.cpython-314.pyc` (dated Aug 16
   23:05, i.e. the drift-audit branch's own test run) *contains* the string
   `_vault_path_is_safe` while `/app/src/web/main.py` (Aug 17 11:24) does
   not — `Containerfile.*` does `COPY src/ src/` and there is no
   `.containerignore`, so host pycs ride along; the container runs Python
   3.13 and never loads a 3.14 pyc, so it is inert. Harmless, but a bare
   `grep -r` inside a container to check "is fix X live" can false-positive
   — grep `*.py`. Not doc drift (no doc claims images are clean of pycache).

### Bottom line (this pass)

No new documentation drift. Nothing about the merge changed live in the last
half hour: the eight drift-audit fixes are still repo-only (now confirmed
behaviourally for the vault guard, not just by grep), the ep-advance
Modelfile over-restore is still open, and the only positive delta is the
21st model finishing its rebuild. Operator to-do unchanged from the bottom
line above; add: the ep-advance repetition loop survived the rebuild on its
first completed run — watch 02:30/03:30 before treating the sweep's ep-advance
fixes as validated.
