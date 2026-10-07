# Security review chain

The **canonical** security documents describe the platform as it is now:

- `docs/AGENT_TRUST_MODEL.md`;
- `SECURITY.md`;
- `docs/COMPLIANCE_SECURITY.md`;
- `docs/SUDO_JUSTIFICATION_PROPOSAL.md`;
- `docs/GUARDRAILS_JUSTIFICATION.md`;
- `docs/SINGLE_EDGE_UNIT_ASSUMPTIONS.md`.

Where a statement in them changed, the earlier wording is kept struck through beside the current value, with the date it changed. Larger replaced passages move to a `## Superseded (kept for the record)` section at the end of the document.

This directory holds the **interstitial records**: one record per review pass, and frozen snapshots of what a pass produced. Together they form a breadcrumb trail. A reviewer can see how each canonical statement got to where it is, and that it has been tested against the live system more than once, rather than asserted once.

## Conventions

- **A review record** (`YYYY-MM-DD-NN-<name>.md`) says why the pass ran, the method, every finding with the status it was found in, the disposition, and how it was validated. Once committed it is frozen: later passes add records and never edit earlier ones.
- **A snapshot** (`*.snapshot.md`) is a canonical document copied verbatim at the end of a pass, under a header that marks it as not current. It shows exactly what was claimed at that point.
- **Statuses** use the canonical vocabulary: ENFORCED, PARTIAL, POLICY ONLY, NOT ENFORCED, NOT APPLICABLE, UNVERIFIED.
- **Fixes** cite the enforcing code or configuration and the test or live check that proves them.

## Chain

| Date | Pass | Record | What it established |
|---|---|---|---|
| 2026-08-13 | Live validation and bounded penetration test | `docs/LIVE_VALIDATION_AND_PENTEST_2026-08-13.md` | first claim-by-claim re-verification against the running system, plus a bounded self-pentest |
| 2026-08-13 | Pentest clearance re-check | `docs/PENTEST_CLEARANCE_CHECK_2026-08-13.md` | each pentest finding re-verified against the current system |
| 2026-10-04 | Adversarial duel (blind safety and code-review reviewers, then a cross-verifier) | `docs/AGENT_SEGMENTATION.md` (segmentation and duel history); fix-wave commits from `6d02bf9` | repository group-write, agent credential subsets, argv tokens, and root executing the checkout, each closed in fix waves |
| 2026-10-06 | Documentation verified against code and live state | `docs/FINDINGS_2026-10-06.md`, `docs/docs-refresh-2026-10-06/CHANGES-security.md` (and the other domain ledgers) | about 520 corrected claims; S1–S3 findings |
| 2026-10-07 | **01 — Agent trust-model documentation pass** | `2026-10-07-01-trust-model-pass.md`; snapshot `2026-10-07-01-agent-trust-model.snapshot.md` | the human → approval → delegated-authority chain, documented for a hostile reviewer; gaps named, with nothing overstated |
| 2026-10-07 | **02 — Hardening pass** | `2026-10-07-02-hardening.md`; snapshot `2026-10-07-02-agent-trust-model.snapshot.md` | root executor for signed sudo approvals, TTL caps, canonical v2, requester binding, console redemption bound, governance events in the verified audit chain; deployment-model exception documented |
| 2026-10-07 | **03 — Post-deploy verification and dependency pass** | `2026-10-07-03-post-deploy-and-dependencies.md` | hardening confirmed live (the gated sudo command now needs a password; audit chain intact); 24 dependency advisories fixed (5 Dependabot + 19 found by auditing the running images); `npm ci` for the frontend build |
| 2026-10-07 | **04 — Dependency scanning on every repository** | `2026-10-07-04-dependency-scanning.md` | pre-push and daily local audit (repos and worktrees, running and dormant images), Dependabot alerts on all 12 repos as a second layer; rule: dormant or undeployed code is not exempt |

External review input between passes (2026-10-07):

- An external reviewer's reading of the public repository prompted pass 01.
- Its reading of pass 01's document recommended keeping this breadcrumb trail.
- It also flagged the missing deterministic, version-pinned builds, which is now at the top of the backlog.
