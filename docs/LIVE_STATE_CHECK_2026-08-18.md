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

---

## Addendum — fourth pass, 02:26–02:28 EDT, against HEAD `6d86df5` (delta only)

HEAD is now `6d86df5` (02:25:55, "Add live-state check notes") — the commit
that added the three passes above and **nothing else** (`docs/` only, 352
lines, 12 ahead of `origin/main`). No code, Modelfile, Quadlet, or manifest
change since `4953c68`, so the deploy under check is unchanged and this pass
is a re-verification, not a new audit. Working tree clean before this edit.
Nothing staged, committed, or changed live.

Re-verified live at 02:26: `verify-manifest.sh` OK 677 (a docs-only commit
cannot break it — `docs/` isn't in the manifest); 118 units / 1 failed
(`disruption-weather-digest`, next 04:35); 37 containers, core + all 7 ingest
still on the pre-merge images (`web 27ab4a345492`, `poller 2eaf83fda734`,
`pusher 1468b440cd94`, `ingest dbbeb8fc2676`) ⇒ **the eight drift-audit
fixes are still repo-only**; `:8000` 200, `:8001` 200, `:8083` openapi 200,
`:8005` refused; tests **17 failed / 144 passed** (4.3 s); 21/21
`corporatetraveldc-pi5-*` models, live `ep-advance` `num_predict 1000` +
venue rule (1) + persona (1); **drift item 1 still open** (`git diff
b3a914b HEAD -- corporatetraveldc.ep-advance` empty; HEAD `num_predict 750`,
0 venue-rule hits). Ops-brief 02:00 was still `activating` at 02:27 (Ollama
`/api/generate` returned 200 at 02:22:13, 21 min gen; `osint-monitor` model
unloading) — the ep-advance 02:30 run will contend for the slot again, so
don't read a 02:30 fallback as a code regression.

One correction to drift item 1's own text: the `e2bddca`→HEAD Modelfile
diff is **13 deleted / 1 added** (net −12), not "−31 lines". The *content*
listed there is exactly right (the `num_predict 1000` block + parity comment
→ `750`, and the whole "may ONLY name venues … never to any other US region
or state" paragraph gone); only the line count was off. Fix unchanged: `git
checkout e2bddca -- corporatetraveldc.ep-advance` + re-sign before the next
`build-models.sh`.

Docs check for this specific commit: no doc index enumerates the
`LIVE_STATE_CHECK_*` files (the only cross-reference to the 08-17 file is
`FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md:31`, describing a manifest
mechanism, not a list), so adding this file left nothing to update in
README.md, CLAUDE.md, `docs/`, `src/ingest/README.md`, or
`src/shared/watchlist_README.md`.

**Bottom line (this pass): no drift from `6d86df5`; nothing changed live
since the third pass; operator to-do unchanged** (ep-advance restore + re-sign,
`build-images.sh` + core restart + `ingest-feed-ctl.sh restart all
--order=lightest-first --stagger=15`, then watch ep-advance 03:30+ once the
02:00 ops-brief has released the slot).

---

## Addendum — fifth pass, 10:12–10:20 EDT, against HEAD `4078706` (delta only)

HEAD is `4078706` (10:12:03, "Fix stale gemma3-SWA alert text -- root cause
varies, don't assume"), 13 ahead of `origin/main`. One file:
`scripts/brief-fallback-monitor.sh` (+13/−1) — a header comment explaining
why the alert body was wrong, and the alert body itself: "This is the
gemma3-SWA / Ollama-timeout failure class … (memory:
brief-ollama-gemma3-swa-fallback)" → "Root cause varies -- check journalctl
--user -u ${unit} around the failure time … before assuming a specific
cause." No code path, threshold, window, unit, or topic changed. Checked
~40 s after the commit landed. Nothing staged, committed, or changed live by
me; working tree = this file only.

### What this commit changes live

- **It is live already.** `corporatetraveldc-brief-fallback-monitor.service`
  is `ExecStart=/opt/corporatetraveldc/private/ctdi-dispatch-internal/scripts/brief-fallback-monitor.sh`
  — runs the host tree directly, no container, no baked copy, and the script
  does not call `verify-manifest.sh` itself. Live unit file = repo copy
  (`diff -q` clean). Next fire 10:50:00; it *will* alert (ep-advance is at
  **5 consecutive fallbacks**, 06:27→10:07 — the 09:50 run saw
  `consec_fb=4 fb=4/6` and sent MAX priority), so 10:50 is the first live
  use of the new text.
- **The commit's own factual claim verifies.** Journal since 06:00:
  ep-advance 06:27, 07:10, 08:00, 09:06, 10:07 are all
  `common.llm: … response is a repetition loop (… x3) -- discarding, caller
  falls back` (06:27 looped on the `=== EXTENDED VENUE MATRIX` header; the
  other four on "The Jefferson Hotel …" — same in-scope venue as the 01:30
  specimen in pass 3). Zero `could not acquire Ollama lock` / timeout on
  ep-advance since 01:00. So "live cause is repetition-loop hallucinations
  getting correctly caught and discarded, not a timeout" is exactly right;
  the ops-brief side is different (03:30 and 06:30 deterministic = "Ollama
  slot unavailable … 1800.0s", i.e. contention, LLM at 01:43/02:33/04:36/
  05:32/08:19) — which is the point of making the text cause-agnostic.
- **Closes a pre-existing item.** "`brief-fallback-monitor.sh:65` alert
  text" in the first pass's *Pre-existing, unchanged* list (line 214 above)
  is now fixed. Nothing else in that list moved.

### Drift found

#### 5. Signed manifest broken again — `scripts/brief-fallback-monitor.sh` is a manifest file and was not re-signed

`MANIFEST.sha256:334` covers the script. `verify-manifest.sh` →
**INTEGRITY FAILURE, 1 mismatch (`scripts/brief-fallback-monitor.sh:
FAILED`)**. Timeline: file mtime **09:57:36** (working-tree edit); the
09:42:38 sweep was `OK 677`; **09:57:39** sweep FAILED; **10:12:03**
committed without a manifest change (`git log -1 -- MANIFEST.sha256` still
`4953c68` 01:53:48); **10:12:39** sweep FAILED again. Two p5
`INTEGRITY SWEEP FAILED` alerts to `ops-health` so far, and it re-fires
every 15 min (next 10:27:39) until re-signed.
`corporatetraveldc-integrity-sweep.service` is now the **only failed unit**
(118 loaded, 1 failed — `disruption-weather-digest` cleared at its 04:35 run,
finished 04:41:27 under the 3 000 s ceiling). Blast radius while broken:
`build-models.sh` GUARD-0 refuses (blocks the drift-item-1 ep-advance rebuild
even after the Modelfile is fixed), host-side `src/common/llm.py`
(`_VERIFY_SCRIPT = <repo>/scripts/verify-manifest.sh`) refuses any manual
`PYTHONPATH=src python3 src/poller/skills/*` run; running containers are
unaffected (they verify their baked `/app`, all built 02:27 against the
01:51 signature). This is the CLAUDE.md rule "After changing tracked code,
re-sign with `scripts/sign-manifest.sh` … Never bypass this" not being
followed — sixth manifest break in this check series (08-15 ×2, 08-16,
08-17, 01:47 today, now). Fix: `scripts/sign-manifest.sh` + commit the
manifest pair; fold it into the same re-sign the ep-advance restore needs.
Not a documentation error — the docs say the right thing — but it is live
state that a doc reader would assume is clean.

### Docs check for this specific change

- No doc in README.md, CLAUDE.md, `docs/`, `src/ingest/README.md`,
  `src/shared/watchlist_README.md` quotes the alert body or the "gemma3-SWA
  failure class" phrasing (the only hits are `LIVE_STATE_CHECK_2026-08-1[2-8]`
  dated records and `INVESTOR_MATERIALS_REVERIFICATION_2026-08-09.md:82,104`
  — "gemma3-SWA root fix (rebuild brief models from qwen2.5:3b/llama3.2:3b)",
  a dated plan whose base-model choice was superseded by phi3:mini on 08-15;
  pre-existing, not created by this commit).
- Every live description of the monitor is behaviour-only and still true:
  `CLAUDE.md:159` "`brief-fallback-monitor` (hourly) alerts loudly",
  `README.md:519-520` "Guard 3 … (hourly) alerts loudly", `ALERT_REFERENCE.md:42`
  "(hourly, loud alert on deterministic-fallback degradation, 2026-08-08)",
  `INFRA_MAP.md:172` "brief-fallback-monitor :50" (timer `OnCalendar=*:50:00`
  ✓). None claims it runs in a container or names the memory the alert
  used to cite. **Nothing to update for this commit.**

### Live state moved since the fourth pass (not from this commit)

- **The eight drift-audit fixes are now live.** `build-images.sh` ran
  02:27:05–02:27:58 EDT (`ingest 6e867f274783`, `poller 2500e795979c`,
  `web 2eddb421694f`, `pusher 363961604403`); ingest ×7 restarted
  02:28:13–02:28:16, poller/web/pusher 02:28:28–29 — i.e. the operator did
  the pass-1 to-do within a minute of pass 4. Verified inside the running
  containers (`*.py` only, per the pycache caveat in pass 3): pusher
  `hot=True` only in the 2 comment lines (`main.py:107-108`); poller
  `push_dedup.py` `flock` ×5, `watchlist.py` `tzinfo is None` ×2; web
  `_vault_path_is_safe` ×4, `routes/webhooks.py` `compare_digest` ×2,
  `routes/watchlist.py` `/batch` registered above `/{entry_id}`; ingest-tfms
  `parsers/tfms_parser.py` `_tfms_program_metric` ×2. **Behaviourally:** the
  pass-3 double-encoded probe `GET /api/v1/vault/research?path=Series/%252e%252e/zz`
  now → **400 `invalid path`** (was 404 through to WebDAV); single-encoded and
  plain `..` still 400. So the pass-1 "None of the 8 drift-audit fixes are
  live" bullet, the pass-1 bottom line's "pusher is still re-firing VIP TFRs
  every 30 s", and the suggested "deployment status" note for
  `DRIFT_AUDIT_2026-08-16.md` are all closed (that doc's remaining staleness
  is just its `:3-6,14` header lines).
- **ep-advance produced its first LLM briefs from the 00:32 model** — 03:18,
  04:06, 05:11 (`brief generated via Ollama/corporatetraveldc-pi5-ep-advance`),
  closing pass 3's "zero LLM ep-advance briefs so far". Then the
  repetition-loop guard discarded every run 06:27→10:07 (5 straight). Net
  for the sweep doc's still-empty `<!-- SCORECARD_ADDENDUM -->`: 3 pass / 6
  discard on 9 completed generations from the rebuilt model today.
- Everything else re-verified unchanged: 37 containers; `:8000` 200, `:8001`
  200, `:8083` 200, `:8005` refused (`runner-demo` `NRestarts=10155`); tests
  **17 failed / 144 passed** (4.3 s, same 17); 21/21 models; **drift item 1
  still open** (`git diff b3a914b HEAD -- corporatetraveldc.ep-advance`
  empty, HEAD `num_predict 750`, 0 venue-rule hits; live model still the
  00:32 build with `1000` + venue rule, last Modelfile commit `76114cc`);
  `DRIFT_AUDIT_2026-08-16.md:3-6,14` header and
  `RUST_REWRITE_ASSESSMENT_2026-08-16.md:6` "8 GB" still as flagged.

**Concurrent operator edit seen while writing this (10:15:20):**
`corporatetraveldc.ep-advance` appeared modified in the working tree — not by
me — and is now **byte-identical to `e2bddca`** (`git diff e2bddca -- …`
empty: `num_predict 1000` + parity comment back at `:17-19`, venue
closed-set rule back, 1 hit). That is exactly the drift-item-1 fix; it is
uncommitted and unsigned, so `verify-manifest.sh` now shows **2** mismatches
(`corporatetraveldc.ep-advance`, `scripts/brief-fallback-monitor.sh`).
Drift item 1 closes on commit + re-sign.

**Bottom line (this pass): the commit invalidates nothing any doc claims
and is already live via the host-tree ExecStart; the one drift is that it
broke the signed manifest (unsigned since 09:57, sweep alerting p5 every
15 min, `build-models.sh` and host `llm.py` blocked). With the ep-advance
Modelfile already restored in the working tree, the remaining operator to-do
is a single `scripts/sign-manifest.sh` + commit (Modelfile + manifest pair),
then `build-models.sh corporatetraveldc-pi5-ep-advance` is unblocked. The
image/restart step from earlier passes is done.**

---

## Addendum — sixth pass, 11:10–11:15 EDT, against HEAD `dac7954` (delta only)

HEAD is `dac7954` (11:09:31, "Fix over-correction: restore num_predict=1000
+ venue closed-set rule to ep-advance"), 14 ahead of `origin/main`. Three
files: `corporatetraveldc.ep-advance` (+13/−1 — the drift-item-1 restore
seen landing in the working tree at 10:15:20, now committed) plus the
`MANIFEST.sha256` / `.asc` pair (signed 11:09:18/11:09:31). Checked ~30 s
after the commit. Nothing staged, committed, or changed live by me.

**Read this first — this file is now inside the signed manifest.**
`dac7954`'s manifest gained one entry (677 → 678):
`docs/LIVE_STATE_CHECK_2026-08-18.md`, hashed as the *working-tree* version
carrying the uncommitted pass-4/5 addenda (`f75b1956…`), not HEAD's blob
(`7e6834b6…`). Two consequences: (a) HEAD as a standalone checkout is already
1 mismatch on this file (a bisect/clone concern only, same class as
`76114cc`/`6d691a1`); (b) **appending this addendum breaks the live
`verify-manifest.sh` on exactly this one file until the operator re-signs**
— the integrity sweep will report it p5 from its next :27:40 fire,
`build-models.sh` GUARD-0 and host-side `llm.py` refuse until then. Fix is
the routine one: `scripts/sign-manifest.sh` + commit (this file + manifest
pair). This also **corrects pass 4's own rationale** ("a docs-only commit
cannot break it — `docs/` isn't in the manifest"): `docs/` has been in the
manifest all along (60 entries at `4953c68`, 61 now; `sign-manifest.sh:50`
scans `git ls-files --cached --others --exclude-standard`, i.e. every
non-ignored file). Pass 4's *conclusion* held only because `6d86df5` **added**
a new file — `verify-manifest.sh` is `sha256sum -c` over the listed entries,
so an unlisted new file cannot fail it — whereas editing any already-signed
doc does. Series-wide lesson: every `LIVE_STATE_CHECK_*` addendum written
after the file has been signed once is a manifest break until re-signed.

### What this commit changes live

- **Drift item 1 (pass 1) — CLOSED.** `git diff e2bddca HEAD --
  corporatetraveldc.ep-advance` empty: `num_predict 1000` + parity comment
  at `:16-19`, the "may ONLY name venues … never to any other US region or
  state" paragraph at `:120-127`, `Under 750 words` unchanged. Committed
  Modelfile now == the live model — `ollama show corporatetraveldc-pi5-ep-advance`
  (`eec0dda2a1b3`, the 00:32 build) reports `num_predict 1000` /
  `num_ctx 6144` / `temperature 0.15`, venue-rule 1 hit, persona 1 hit. **No
  `build-models.sh` run is needed** to make live match repo for this model.
- **Drift item 5 (pass 5) — CLOSED.** The re-sign covered
  `scripts/brief-fallback-monitor.sh` (new hash `2f7ba310…`) as well.
  `verify-manifest.sh` → **OK, 678 files** at 11:10; the 11:12:41 sweep
  logged `sweep OK` and `corporatetraveldc-integrity-sweep.service` is
  `inactive` (clean) after two failed fires (10:42:40, 10:57:40 — the
  latter still 2 mismatches, `corporatetraveldc.ep-advance` +
  `brief-fallback-monitor.sh`). Manifest last-commit is now `dac7954`,
  `git diff HEAD -- MANIFEST.sha256*` empty. `build-models.sh` and host
  `llm.py` were unblocked from 11:09:31 until this addendum landed.

### Docs check for this specific change

- Restoring the Modelfile makes two dated docs **true again** that
  `76114cc` had silently invalidated: `FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md:267`
  ("`ep_advance_brief.py` … num_predict 750→1000") and `:273` ("Modelfiles
  … matching `num_predict` parity"), and `PHASE4_VALIDATION_2026-08-16.md:52`
  ("num_predict matching each call site's") — call site
  `ep_advance_brief.py:986` `"num_predict": 1000` == Modelfile `1000`. No
  other doc under README.md, CLAUDE.md, `docs/`, `src/ingest/README.md`,
  `src/shared/watchlist_README.md` quotes an ep-advance `num_predict` value
  or the closed-set venue rule (only `LIVE_STATE_CHECK_2026-08-1[78]`
  records). CLAUDE.md's brief-model description ("`FROM phi3:mini`", SWA
  denylist, 200 s smoke promotion) is unaffected. **Nothing to update.**
- The sweep doc's `<!-- SCORECARD_ADDENDUM -->` (`:198`) is still empty;
  today's tally from the rebuilt model is unchanged from pass 5 (3 LLM
  briefs 03:18/04:06/05:11, 6 repetition-loop discards 01:30–10:07).

### Live state moved since the fifth pass (not from this commit)

- **`ingest-stdds` + `ingest-tfms` are stopped by design, not broken.**
  `thermal-ingest-guard` tripped **tier 1 on load** at 10:56:59
  (`load1=10.98`, temp only 66.1 °C, threshold `THERMAL_GUARD_TIER1_LOAD=10.0`,
  `TIER1_FEEDS=tfms,stdds`) → both units `inactive (dead)` since 10:56:58,
  clean exit 0; `podman ps` = **34** containers (was 37: these two + the
  `runner-demo` crash-loop, which died again 11:08:59). Resume requires
  load < 6.0 **and** temp < 65 °C held (11:12: load1 4.16, 62.8 °C — should
  clear on its own). `scheduled-ingest-restart.sh` logs a WARN pair every
  2 min for the two while they're down (cosmetic). Behaviour is what
  `INFRA_MAP.md:170` / `ALERT_REFERENCE.md` / `GUARDRAILS_JUSTIFICATION.md`
  describe — not drift, but a doc reader expecting "7 ingest containers up"
  should check the guard state first (`skill-state/thermal_ingest_guard_state.json`).
- **New failed unit:** `corporatetraveldc-concierge-travel-daily-watch`
  failed 10:54:32 — `requests ReadTimeout host.containers.internal:80
  (read timeout=15)` after 24 min; unrelated to this commit (poller image
  `build-date=20260818T062650Z`). 118 units loaded / **1 failed** (this
  one) once the sweep cleared.
- **ep-advance 10:30 run** still `activating` at 11:13 (43 min): first
  `/api/generate` 200 at 10:56:37, model showing `Stopping...` in
  `ollama ps` at 11:13, no result logged yet — do not score it. The
  brief-fallback-monitor's 10:50 fire (first live use of `4078706`'s
  cause-agnostic text) sent MAX priority as predicted (`ep-advance …
  consec_fb=5 fb=5/6`; ops-brief `consec_fb=0 fb=2/6`).
- Unchanged: `:8000` 200, `:8001` 200, `:8083` openapi 200, `:8005`
  refused; core images `web 2eddb421694f` / `poller 2500e795979c` /
  `pusher 363961604403` / ingest `6e867f274783` (02:27 build); tests
  **17 failed / 144 passed** (3.6 s, same 17); 21/21 models;
  `DRIFT_AUDIT_2026-08-16.md:3-6,14` and `RUST_REWRITE_ASSESSMENT_2026-08-16.md:6`
  still as flagged.

**Bottom line (this pass): `dac7954` invalidates nothing — it closes drift
items 1 and 5, restores the accuracy of the 08-16/08-17 sweep docs it had
contradicted, and needs no model rebuild (live ep-advance already matches).
The only open manifest item is the one this addendum itself creates: re-sign
+ commit this file with the manifest pair. Everything else on the operator
to-do from earlier passes is done; watch the ep-advance 10:30/11:30 results
and the thermal guard's tier-1 auto-resume.**

---

## Addendum — seventh pass, 12:59–13:02 EDT, against HEAD `ff3b0f0` (delta only)

HEAD is `ff3b0f0` (12:58:52, "Exclude docs/LIVE_STATE_CHECK_*.md from the
integrity manifest -- informational, not security-relevant"), 15 ahead of
`origin/main`. One file: `scripts/sign-manifest.sh` (+14/−1) — a comment
block plus one regex change on the `git ls-files … | grep -zvE` filter, which
now drops `docs/LIVE_STATE_CHECK_[0-9-]+\.md` alongside `MANIFEST.sha256`/`.asc`.
No runtime code path, unit, container, Modelfile, or topic touched. Checked
~40 s after the commit. Nothing staged, committed, or changed live by me;
working tree = this file + the operator's post-commit manifest pair.

### What this commit changes live

- **It is live already, and already used.** `sign-manifest.sh` is host-side,
  no container/baked copy. The operator re-signed at **12:59:05** (13 s after
  the commit — `MANIFEST.sha256`/`.asc` are modified-uncommitted in the tree,
  signed by `419A864C…3159`, good signature). Manifest **678 → 671 entries**:
  the seven `LIVE_STATE_CHECK_2026-08-1[2-8].md` entries are gone (0 remain),
  `scripts/sign-manifest.sh` re-hashed to `ea62f428…`. `verify-manifest.sh`
  → **OK, 671 files** at 12:59:33 — and, the point of the change, it is
  **still OK after this addendum was appended** (re-run below), which is the
  first `LIVE_STATE_CHECK` edit today that has not broken the manifest.
- **Pass 6's "series-wide lesson" is now void:** "every `LIVE_STATE_CHECK_*`
  addendum written after the file has been signed once is a manifest break
  until re-signed" was true from pass 6 (11:14) until 12:59:05 and is no longer
  true. Same for the "open manifest item this addendum itself creates" in
  pass 6's bottom line — closed by this commit + re-sign, not by a re-sign of
  the doc. Pass 4's original claim ("`docs/` isn't in the manifest") is still
  wrong in general (60 `docs/` entries remain) but is now accidentally right
  for this one file family.
- Scoped-verify side effect, benign: `verify-manifest.sh docs/LIVE_STATE_CHECK_2026-08-18.md`
  now says "matched nothing in the signed manifest -- refusing to trust it".
  Nothing calls a scoped verify on a doc (`llm.py` verifies the skill file +
  Modelfile; entrypoints run the collective check), so no consumer is affected.
- `corporatetraveldc-integrity-sweep.service` is `failed` at this writing —
  from its **12:57:42** fire, i.e. 70 s *before* the commit, when the tree
  legitimately had 2 mismatches (this file + the edited `sign-manifest.sh`).
  Not caused by the commit; expected to clear on the 13:12:42 fire (manual
  verify already passes). `build-models.sh` GUARD-0 and host-side `llm.py`
  are unblocked as of 12:59:05.

### Docs check for this specific change

- README.md `:433-435` and CLAUDE.md `:58-61` describe the gate as covering
  "code changes"/"tracked code" — still accurate; neither enumerates
  `docs/` coverage, so nothing to change.
- `sign-manifest.sh`'s own header (`:3` "whole-repo-tree integrity
  manifest", `:37` "true whole-repo-tree coverage") and
  `SMOKE_TEST_HARNESS_2026-08-17.md:31` ("GPG-signed whole-repo-tree
  integrity manifest") are now "whole tree minus one dated-doc family" — the
  new comment block at `:50-62` states the carve-out and its reasoning right
  under those lines, so this is a wording nit, not a mismatch. Not edited.
- `docs/COMPLIANCE_SECURITY.md` has no "Signed Manifest Integrity" section
  (its own `:56-61` housekeeping note already records that
  `sign-manifest.sh:5` cites a heading that doesn't exist) — pre-existing,
  unchanged by this commit, still open.
- Dated records that described the *old* behaviour stay correct as history and
  now read as the motivation for this commit: `PENTEST_CLEARANCE_CHECK_2026-08-13.md:55`
  ("STILL a benign whole-tree miss … `docs/LIVE_STATE_CHECK_2026-08-13.md`"),
  `FABLE_TIMING_ARTIFACT_SWEEP_2026-08-17.md:31` (GUARD-0 blocked on
  `LIVE_STATE_CHECK_2026-08-17.md`), and passes 4–6 above. That failure class
  cannot recur for this file family.
- The commit comment's claim about `scripts/post-commit-doc-verify.sh`
  verified: it exists (2026-08-12), the installed `.git/hooks/post-commit` is
  byte-identical to it, and its prompt (`:52-57`) does hard-rule
  "DO NOT commit or stage anything". `PHASE4_VALIDATION_2026-08-16.md:246-251`
  ("sign-manifest.sh hashes `git ls-files --cached --others
  --exclude-standard`") is still true — the exclusion is a post-filter on
  that list.
- `src/ingest/README.md`, `src/shared/watchlist_README.md`: no manifest
  claims. **Nothing to update.**

### Live state moved since the sixth pass (not from this commit)

- **Thermal guard tier-1 auto-resumed:** `ingest-stdds` + `ingest-tfms` are
  `Up 2 hours` (~11:20 restart) on the same ingest image `6e867f274783`;
  `podman ps` = **38** (34 + the two + `runner-demo` back + one more).
  Load1 4.47 at 13:00. The 7-ingest-container claim is true again.
- **ep-advance is producing LLM briefs again:** the 10:30 run finished at
  **11:24:51** and the 11:30 run at **12:34:03**, both `brief generated via
  Ollama/corporatetraveldc-pi5-ep-advance:latest` (no fallback, no
  repetition-loop discard logged) — first back-to-back LLM successes on the
  restored `num_predict 1000` model. The 12:30 run started 12:34:03 and is
  still `activating` at 13:00; 12:00 ops-brief `deactivating`. Watch 13:30
  before calling the streak trend rather than luck.
- Failed units: `integrity-sweep` (transient, above) and
  `corporatetraveldc-transport-pattern-digest` (12:53:10, systemd
  `timeout` → SIGKILL after 28 min wall / 820 M peak on the 06:26 poller
  image — Ollama-slot contention, same class as the earlier long briefs;
  unrelated). `concierge-travel-daily-watch` from pass 6 has cleared. 118
  units loaded.
- Unchanged: `:8000` 200, `:8001` 200, `:8083` openapi 200, `:8005` refused;
  core `web 2eddb421694f` / `poller 2500e795979c` / `pusher 363961604403`;
  21/21 models; tests **17 failed / 144 passed** (3.4 s, same 17).

**Bottom line (this pass): `ff3b0f0` invalidates nothing in README.md,
CLAUDE.md, `docs/`, or the two sub-READMEs. It retires the one recurring
false-positive this file series has been generating (pass 6's manifest
warning is superseded), and the operator's 12:59:05 re-sign is already live
— the only follow-up is committing the modified `MANIFEST.sha256`/`.asc`
pair (and this file) whenever convenient; nothing is blocked in the meantime.**

## Addendum — eighth pass, 13:30–13:37 EDT, against HEAD `61ad611` (delta only)

HEAD is `61ad611` (13:29:49, "Template real FDPS GUFIs in fdps_fixm30_real
fixtures for public mirror"), now **0 ahead of `origin/main`** (the operator
pushed; pass 7's "15 ahead" is history). One file: `scripts/scrub-public-tree.py`
(+60): 23 `SUBSTITUTIONS` entries mapping the real FAA GUFIs in
`tests/ingest/fixtures/fdps_fixm30_real/*.xml` to
`AAA01234-5678-9abc-def0-0000000000{04..26}`, plus the same 23 placeholders
added to `ALLOWED_UUIDS`. No runtime code path, unit, container, Modelfile,
or topic touched. Nothing staged, committed, or changed live by me; nothing
pushed to `public`.

### ⚠ Working-tree event during this pass (not from the commit — read first)

At **13:33:05** something in another session ran `git stash` (reflog:
`reset: moving to HEAD`, `stash@{0}: WIP on main: 61ad611`), then a
`git pull --rebase` no-op onto `origin/main` at 13:33:31 — the exact
`git stash; git pull; git stash pop` sequence in `scripts/session-restore.sh`
Step 1, except the **pop never happened** (checked repeatedly to 13:35:44,
no git process running). The stash holds three files: the operator's
**13:30:40 re-signed `MANIFEST.sha256`/`.asc`** (the only copy with the
post-commit `scrub-public-tree.py` hash `26799b8d…`) and the **398 lines of
passes 5–7** of this file. Consequences at 13:33:50: `verify-manifest.sh`
→ **INTEGRITY FAILURE** (`scripts/scrub-public-tree.py: FAILED` — HEAD's
committed manifest still carries the pre-edit hash `136c79c3…`), so
host-side `llm.py`, `build-models.sh` GUARD-0 and the 13:42:43
`integrity-sweep` fire were all about to (re-)fail, and this file was back to
352 lines.

**What I did (working-tree writes only; no `git stash pop/drop/apply`, no
index, no branch):** after confirming the stashed manifest verifies cleanly
against the current tree (`sha256sum -c` all 671 OK, GPG good signature),
I wrote the three files back from `stash@{0}` with `git show
stash@{0}:<path> > <path>` — byte-identical (`git diff --quiet stash@{0} --
<3 files>` passes), `verify-manifest.sh` → **OK, 671 files** at 13:35:50.
`stash@{0}` is **still there, untouched**. Follow-up for the operator:
`git stash pop` will now *refuse* ("local changes … would be overwritten")
because the tree already contains everything in it plus this addendum —
the correct action is `git stash drop` after a glance at `git stash show -p
stash@{0}` (it should be a no-op against the tree except this addendum).
Do not force-pop; that would only fight the identical content.

### What this commit changes live

- **Nothing.** `scrub-public-tree.py` is host-side only — called by
  `scripts/push-public.sh:42` (itself invoked by `push-and-sync.sh:44` on
  an explicit `[y/N]`); no Containerfile/`.container`/`.service` copies it
  (`ultrafeeder.container:42` merely mentions it in a comment). Core
  `web 2eddb421694f` / `poller 2500e795979c` / `pusher 363961604403`
  unchanged; 21/21 models; `:8000` 200, `:8001` 200, `:8005` refused.
- **Manifest:** the file *is* in the manifest (`MANIFEST.sha256:371`), so
  the 13:23:37 edit broke verification until the operator's 13:30:40 re-sign
  (51 s after the commit) — same benign pattern as passes 5 and 7. The
  `integrity-sweep` failure at 13:27:43 (`scrub-public-tree.py: FAILED`) is
  from that window, 2 min *before* the commit; the stash episode above then
  re-broke it 13:33:05–13:35:50. Manual verify passes now; the 13:42:43 fire
  should clear it.
- **The commit's claim independently reproduced:** ran the scrub against
  `HEAD^{tree}` (`9b59df9b…`) → exit 0, scrubbed tree `247d4408…`, 38 DROPs,
  **0 unrecognized UUIDs / emails / IPv4 / forbidden literals**. Cross-check:
  the 9 fixture files contain exactly **23 distinct UUIDs and those 23 = the
  23 new `SUBSTITUTIONS` keys** (no fixture GUFI unmapped, no mapping
  unused). In the scrubbed tree `AH_sample_9.xml` carries only
  `AAA01234-…-000000000004`; `scripts/scrub-public-tree.py` itself is
  dropped (only `scrub-public-tree.example.py` ships), so the real GUFIs
  in the mapping table do not leak either. The 6 tests in
  `tests/ingest/test_fdps_fixm30_real_samples.py` **pass on the scrubbed
  copy too** (extracted `247d4408…` to a temp dir; only `:78` looks at the
  GUFI and it asserts truthiness) — the public mirror will not ship a red
  test. Private suite unchanged: **17 failed / 144 passed** (5.6 s).
- `public/main` is still `e3db2d2` (2026-08-14 tip, last fetched 08-16) —
  it has **no** `fdps_fixm30_real/` files yet; the mirror is now unblocked
  but has not been synced. Not drift; noting so nobody reads "for public
  mirror" as "on the public mirror".

### Docs check for this specific change

- README.md, CLAUDE.md, `src/ingest/README.md`, `src/shared/watchlist_README.md`:
  none mention the scrub script, `ALLOWED_UUIDS`, or the real fixtures.
  **Nothing to update.**
- Every `docs/` mention of `scrub-public-tree.py` is generic ("two-layer
  discipline", "the backstop for the public repo" — `COMPLIANCE_SECURITY.md:44`,
  `DEMO_DATA_ISOLATION_PLAN_2026-08-13.md:86`, `PENTEST_CLEARANCE_CHECK_2026-08-13.md:242`,
  `PHASE4_*_2026-08-16.md`, `LIVE_VALIDATION_AND_PENTEST_2026-08-13.md:192`)
  and still true — this commit is that discipline working as documented
  (layer 2 refused, layer 1 was extended). `TAILNET_MIGRATION_INVENTORY.md:119-122`
  quotes the script by line number (`:84`, `:87`, `:235`, `:237`); those
  numbers were already stale before today and the +60 lines shift the
  latter two again — pre-existing, dated-doc, not edited.
- **One nit, real but sub-threshold:** `tests/ingest/test_fdps_fixm30_real_samples.py:18-19`
  ("Fixtures here … are UNMODIFIED copies of real captured messages") and
  `SMOKE_TEST_HARNESS_2026-08-17.md:124` ("copied unmodified") are true in
  this repo but the docstring ships to the public mirror, where the same
  fixtures now carry the very `AAA01234-…` sequential-GUFI shape that the
  same docstring (`:7-8`) uses as the tell for "clearly not real captures".
  A public reader gets a slightly self-contradicting comment; no test or
  behaviour depends on it. Left as-is — a one-line "GUFIs are templated on
  the public mirror; see scrub-public-tree.py" in that docstring would close
  it, and it's a code file (manifest-covered), so it belongs in an operator
  commit + re-sign, not a doc-check edit.
- `sign-manifest.sh` / pass-7 carve-out: this file family is still outside
  the manifest, so appending this addendum did not break verification
  (re-run below).

### Live state moved since the seventh pass (not from this commit)

- **ep-advance streak is three:** 12:30 run finished 13:11:53 (`brief
  generated via Ollama/corporatetraveldc-pi5-ep-advance:latest`, logged
  13:08:00) — pass 7's "watch 13:30" is answered for the LLM path; the 13:30
  run is `activating` at 13:35. `ops-brief` inactive between runs.
- `podman ps` = **35** (pass 7: 38); `ingest-stdds`/`ingest-tfms` still
  `Up 2 hours`; `runner-demo.service` `activating` (its `:8005` refusal is
  the same as pass 7). 118 units; failed = `integrity-sweep` (transient,
  above) + `transport-pattern-digest` (12:53, unchanged from pass 7).
  Load1 5.21 at 13:33.

**Bottom line (this pass): `61ad611` invalidates nothing in README.md,
CLAUDE.md, `docs/`, or the two sub-READMEs, and its "0 unrecognized UUIDs"
claim reproduces exactly (23/23 mapped, scrub exit 0, public-copy tests
green). The only thing that needs a human is the orphaned `stash@{0}` from
13:33:05: the working tree already holds its full content (manifest verifies
OK again as of 13:35:50), so it should be dropped, not popped, and the
modified `MANIFEST.sha256`/`.asc` pair + this file committed whenever
convenient.**
