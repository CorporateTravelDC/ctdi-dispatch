# Security review 09 — adversarial validation of provenance, deployment identity, agent authority and audit integrity (2026-10-08)

> **Review record. Frozen once committed.** Part of the security review chain (`README.md` in this directory). It follows `2026-10-08-08-public-history-secrets.md`. Earlier records are not edited.

## Baseline

| Item | Value |
|---|---|
| Commit | `2d8fcc9171d5c6d501923ecd264cf507feb4df19` (signed, status `G`), working tree clean |
| Host | Linux 6.18 aarch64 (16 KiB pages), podman 5.8.7, Python 3.14.8 (host), images Python 3.12/3.13 |
| Live state during the review | an operator-started full rebuild (`stack-refresh --weekly`) was running; this review did not build, restart, retag or write anything live |
| Isolation | provenance tests: a separate podman storage root and receipt store in a scratch directory, a throwaway git repository signed with a throwaway GPG key, base image copied in by `podman save`/`load`. Audit-chain tests: a disposable Postgres 16 container on a private loopback port, with a guard that refuses any database other than the disposable one. Agent-authority tests: the repository's own test harness (SQLite test backend, real ssh-keygen signatures), in a separate worktree |
| Live reads (read-only) | the app database role's attributes and grants, the checkpoint table row count, the sweep journal, the operator GPG keyring validity counts |

Method rule: every PASS below exercised the production decision function (`scripts/build/provenance.py`, `src/common/agent_gateway.py`, `src/common/governance.py`, `src/web/routes/agent_gateway.py`, `src/common/audit_chain.py`, the migration 0062 trigger) with controlled fixtures. Where a part was replaced, the row says so.

## A. Executive verdict

| Rubric area | Grade | Basis |
|---|---|---|
| Local-first architecture | not graded | not exercised in this pass |
| Security and agent governance | **A−** | every C test passed against the real gateway and governance code; caveat R09-3 (the shared database role is a superuser) |
| Reproducible builds (levels 1–3 as claimed) | **B+** | identity is content-addressed and enforced at the rollout gate and the sweep; gaps R09-5, R09-6, R09-7; level 4 is not claimed and was not tested |
| Regional repurposeability | **C+** | NOTAM zones, receiver position and Amtrak scope are settings; the SMES/surface airport sets, METAR stations and DC facility sets are code constants (documented in `docs/REGIONALIZATION.md` §2 as code edits) |
| Auditability and provenance | **C+** | edits, middle deletions, reordering and forks are detected; but concurrent writers fork the chain (R09-1), tail deletion is not detected (R09-2), a verifier error is reported as healthy (R09-4), and the app role can rewrite everything (R09-3) |

The mutable-tag concern from the earlier external review is **cleared** (section D).

## B. Test results

### Test A — image identity

| ID | Claim tested | Decision point | Method | Result | Outcome |
|---|---|---|---|---|---|
| A1 | a correctly built and recorded image verifies | `provenance.record`, `verify_image`, `verify_source`, `check_deploy` | isolated podman + signed fixture repo | all checks VERIFIED; `check-deploy` rc 0 | PASS |
| A2a | a tag re-pointed at an unrecorded image is caught | `verify_image` (id → receipt lookup; `by-name` lineage) | isolated | verify FAILED ("has NO receipt, but … was last recorded as …"); `check-deploy` rc 1 | PASS |
| A2b | a tag re-pointed at **another service's recorded** image is caught | same | isolated | verify VERIFIED, `check-deploy` rc 0; a container started from it reports UNVERIFIED at most | **FAIL** → R09-5 |
| A3 | receipt id ≠ image id | `verify_image` | isolated | FAILED (image id) | PASS |
| A4 | image without a receipt | `verify_image` | isolated | name previously recorded: FAILED; never-recorded name: UNVERIFIED, `check-deploy` rc 0 (documented pre-provenance allowance) | PASS (as documented) |
| A5 | stale receipt (older commit) | `verify_source` "current source" | isolated | UNVERIFIED, never VERIFIED, exit 0 | PASS |
| A6 | receipt field tampering | `verify_image`/`verify_source` | isolated, 10 variants | detected: image id, tree status, manifest digest, Containerfile digest (FAILED). **Not** detected: architecture, base-image digests, apt snapshot, builder, timestamps. `source_commit` nulled or unknown → UNVERIFIED (silent). Malformed JSON or missing `sbom` key → traceback, exit 1 (fails closed, aborts the whole run) | PARTIAL → R09-7, R09-8 |
| A7 | SBOM tampering | `verify_image` (+`--deep`) | isolated | file edited: FAILED; deleted: FAILED; edited **with** a matching receipt digest: VERIFIED shallow, FAILED `--deep` | PASS (shallow catches edits, deep catches consistent forgery; the sweep runs shallow — documented "with --deep") |
| A8 | rollback with the documented procedure | `verify_image`, `check_deploy` | isolated: `:previous` set from the old id as `serialized-rollout.sh` does, then `podman tag :previous :latest` | old image's own receipt found: id/SBOM/source VERIFIED, "current source" UNVERIFIED; `check-deploy` rc 0 | PASS |
| A9 | restart without rebuild; tag moved after start; running id ≠ tag | `verify --running` (`local_containers` reads each container's actual image id) | isolated, real containers | restart: VERIFIED; tag moved to rogue: tag row FAILED, running row VERIFIED, rc 1; container running a rogue id with a clean tag: running row FAILED, rc 1 | PASS |
| A10 | deployment enforcement contract | `serialized-rollout.sh` (`check-deploy` before each restart), `scheduled-integrity-sweep.sh` (`verify --running` every 15 min) | static + the A tests | prevention for rollout restarts only; detection (priority-5) for every other start path; `build-images.sh` has no gate (prints manual restart steps) — all as documented in `docs/REPRODUCIBLE_BUILDS.md` "Deployment identity". The gate reads the tag, then waits for load before restarting: a retag in that window is not prevented (operator-account only) | PASS (implemented contract) |

### Test B — receipt and SBOM trust

| Question | Answered by | Verified |
|---|---|---|
| source built | receipt `source_commit`; verifier checks it exists, its signature status, and that `MANIFEST.sha256` at it hashes to the receipt | yes, but signature status `U` (any key in the operator keyring, trust unknown) counts as signed → R09-6 |
| locked dependencies | dependency-file digests vs the commit | yes (A6) |
| base images / OS sources | receipt `base_images`, `apt_snapshot` | **not** cross-checked against the commit's Containerfile (A6) → R09-7; the Containerfile digest itself is checked, so the authoritative `FROM` is bound |
| platform | `record` refuses a foreign architecture (rc 1, nothing written; tested with `BUILD_EXPECTED_ARCH=amd64`) | yes at record time; the receipt field is not re-checked at verify time |
| artifact | content-addressed image id | yes |
| SBOM | digest in the receipt; `--deep` re-derives | yes (A7) |
| deployed / running | `check-deploy`, `verify --running` | yes (A9), except cross-service substitution (A2b) |
| anchoring | receipts are unsigned; trust = content-addressed id + signed commit; the store is operator-only (`0755`, owner-writable only, 0 group/world-writable entries). The doc puts the operator account out of scope | as documented |
| failure states | VERIFIED / UNVERIFIED / FAILED / NOT APPLICABLE | yes; exit 1 only on FAILED |
| failed recording stops the build | `build-images.sh` `die` (runner path included since review 06); `serialized-rollout.sh` exit 1; `stack-refresh.sh` holds that image. Refusals tested: foreign architecture, empty SBOM (rc 1, 0 entries written). Interrupted recording (SBOM written, receipt missing) → FAILED / `check-deploy` rc 1 | PASS |

Hash-locked resolution (levels 1–2), artifact provenance (level 3) and bit-for-bit reproducibility (level 4, not claimed) were kept separate; nothing here tests level 4.

### Test C — agent authority (12 tests, `tests/web/test_review09_agent_authority.py`, all pass)

| ID | Claim | Decision point | Result | Outcome |
|---|---|---|---|---|
| C1 | arguments cannot change the acting identity | `gw.authenticate` → `cx["account"]`; `_call` passes it to `board_insert`, `council_create` | forged `from`/`sender`/`account`/`requester` ignored; posts, council requester and `status` all report the token's account; a token on another slug is 401 (existing test) | PASS |
| C2 | a signature authorizes one exact request | `governance.resolve_signed` over `approval_canonical` v2 | A's signature on B: 403; deny signature used as allow: refused; editing command, requester, reasoning, kind or pattern after signing: refused; then the untouched request resolves | PASS |
| C3 | no replay | `resolve_signed` status check; OAuth code single use; `approved-exec` `O_EXCL` ledger (existing tests) | second resolution 409 | PASS |
| C4 | expiry | `resolve_signed` | resolution after expiry 409 | PASS |
| C5 | concurrent redemption | `UPDATE … WHERE status='pending'` rowcount; `exchange_code` `UPDATE … WHERE status='approved'` | 8 concurrent resolutions → exactly 1; 6 concurrent code exchanges → exactly 1 token | PASS on SQLite; the same single-statement guard is atomic on Postgres but was not run there (gap E-3) |
| C6 | revoked connector / inert account | `authenticate` | revoked connection 401; inactive signer 403 and the connection is revoked | PASS |
| C7 | in-flight revocation | `authenticate` once per request | a call already past `authenticate` completes; the next request is refused. No forced termination is documented or implemented; calls are short synchronous operations | PASS (implemented contract) |
| C8 | gateway kill | `kill_all`, `_check_open` | MCP 503, refresh and authorize refused, connectors disabled; new OAuth client **registration** is still accepted while frozen, and cannot authorize | PASS |
| C9 | no tool reaches a signature-gated action | tool set fixed at 7 tools; `council_request` creates a pending approval only | board posts of "allow" text to council/approvals threads change nothing; an agent cannot register as an approver or sign as one with its board key; path traversal on `research_read` refused by the path validator ("invalid path") | PASS |
| C10 | attribution | `agent.tool.call`, `agent.link.approved` | connector, account, tool, outcome recorded; the link event names the approval id. The tool-call event carries no connection id (join by connector, account and time) | PASS |

### Test D — audit chain (disposable Postgres, real trigger from migration 0062, real `common.audit_chain`)

| ID | Claim | Result | Outcome |
|---|---|---|---|
| D1 | a normal chain verifies | 6 events via `db.audit` → intact | PASS |
| D2 | an edited row is detected | `mismatch=[3]` | PASS |
| D3 | a deleted middle row is detected | `broken=[4]` | PASS |
| D4 | reordering is detected | `mismatch=[2,3]` | PASS |
| D5a | a forked history is detected | `mismatch=[7], broken=[7]` | PASS |
| D5b | concurrent writers do not fork the chain (the trigger's stated purpose) | 20 concurrent `db.audit` writers: chain **broken in 5 of 5 runs** (1–3 forks per run) | **FAIL** → R09-1 |
| D6 | tail truncation is detectable | newest 2 rows deleted: **intact**; the previous head is gone from the table and nothing compares it with the journaled head | **FAIL** against the documented claim → R09-2 |
| D7 | application vs administrative permissions | live (read-only): the app role `dispatch` owns `audit_log`, holds every privilege on it, and is a **superuser**; disposable: that role edited row 2 and recomputed the chain → intact; `COPY … TO PROGRAM` succeeded | **FAIL** → R09-3 (whole-chain rewrite is a documented limit; superuser is not documented) |
| D8 | a privileged operation cannot succeed without its audit event | governance events: the decision stands and the event is missing (`governance.audit` never raises — documented, §11). Admin bearer routes: `require_admin` calls `db.audit` directly, so an audit failure fails the request (closed) | as documented |
| D9 | tool calls are in the chain | `agent.tool.call` written through the same writer (C10) | PASS |
| D10 | a verifier failure is never reported healthy | tampered table → CLI rc 1 with `"intact": false` → sweep FAILED. Tampered table **and** an erroring verifier (pgcrypto dropped) → CLI rc 1 without that string → the sweep's own branch logs "unavailable", `chain_rc=0`, status ok, no push | **FAIL** → R09-4 |

### Test E — cross-architecture

| Target | Builds | Starts | Functional tests | Security controls | Performance |
|---|---|---|---|---|---|
| linux/arm64 (this host) | yes (production) | yes | suite passes on this host | yes (A, C, D above) | production |
| linux/amd64 | yes under emulation (review 05; web image, identical 59 package versions) | **not demonstrated** (this kernel cannot map psycopg's x86 libraries under emulation) | not run | receipts accept it only with `BUILD_EXPECTED_ARCH=amd64` | not measured |
| Apple Silicon, Linux VM (arm64) | expected: same arm64 images; not run | not run | not run | not run | not run |

Native or host-specific parts: `psycopg-binary`, `solace-pubsubplus` (wheels for both architectures, hashes in the universal lock; `test_the_real_locks_are_universal_and_complete` passes), the host `llama-server` binary and model (host service), systemd user units and Quadlets (Linux host or a Linux VM with systemd), SDR receivers (USB hardware; a macOS-hosted VM normally has no USB passthrough for them). Nothing here supports a claim either way for Apple Silicon.

### Test F — regional repurposeability

| Class | Examples |
|---|---|
| Reference defaults | DC receiver coordinates as the fallback for `ULTRAFEEDER_LAT/LON`; example watchlists |
| Settings | `NOTAM_HOME_ARTCCS`, `NOTAM_MONITOR_ARTCCS` (no code default), receiver position, Amtrak stations/routes |
| Optional integrations | MWAA FIDS (DCA/IAD), SDR feeders, AIS |
| Hardcoded functional dependencies | `SMES_AIRPORTS` / `_STDDS_REGIONAL_AIRPORTS` (`smes_parser.py`), `_SMES_AIRPORTS` (OOOI authority rule, `db.py`), `_DC_AREA_AIRPORTS` (`db.py`), `_DC_FACILITIES` (`tfms_parser.py`), `DC_STATIONS` (METAR and CPS, `metar.py`). A deployment elsewhere gets no surface tracking, no SWIM-over-airline OOOI rule and no METAR/CPS for its airports without code edits. `docs/REGIONALIZATION.md` §2 lists these honestly as code edits, so this is a portability limit, not a misrepresentation |

## C. Confirmed findings

| # | Finding | Severity | Prerequisite | Consequence | Smallest remediation |
|---|---|---|---|---|---|
| R09-1 | **Concurrent audit writes fork the hash chain.** The identity id is assigned before the trigger takes its advisory lock, and the trigger picks the predecessor by highest id, so a lower-id row that wins the lock later is skipped by the next writer | medium | none: ordinary concurrent writers (web, poller, gateway tool calls) | a false "chain does not verify" priority-5 alarm, and a chain whose integrity signal cannot be trusted. Not yet seen live (103 intact sweeps since 10-07) | take the chain position inside the trigger after the lock (a sequence or a one-row head table updated under the lock) and have the verifier order by it |
| R09-2 | **Deleting the newest audit rows is not detected**, contrary to "audit row edited or removed: flagged within 15 min" | medium | write access to `audit_log` (any app container) | the most recent actions can be erased silently | the sweep keeps the last verified head (hash and id) in a state file outside the database and fails if that head disappears or the id goes backwards |
| R09-3 | **The shared application database role is a Postgres superuser** and owns `audit_log` | medium | code execution in any container holding the database password | the whole chain can be rewritten and re-hashed, triggers disabled, and OS commands run inside the database container | `ALTER ROLE … NOSUPERUSER`; then revoke UPDATE/DELETE/TRUNCATE on `audit_log` from the app role (owner split, as migration 0062 already plans) |
| R09-4 | **An erroring chain verifier is reported as healthy.** The sweep treats any non-zero exit without `"intact": false` as "web container unavailable" | low–medium | the same as R09-3, or any benign breakage | the chain check can stay off indefinitely with a warning line only | fail when the verifier ran and errored; keep "unavailable" for a stopped web container only, and alert after repeated unavailability |
| R09-5 | **A tag re-pointed at another service's recorded image passes `check-deploy` and the sweep** | low | operator account (retag) | a unit runs another service's legitimately built code; outage rather than untrusted code | require the receipt's `deploy_name` (or the name's ledger) to match the tag being checked |
| R09-6 | **Commit signature status `U` counts as signed**; the live operator keyring holds 4 keys of unknown validity | low | a commit signed by one of those keys reaching a build | provenance reports such a build as signed | check the commit's signing key against the pinned fingerprint, as `verify-manifest.sh` does |
| R09-7 | **Receipt base-image digests and architecture are not verified**, although the build doc says the base digest is "checked against the Containerfile at that commit" | low | write access to the receipt store (operator account) | an auditor reading the receipt can be misled; the authoritative Containerfile is still bound | derive the FROM digests from the commit's Containerfile and compare; compare the recorded architecture with the image |
| R09-8 | **Receipt robustness:** a nulled or unknown `source_commit` downgrades to UNVERIFIED without an alert; one malformed receipt aborts the whole verification run with a traceback | low | operator account | fails closed for the run, but hides the other images' results; the downgrade is quiet | treat a recorded receipt with no resolvable commit as FAILED; catch per-image parse errors and report them as FAILED rows |

## D. Cleared concerns

- **Mutable `:latest` / `:previous` tags are not a deployment-identity vulnerability.** Verification resolves every tag to its content-addressed image id and looks up the receipt by that id (A2a, A3); the sweep checks the actual image id of every running container independently of the tag (A9, A9b); rollback to `:previous` is recognised through that image's own earlier receipt (A8). The one gap (R09-5) is cross-service substitution, which is about the name binding, not tag mutability.
- Review 06's runner path records provenance and stops on failure; foreign-architecture images are never recorded.
- SBOM edits are caught; consistent SBOM forgery is caught by `--deep`.
- Approval binding, replay, expiry, single-winner concurrency, connector identity isolation, revocation and the kill switch hold against the real code.

## E. Remaining verification gaps (not vulnerabilities)

1. **NOT RUN — HUMAN AUTHORIZATION REQUIRED:** `approved-exec.py` end to end as root against the installed pin and ledger. Procedure: create a `sudo` approval for an allowlisted no-op shape, `scripts/approve.sh allow <id>`, run `sudo -n /usr/local/libexec/ctdc/approved-exec.py <id>` twice (second must refuse "already been executed"), then once more after `expires_at + 300 s` with a fresh approval (must refuse "too late").
2. Sweep end-to-end with a real priority-5 push on a provenance FAILED (only the classification logic was exercised).
3. C5 concurrency on Postgres (tested on the SQLite test backend).
4. amd64 runtime and Apple Silicon (not available on this host).
5. `--deep` SBOM verification is not part of the 15-minute sweep (documented), so consistent SBOM forgery is detected only on demand.
6. Signed audit checkpoints: the table is empty on the live database (planned, as documented).

## F. Process notes

- Every tampering test ran in an isolated podman root, a disposable Postgres or the test harness. Nothing live was built, restarted, retagged or written.
- One falsification probe called the `research_read` tool with an in-scope path through the test harness, which made a **real read-only WebDAV GET** to the live vault (result: not found). The repository's test safety net isolates the database and ntfy, not WebDAV; a test that called `workspace_contribute` would write to the live vault. Recorded as a test-infrastructure gap.
- Test additions: `tests/web/test_review09_agent_authority.py` (12 tests) in the review worktree; not merged, signed or deployed. The provenance and audit-chain experiments are scripts outside the tree (real podman / disposable Postgres) and are described here rather than committed.
