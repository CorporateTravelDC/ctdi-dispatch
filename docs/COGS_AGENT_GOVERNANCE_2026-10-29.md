# Agent governance — COGS and replacement-capability inventory

Internal planning addendum, 2026-10-29 UTC. This is a capability-scope correction,
not a vendor quote, appraisal, revenue claim or investor-material release.
Basis: implementation at c5711d0a2f10c53cadc9a19ae01620c1b30ac07c, one
own-account flight-watch/API execution, and the prepared instruction patch.
No new commercial price or measured labor figure was obtained in this pass.

## What is now explicitly counted

The following belong in the platform's deployment-ready capability inventory.
They must not disappear from a replacement assessment because they lack a
convenient subscription SKU, or be counted again as a second database/network.

| Capability | Implementation/evidence | Delivery status | Cost/value treatment |
|---|---|---|---|
| Separate human/agent Unix identities and restricted groups/config | `scripts/agent-segmentation/plan.sh`, allowlists, `docs/AGENT_SEGMENTATION.md`; own account and limited subset used | Existing implementation; all-account boundary retest pending | Include identity/provisioning/control engineering and recurring administration; unpriced |
| Per-account skill grants and clawback | `scripts/lib/skill_grants.py`, signed source and installed-copy verification | Existing implementation; historical copy/connector drift observed | Include policy/distribution capability, not a new inference subscription; unpriced |
| Operator-minted personal API token with named mutation actions | `scripts/mint-agent-api-token.sh`, `src/auth/auth.py::require_admin/action_allowed`, schema 0070 | Existing since 2026-10-05; one watch add returned 201 on 2026-10-29 | Include credential lifecycle, provisioning and action authorization; no new feed/model-token charge established |
| Account-attributed gateway, workspace and board routes | `src/web/routes/agent_gateway.py`; own connector workspace contribution and board handoff executed | Existing routes; action surface depends on tool catalog | Include integration, attribution and review workflow; share existing hosting/database costs |
| Revocation/liveness linkage and human approval boundaries | Account-labelled tokens, liveness and approval implementation | Code traced; no live revocation, kill, approval replay or signature test in this pass | Include implemented lifecycle/approval capability; do not label it newly security-certified |
| Account-specific pamphlet rendering and corrected API/skill guidance | Prepared renderer/template, three corrected skills, flight skill API client | Source-complete patch and fixtures; not declared installed/deployed | Include completed engineering scope; separate operator distribution/runtime acceptance work still due |

## Cost ledger and valuation limits

| Ledger | Entry | Amount / handling |
|---|---|---|
| Recurring cash COGS | Incremental hosting/CPU/storage/network used by authorization, audit and refresh | Not separately metered. Runs on existing platform infrastructure; do not infer exactly zero incremental cost |
| Recurring operations | Operator provisioning, grant review, expiry/revocation decisions, monitoring, instruction parity and maintenance | Labor frequency/rate not measured; explicitly unpriced |
| Existing subscriptions | Agent vendor plans and any existing network/backup services | Keep the existing `COST_STRUCTURE.md` lines; this mechanism does not prove those plans are free |
| One-time engineering | Segmentation, authorization integration, lifecycle tools, fixtures, API client and instruction repair | Implemented/source-complete scope included; hours and labor rate not measured, so no dollar uplift invented |
| Replacement equivalent | Identity/policy administration plus integration of action authorization, attribution, audit and human review | Explicit unpriced scope in the replacement model; obtain a scope-matched quote or a bottom-up labor estimate before changing totals |
| Existing infrastructure overlap | PostgreSQL, hosting, mesh access, notifications and generic audit already included elsewhere | Do not add their full cost again under governance |

The dated 2026-09-28 and 2026-08-18 dollar totals remain historical estimates.
They are not recomputed here and must not be advertised as an all-inclusive
price for the expanded governance inventory. "Unpriced" is not a measured
zero, nor a claim that no commercial alternative exists. This addendum is
linked from both the current cost structure and the historical two-column
comparison so the omission is visible wherever those totals are cited.

## Security boundaries that constrain the value claim

The action allowlist is applied by `require_admin(action)`, not by every read
route. Tier-only reads remain tier-authorized. An empty scope is legacy
unrestricted behavior; an action grant is not parameter-bound human approval.
The mint helper does not specify `expires_at`, and token generation defaults
to no expiry. Do not price or market automatically short-lived, universally
narrow per-route tokens as an implemented feature of this helper.

The successful watch request proves that one separately operator-minted
account token can perform its intended operation without a shared admin
credential. It does not prove every account has a token, lifecycle enforcement
has passed a fresh pen test, or all installed instructions match source.
See [Per-agent API actions](PER_AGENT_API_ACTIONS.md) for the exact test scope
to include before the later pen test. Documentation preparation does not
authorize or start that test.

## What would permit a priced update

Establish measured recurring resource use and administration time, identify
already-counted costs, and obtain a replacement quote or an evidenced labor
estimate for the uncovered functions. Record date, deployment scope, included
controls and exclusions. Recalculate the complete ledger once, with any
low/high assumptions explicit; retain the dated baseline as history. Investor
promotion remains the operator's separate decision.
