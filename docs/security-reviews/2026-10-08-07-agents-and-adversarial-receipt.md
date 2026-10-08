# Security review 07 — new agents, and an adversarial receipt for 2026-10-07 → 2026-10-08 (2026-10-08)

> **Review record. Frozen once committed.** Part of the security review chain (`README.md` in this directory). It follows `2026-10-08-06-build-arch-and-ladd.md`. It covers the Codex and ChatGPT onboarding and re-tests the claims made since review 05 against the live system. The operator's reconcile of the same date is in the vault: `01-Sources/manual/20261008T114213Z.md`.

## Scope

- **Changes since review 06** (`8b5be86`):
  - Codex as its own agent account (`553ffb5`, plus a fix-forward run by relay);
  - the ChatGPT service identity and connector (`92231b1`);
  - the vault pamphlet step;
  - this record's own fix.
- **Method:** live probes, read-only unless stated, plus the test suite. No destructive testing. Identifiers that could be LADD-listed are reported as counts only.

## Holds (re-tested live)

| Claim | Probe | Result |
|---|---|---|
| Team accounts cannot write the repository | group-writable files in the checkout and in `.git` | **0 / 0** |
| The new agents have no sudo | `sudo -l -U ctdc-agent-openai-codex` | not allowed |
| ChatGPT is a service identity | shell, groups | `/usr/sbin/nologin`; `ctdc-dev`, `ctdc-agents` only; home closed to other accounts |
| Gateway endpoints need a token bound to their connector | POST `/mcp/{codex,chatgpt,cowork,<unknown>}` without a token | **401** for all four; an unknown slug is indistinguishable from a known one. Cross-connector refusal: existing test |
| Codex is live only while its own login and daemon are | `team-liveness.sh --status`; tests | `agent/codex refresh=0.0d session=active`. Tests: a stale login, a missing refresh token, a missing daemon, or Claude-only credentials each make it stale |
| Nothing Codex runs as the operator | `verify.sh` 4d–4g | all PASS |
| LADD lookup | 200 random US registrations, both forms; 20 removals | 200/200, 200/200; 0 removals listed |
| Running images match their receipts | `provenance.py verify --running` | 14 VERIFIED, 4 UNVERIFIED (one commit behind), 0 FAILED |
| The current public mirror carries no live watchlist and no N-shaped registration | GitHub contents of `watchlists/`; leak gate | example only; gate clean |

## Findings

| # | Finding | Severity | Disposition |
|---|---|---|---|
| F1 | **Gateway tool calls were not audited.** `tools/call` was authenticated and executed, but only `agent_connections.last_call_at` was kept. ChatGPT's and Codex's board posts on 2026-10-08 have no audit-chain record. The ChatGPT documentation written that morning said "every call audited", which was **false when written** | medium | **fixed:** every tool call is an `agent.tool.call` event (connector, account, tool, outcome, sha256 of the arguments, never their content). Test `test_every_tool_call_is_in_the_audit_chain_without_its_content`. Docs corrected with strikethrough |
| F2 | **Real aircraft registrations in other public repositories.** `agentic-management-tooling-mcp` (public, active): 1, LADD-listed. `corporatetravel-dispatch-mcp` (public, archived): 3 real, 2 LADD-listed | high (privacy) | **fixed by history rewrite** (operator direction: "clean those up and rewrite"). Every revision of all three public repositories (`ctdi-dispatch` mirror, 578 commits; `agentic-management-tooling-mcp`, 17; `corporatetravel-dispatch-mcp`, 27) was rewritten with one real→synthetic mapping: registrations to the same `Q` values the private tree uses since review 06, real hex codes to values unassigned in the FAA and OpenSky tables. Commit messages included. The live watchlist file was removed from every revision of the mirror. Verified after the rewrite: 0 registration-shaped tokens, 0 mapped real hexes, 0 watchlist paths in any revision. The archived repository is unarchived for the push and re-archived. Limits: rewritten commits lose their original signatures; forks, clones and GitHub caches made before the push may keep the old objects |
| F9 | **Real identifiers the review 06 sweep missed** (found by the F2 history scan): real hex codes in five tests and fixtures (one was the `a0000…`-style value review 06 itself wrote into a new test, which is assigned to a real aircraft) and two Canadian registrations in tests. The N-number guard cannot see either kind | medium (privacy) | **fixed:** replaced with the same values the history rewrite uses, so the next public push matches the rewritten history. Hex-shaped tokens that were not aircraft addresses (IATA flight numbers such as two letters A–F plus four digits, and ADS-B payload values in a FAA FIXM sample) were deliberately left alone. A general guard for hex codes and non-US registrations is not built: every hex-shaped token needs a registry lookup to judge, so it stays a review step |
| F3 | **The Codex agent's vendor login is the operator's personal ChatGPT account.** Pairing and the phone path depend on it, so the agent's `~/.codex/auth.json` holds tokens for that personal account | medium | accepted for this single-operator deployment and recorded. Per `docs/AGENT_TRUST_MODEL.md` §0, personal accounts are not acceptable at organizational scale: a managed workspace account per agent there |
| F4 | **Two old active tokens:** a `cowork` shares-tier token (2026-08-16, before Cowork moved to the gateway) and a `corporatetravel` **admin** token (2026-08-29) | medium | open, operator review; revoke if unused |
| F5 | **Credential rotation after the 2026-10-04 duel is partly unverified.** `DISPATCH_ADMIN_TOKEN` was rotated 2026-10-05 and the old row revoked; `BOARD_KEY`, `NTFY_TOKEN` and the Nextcloud app password are not confirmed | medium | open, operator |
| F6 | **A screen session `Claude-ChatGPT-Remote-Control` runs as the operator** | unknown | open: if it is a vendor agent, it is the operator-as-agent exception and should move to an agent or service account like Codex |
| F7 | **The Codex daemon updates itself** under `~/.codex/packages/` (observed: CLI 0.160.0, operator daemon 0.161.0) | low | recorded as a runtime-download exception (`docs/REPRODUCIBLE_BUILDS.md`); the installed CLI is pinned |
| F8 | **Cloud agents need their pamphlet in the vault;** ChatGPT's first read was a 404 | low | fixed: published; `plan.sh --add-service` now prints the publish step |

## Process failures in the period (the agent's own, recorded for the record)

- **Outage:** an amd64 test poisoned the base-image cache, and production images were built amd64. About 40 min of web 500s and about 80 min of SWIM ingest lost (review 06).
- **Incomplete activation:** the Codex activation copied the binary without its package, so the daemon failed. The account was minutes from being made inert; a fix-forward relay restored it.
- **A relay killed its own shell:** a relay quit a screen session by name, and the operator's shell was inside it, so the relay died before its `trap` re-enabled the liveness timer. Standing rule since: relays never kill sessions.
- **Missed onboarding step:** the ChatGPT onboarding omitted the vault pamphlet.
- **Overstated claim:** "every call audited" (F1).
- **Four read-only `python3 -c` invocations,** against the heredoc rule (two more during the F2 and README work: a JSON count of `/api/v1/feeds`, and one inside a container command that never ran).
- **One real hex code printed to the session** (a Canadian commercial aircraft, not LADD-listed) while masking registrations in a context listing; the mask covered registrations only.

## Observations recorded

- **ChatGPT Plus performs writes** through a developer-mode connector. It posted to the board at 11:33:50Z, despite OpenAI's help center describing Plus as read/fetch only.
- **Headless Codex phone pairing worked** with no desktop app and no X11. This matches openai/codex #31183 → #35928, and #50660 (0.160.0). Most likely an upstream fix.

## Validation

- **Gateway tests:** 18 (one new).
- **Liveness:** 81 (6 new for codex mode).
- **Segmentation dry-run:** 17 (2 new).
- **History rewrite:** 0 real registrations, 0 mapped real hex codes, 0 watchlist paths in any revision of the three public repositories (counts only).
- **Edited tests:** 328 pass (the 11 files touched by F9 plus the ingest suite and the synthetic-identifier guard).
- **Full suite:** run in the deploy relay, together with the leak gate.
