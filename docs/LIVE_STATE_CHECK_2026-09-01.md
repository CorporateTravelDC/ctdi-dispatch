# Live-state check 2026-09-01 post-d5c8a5d (build pip-timeout tolerance + auto maintenance-window + weekly-compile transport-patterns scan)

Checked ~21:45–22:00 EDT, immediately after commit `d5c8a5d` (21:44 EDT).
Scope per the standing drift-check brief: does anything README.md /
CLAUDE.md / docs/ / src/ingest/README.md / src/shared/watchlist_README.md
currently claim that this commit invalidated, verified against the live
box, not against the docs themselves.

Prior-knowledge check (second-brain, before deriving anything): the most
recent doc-drift note is `corporatetraveldc/01-Sources/manual/20260831T102922Z.md`
(post-commit 2999633) — its findings (GUARDRAILS ingest CPUWeight 30→5000
drift, etc.) are about a different commit's surface and are NOT re-derived
here. No prior note covers pip timeouts, the maintenance-window scripts,
or the weekly-compile input set (searched several phrasings; all empty).

## F1 — REAL BUG (new, not this commit, found live during this check): `ep-advance-venues` crashes on every fire — `TypeError: generate() got an unexpected keyword argument 'top_p'`

`corporatetraveldc-ep-advance-venues.service` failed its first **genuine**
06:10 ET fire today (exit 1 at 06:12:36) — journal shows a clean Python
traceback, not an infra problem:

- `src/poller/skills/ep_advance_venues.py:63` passes `top_p=0.9` and
  `:65` passes `max_retries=0` into `common.llm.generate()`, whose
  signature (`src/common/llm.py:831`) accepts **neither** — `max_retries`
  was deliberately removed in the 2026-08-30 Ollama-gutting pass, and
  `top_p` never existed there (it lives in persona dicts in
  `common/personas.py`, not in `generate()`'s signature). Python reports
  only the first bad kwarg, so fixing `top_p` alone would just surface
  the identical TypeError on `max_retries` next.
- This is the exact bug class the 08-30 gutting pass fixed in
  `ep_advance_brief.py` (it passed `max_retries=0` too) — but
  `ep_advance_venues.py` was created the **next day** (08-31 EP-advance
  split) and re-introduced it. Import-testing can't catch it (call-time
  error); only a real fire could, and the first real fire did.
- This **invalidates CLAUDE.md's classification** of the 08-31 13:54
  venues failure as "harmless one-time cosmetic failure" (catch-up fire
  against a pre-rebuild image). That 13:54 failure was indeed cosmetic,
  but the entry's implication — next real fire is fine — is wrong: the
  skill fails 100% reproducibly at the LLM call and has **never produced
  a venue advisory**. Will fail again tomorrow 06:10 ET unless the two
  kwargs are removed + signed + poller image rebuilt before then.
- Counterpart, positive: CLAUDE.md flagged the whole EP-advance split as
  "not yet independently verified end-to-end." The **hourly half now is**:
  `corporatetraveldc-ep-advance.service` shows 12+ consecutive clean
  "Finished" runs today (09:52 through 20:52 EDT), all on the split code.
  The verification debt is now confined to the venues half, which is
  blocked on this bug.

Not fixed in this pass — a src/ edit would sit unsigned and trip the
integrity gate on every timer unit until the operator signs; flagged
instead (and persisted to the second-brain).

## F2 — REAL deployment lag (live-state, not doc text): none of today's three commits run in any long-running container; HEAD is in no image at all

Verified by image ID + in-container grep, not by assumption:

- `:latest` for all five images was built **13:16–13:18 EDT today**
  (17:16–17:18 UTC) — consistent with commit `de0f53d` (13:09 EDT). It
  contains `de0f53d` (opensky) and `8b9b359` (FIDS OOOI fix) but **not**
  `d5c8a5d` (verified: 0 hits for `transport_pattern_files` in the
  image's `second_brain_weekly.py`; no `PIP_DEFAULT_TIMEOUT` in env).
- The **resident poller container** (`systemd-corporatetraveldc-poller`,
  up 26 h) runs image `7b99e7756f8a` built **08-31 19:23 EDT** — it
  predates all three of today's commits and runs `python3 -m poller.main`,
  the exact file `8b9b359` fixed. Verified: 0 hits for the fix's
  `wheels-down` comment in the running container vs 1 in `:latest`. **The
  FIDS "Landed"→`in` OOOI bug (flights swept at touchdown, `on` phase
  never recorded) is still the live production behavior ~11 hours after
  being fixed.** web/pusher/ingest*/amtrak similarly all predate today's
  builds (started 08-30/08-31); for those the gap is lower-stakes
  (de0f53d's `db.py` change is additive DDL).
- `podman-auto-update.timer` is **disabled**, so the
  `io.containers.autoupdate=local` labels on these quadlets do nothing —
  README's manual "rebuild + restart" procedure (README.md ~553) is the
  only refresh path, and the restart half wasn't run after the 13:18
  rebuild. Timer-fired oneshot skills DO pick up `:latest` per fire, which
  is why they're on newer code than the resident poller.
- **A deploy appears to be in progress from another session**: a bare
  `podman build -t localhost/corporatetraveldc-web:latest -f
  Containerfile.web .` has been running since **20:33 EDT (1h16m+ and
  counting)** — started 14 s after the Containerfile pip-env edit, and a
  dangling image layer from it already carries the new
  `ENV PIP_DEFAULT_TIMEOUT` instruction. Nothing here was touched or
  "helped" by this check; documented only.
- Deadline notes: (a) venues timer fires 06:10 ET tomorrow (will fail
  again regardless — F1 is a source bug present in every image);
  (b) `second-brain-weekly` fires **Sun 2026-09-06 18:15 ET** from
  `:latest` — the transport-patterns scan only makes that fire if a
  poller rebuild off `d5c8a5d` lands before then.

## F3 — Doc drift from this commit: weekly-compile input-set claims

1. `docs/SECOND_BRAIN_STATUS.md:414–416` — "`second_brain_weekly.py` --
   ... Reads the past 7 days of `01-Sources/daily/*.md` over WebDAV."
   The real input set is now **three** directories: `01-Sources/daily/` +
   `04-Syntheses/daily/` (added 2026-08-06, so this line was already one
   directory stale) + `01-Sources/transport-patterns/` (added by this
   commit). This commit widens existing drift rather than creating it.
   (Same bullet's "Ollama-synthesizes" wording is pre-existing cutover
   staleness, out of this commit's scope.)
2. `docs/DEDICATED_MODELS_PLAN.md:247` — "Current state:
   `second_brain_weekly.py` reads the current week's 7 daily notes" —
   same axis, in a dated plan doc; the mini-RAG plan's actual premise
   ("no visibility into prior weeks") is still true, so low-stakes.

Neither fixed here (both files are signed/tracked; text-only edits would
sit unsigned same as F1 — flagged for the next signing pass instead).

## F4 — Checked, NOT drifted

- **llama-chat CPUWeight baseline**: live `CPUWeight=9000` right now,
  matching the tracked unit (`systemd/corporatetraveldc-llama-chat.service:32`)
  and every doc that states 9000. The new suppression is `--runtime`-only
  and was verifiably not left stuck engaged (the on/off scripts' central
  risk). Docs claiming the 9000 baseline stay accurate; the suppression
  is a transient exception documented in the scripts' own headers.
  One observation, not drift: the in-flight bare `podman build` (F2)
  **bypasses** `build-images.sh` and therefore gets no suppression —
  llama-chat was at weight 9000 with two llama-server processes at ~150%
  CPU each (load avg 21 on 4 cores) while it ground through its 76th
  minute. Live confirmation of exactly the contention the commit
  describes; the protection only covers wrapper-invoked builds.
- **README.md "After any code change"** (~529/553): still the correct
  procedure; the maintenance-window hook is additive inside
  `build-images.sh`, no README claim invalidated. (README's
  "dedicated Ollama models / build-models.sh" first-time-setup text is
  pre-existing cutover drift, not this commit's.)
- **`src/ingest/README.md` and `src/shared/watchlist_README.md`**: this
  commit's only contact with their subject areas is the ENV-only
  `Containerfile.ingest` addition — no claim in either file touches pip
  behavior or build env. Unaffected.
- **The commit's own factual claims verified**: vault really does contain
  both filename shapes `_file_date()` now handles (hyphenated digest
  stamps like `01-Sources/transport-patterns/2026-07-29T1625.md`,
  unhyphenated `remember_text` stamps like `manual/20260830T030623Z.md`);
  `transport_pattern_digest.py` really writes to that directory.

## F5 — Expected/self-resolving, observed and already resolved or known

- `corporatetraveldc-integrity-sweep.service` failed 21:35 EDT (unsigned
  working-tree window between the 20:32 Containerfile edits and the 21:43
  signing) and then **succeeded 21:50:25** ("all 870 files match") —
  standing pattern, already self-resolved, listed here only so nobody
  re-derives it.
- `corporatetraveldc-transport-pattern-digest.service` failed again 12:36
  EDT (SIGKILL after ~12 min) — the chronic known-bad (08-29 P3-2, zero
  successful runs ≥14 days), unchanged. One interaction with this commit
  worth naming: until the digest is fixed, the new
  `01-Sources/transport-patterns/` scan mostly serves **ad-hoc**
  `remember_text` notes (the commit's stated motivation) — the last
  digest-written file in that directory is 2026-07-30.

## Net

Two real findings persisted to the second-brain (F1 bug, F2 deployment
lag incl. the not-yet-live FIDS OOOI fix); one modest doc drift (F3)
queued for the next signing pass; everything else checked clean. No
files staged or committed; this file is the only working-tree addition.
