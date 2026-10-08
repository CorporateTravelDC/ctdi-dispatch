# Security review 06 — architecture outage, provenance gaps and the LADD lookup (2026-10-08)

> **Review record. Frozen once committed.** Part of the security review chain (`README.md` in this directory). It follows `2026-10-07-05-reproducible-builds.md` and records the first deployment of that pass and what it exposed. Review 05 is not edited. Its statements are corrected here and, with dates, in the canonical documents `docs/REPRODUCIBLE_BUILDS.md` and `docs/LADD_CUI_HANDLING.md`.

## 1. Outage: four production images built for the wrong architecture

**What happened.** Review 05 included an amd64 portability build of the web image under emulation on this arm64 host. That build pulled the **amd64 member** of the `python:3.13-slim` index into local storage. The production builds that followed name the same **index** digest, and podman resolved it to the locally cached member, so the rollout produced amd64 images for **ingest, poller, pusher and web**. Under emulation on this 16 KiB-page kernel, psycopg's bundled native libraries do not load, so those containers could not reach Postgres.

Amtrak-tracker, demo, runner, acars-watcher and contact were built as arm64 and were unaffected. The reason one base digest gave both architectures in the same run was not determined.

**Timeline** (UTC / Eastern):

| Time | Event |
|---|---|
| 2026-10-07 21:12Z / 17:12 ET | amd64 portability build (review 05 testing) caches the amd64 base |
| 23:36Z / 19:36 ET | locals-only rollout (Relay 2) starts from signed `3c9f296` |
| ~23:54Z / 19:54 ET | ingest (7 units) restarted on an amd64 image |
| 00:00Z–01:20Z / 20:00–21:20 ET | **no FAA flight-data rows written** (`flight_events` per 10 minutes: 2,211 in 23:50Z, then none until 01:20Z). The other SWIM feeds use the same ingest image and very likely lost the same window; not measured separately |
| ~00:43Z / 20:43 ET | web restarted on its amd64 image: `/healthz` 500 |
| ~01:13Z / 21:13 ET | found while investigating a LADD import; cause identified; the agent's own rollback was blocked by the session's permission check, and the operator ran it |
| ~01:20Z / 21:20 ET | operator relay retags the four to their arm64 `:previous`, restarts them (`/healthz` 200), removes the amd64 base images and rebuilds the four as arm64 |

**Impact.**
- The dispatch web service (`/healthz`) returned 500 for about 40 minutes. The public websites are served separately and were not measured.
- About 80 minutes of SWIM ingestion were lost.
- One live OOOI watch on a commercial flight missed its gate arrival, which fell inside the gap; it stayed at ON (23:57:55Z, surface surveillance).

**Why the controls did not stop it.**
- The receipt recorded `"architecture": "amd64"` faithfully, but nothing compared that with the host.
- The deploy gate checks that a receipt exists, not that the image runs.

**Fix:**
- Every `podman build` in `build-images.sh`, `stack-refresh.sh` and `serialized-rollout.sh` now passes `--platform linux/<host arch>`.
- `provenance.py record` refuses an image whose architecture differs from the host's (`BUILD_EXPECTED_ARCH` overrides for a cross-build host), so such an image is held like a failed build.
- `build-policy.py` fails any build command without `--platform`.
- Tests: `Review06Incident`.
- Operating rule, written into the canonical document: run cross-architecture tests on another host, or remove the foreign base images afterwards.

## 2. Provenance gaps found by the first full deployment

| Finding | Effect | Fix |
|---|---|---|
| `build-images.sh` builds the runner in a separate block that recorded **no receipt** | after the recovery relay, the runner tag pointed at an unrecorded image; the sweep **correctly** reported FAILED | the block records provenance, with labels; `build-policy.py` now requires a provenance record after every build command. The image was built from signed `3c9f296` and was recorded after the fact |
| The contact API image (website repository) was a documented exception, but the verifier fails an unpinned base | the sweep FAILED every 15 minutes (its code sends a priority-5 push on each failure; delivery not checked), from the first rebuild after review 05's deploy until this fix | the website repository now has its own `build/policy.toml`, a hashed lock seeded from the running versions (23 packages, identical), base by digest and the apt snapshot. The same tooling runs unchanged with `--root`, a working test of the repurposeability claim. Trial build: arm64, `pip check` clean, imports OK |

## 3. LADD

**Found by the operator's new rule.** After every LADD upload, prove that the import took and held with a random tail inquiry through the real lookup, then shred the source files. The first check on the 2026-10-06 import:

| Sample (random, counts only) | Reported as LADD |
|---|---|
| US registrations, queried as stored (`N…`) | **0 / 200** |
| US registrations, queried in registry form (no N) | **0 / 200** |
| Flight-ID strings starting with N | **0 / 100** |
| Other identifiers | 200 / 200 |
| Recorded removals | 0 / 4 (correct) |

**Cause.**
- The CUI files list US registrations with the leading N, and the import stores them as given (24,034 of 73,479 entries).
- `db.faa_is_ladd()` stripped the N from every input before an exact match, so it matched none of them. It also stripped the N from flight IDs.
- A live case the same night: a GA aircraft on the list was reported "not LADD" by the lookup and confirmed listed only by a direct table check.

**Exposure.** The `ladd` flag on the registry lookups (`faa_lookup_by_n_number` / `faa_lookup_by_hex`, behind `/api/v1/aircraft/…` and the watchlist) read `false` for listed US aircraft, for Tier 1+ callers.
- **Not affected:** Tier 0 (always `false` by design) and the demo scrub, which matches raw tokens against the table.
- **Not found:** no code suppresses anything on this flag. It is reported only.

**Fix:**
- `db.ladd_lookup_keys()` gives the identifier as given plus the other registration form. An N followed by a letter is a flight ID and is never stripped.
- Tests: `tests/common/test_ladd_lookup_and_fdps_tail.py`.

**Second finding.** The poller's registry fetcher still called the FAA's discontinued public LADD download and **full-replaced** the table whenever it parsed anything. That would have wiped the CUI-sourced list and skipped every recorded removal.
- It failed harmlessly only because the endpoint is gone.
- The call is retired, kept as `SUPERSEDED` comments.
- A test asserts that the fetcher cannot write the table.

**Procedure.** `docs/LADD_CUI_HANDLING.md` now reads import → random tail inquiry check → shred. Twelve CUI source files that had accumulated since 2026-08-25 were shredded by the operator on 2026-10-08.

## 4. FDPS lookup for registration callsigns

**Finding.** `db.get_flight_plan_by_callsign()` accepted only airline-shaped callsigns (a three-letter airline code and a number). The FDPS parser stores every callsign by position (`cs[:3]` / `cs[3:]`), so a GA registration flying as its own callsign was never found.
- Watch adds reported `FDPS:N`.
- The OOOI path that may assert ON/IN, SWIM, could not see GA tails.

**Not related to LADD.**

**Fix:** a positional fallback when the callsign is not airline-shaped. Tested, along with unchanged airline behaviour.

## 5. Real aircraft identifiers in the repository, and the synthetic Q registry

**Operator direction (2026-10-08).** Use the synthetic `Q` registry, which no state registers aircraft under, for every illustrative, GA or privacy-respected tail number, the way RFC 5737 and RFC 2606 values stand in for addresses and domains. Make it the default.

**What a sweep of the tracked text found** (counts only; no identifiers in this record):

| Finding | Count |
|---|---|
| Real registrations (currently FAA-registered or LADD-listed) in fixtures, code comments, tests and a skill doc | 46 aircraft (101 occurrences), **9 LADD-listed** |
| Their real ICAO hex codes alongside them | 10 aircraft (17 occurrences) |
| Unregistered placeholder registrations, any of which could be issued later | 11, plus one format example |
| Placeholder hex codes that are in fact assigned to real aircraft | 6 |
| The live permanent flight watchlist, published to the public mirror since 2026-07-28, naming a **LADD-listed** aircraft and revealing which flights this deployment watches | 1 file |

Several of the LADD-listed values looked like placeholders: an illustrative-looking example in a test turned out to be a real, privacy-listed aircraft. That is the case for a reserved registry.

**Changes:**
- **Registrations and hex codes:**
  - every real or placeholder registration in tracked text is now a random `Q` registration, used consistently across files;
  - a real hex code became a random value unassigned in the FAA and OpenSky registry tables;
  - the mapping was kept only in the agent's scratchpad (owner-readable) and deleted.
- **Format examples:** where the US format itself is the subject, an impossible `N0…` form is used.
- **Parsers:** the FDPS GA-tail classifier, the TDLS registration extractor and the runner map search accept `Q` registrations, so synthetic fixtures exercise the GA code paths. Live traffic never carries one.
- **Public mirror:**
  - drops `watchlists/permanent_flights.json` and publishes `permanent_flights.example.json` instead;
  - the leak gate refuses any US-registration-shaped identifier in text and reports only a count. Binary bytes are skipped, because compressed data forms the shape by chance; OOXML text parts are still scanned.
- **Guard:** `tests/scripts/test_synthetic_identifiers.py` (4 tests).
- **Convention:** documented in `README.md` "Synthetic aircraft identifiers".

**Not undone:** the public mirror's git history still contains the earlier versions of these files. Removing them needs a rewrite of the public repository's history, which is an operator decision.

## 6. Also

- An invalid escape sequence in `src/web/routes/agent_gateway.py` (a JavaScript regex inside a Python f-string) was written with doubled backslashes. The rendered page is unchanged; the warning would have become an error in a future Python.
- **Rule breaches by the agent, disclosed:** two `python3 -c` invocations (a version read; an empty `pass`), both read-only.

## Validation

- **New tests:** `tests/common/test_ladd_lookup_and_fdps_tail.py` (5), `Review06Incident` (2), `tests/scripts/test_synthetic_identifiers.py` (4); build-integrity tests now 36, all passing.
- **Policy:** `build-policy.py` VERIFIED on both repositories.
- **Full suite, leak gate and live post-deploy checks:** in the deploy relay. After deploy:
  - the LADD random check must report every sampled US registration as listed;
  - a GA registration callsign's plan must resolve through `get_flight_plan_by_callsign`;
  - every running local image must verify.

## Status after this pass

- **Reproducibility:** the levels in `docs/REPRODUCIBLE_BUILDS.md` stand, with one new enforced property (build architecture) and the contact image moved under the policy. The incident shows a limit of digest pinning on one host: an index digest says which images are allowed, not which architecture a cache will hand back. The platform pin closes that.
- **LADD:** the lookup is correct for the stored forms. The CUI import is the only writer.
