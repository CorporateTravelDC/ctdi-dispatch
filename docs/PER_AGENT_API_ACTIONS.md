# Per-agent dispatch API actions after segmentation

The operator grants API actions to an account separately from granting a skill or creating its Unix account. A skill describes an operation; it does not grant its credential, action scope, connector tool, or notification permission.

## Credential and transport decision

1. Identify the current account and the exact route's authorization action. Use only that account's connector or credential. The flight milestone watch add is `POST /api/v1/watchlist/flights`, action `watchlist.flight.add` (`src/web/routes/watchlist.py`).
2. If the current account's authenticated connector actually exposes the requested operation, use it within its granted scope. Reading the skill through `skill_read` does not execute it. The current gateway tool catalog in `src/web/routes/agent_gateway.py` has eight tools and no watchlist operation.
3. With an on-box Unix session, an operator-issued personal token may be read from the current account's own `~/.config/ctdc/api-token`. Read it only inside the request process, require a regular file owned by the current UID with no group/other permissions, reject symlinks and empty content, and never print the value or put it in argv. Use only `http://100.x.x.x:8000`. This is the account's bearer token, not an SSH/GPG signature.
4. The group secrets subset remains configuration loaded verbatim through `scripts/with-dispatch-env.sh`; never source it. The loader does not load the personal token file. Do not expect `DISPATCH_ADMIN_TOKEN` in the agent subset or seek the operator's production env files.
5. A missing/insecure token file, absent connector operation, or denied request means stop that action and report the missing capability to the operator. A dispatch-web 403 can mean missing, expired, revoked, wrong-tier or out-of-scope credentials; it does not identify which. Do not self-mint, broaden scope, use another account, or fall back to a shared/master credential. After an uncertain write outcome, check the requested record if authorized before considering a retry.

## Explicit operator provisioning step

Account creation with `scripts/agent-segmentation/plan.sh` does not mint this personal token. This is a separate, optional, task-dependent operator decision. The operator selects the account and exact actions, then runs `scripts/mint-agent-api-token.sh ACCOUNT --actions ACTIONS --tier admin --execute` from the repository. Without `--execute` it is plan-only. Agents never execute the mint operation.

The helper delivers the value to `/home/ACCOUNT/.config/ctdc/api-token`, owned by that account, mode 0600, through a pipe. It records `user_label=ACCOUNT` and `allowed_actions` on the token. The `admin` tier here is necessary for the route's dependency; the explicit action allowlist constrains that token. `src/auth/auth.py::require_admin` denies and audits actions outside the allowlist. Empty scopes retain legacy unrestricted behavior, so use explicit named actions rather than an empty scope or a broad wildcard. This is action scoping, not a parameter-bound approval: `watchlist.flight.add` is not restricted to one particular flight.

The helper's `/admin/healthz` probe should answer 403 when `admin.healthz` is not granted. That expected denial does not establish that the intended action will succeed. Confirm the authorized task's own response. Skill grants and liveness are separate controls; do not infer current liveness or revocation behavior from file existence alone.

## Flight watch and notification boundaries

For a flight watch, normalize the operating callsign, include the requested origin and destination, and require a verified current-leg hex ID (preferred) or registration/tail before adding. If neither is established through permitted sources, report the watch blocked on identity. Existing callsign-only records are incomplete; reconcile them rather than claiming a ready watch. A successful 201 from the add route confirms the entry was added/refreshed; it does not prove a future milestone was observed or a notification delivered. Use the account name for `added_by`; do not imply operator authorship.

A watch-add scope does not grant watch deletion, batch addition, manual alert pushes, approval, deployment, publication or vault administration. If the skill also asks for pushes, perform only separately authorized actions and report any unavailable ones. Do not ask for the shared `NTFY_TOKEN` or `DISPATCH_ADMIN_TOKEN`.

## Distribution and casebook evidence

The tracked `skills/` files are source instructions. Installed skill copies and connector-served skill content must be checked against that source; matching names are insufficient. The operator signs and deploys changes and reconciles granted copies. Agents prepare patches in their own clone and use workspace contributions for durable findings. Never overwrite root-owned skill copies or the rendered home pamphlet.

The casebook collection is under vault `01-Sources/personal-notes/Series/platform-history/`, with its numbered index in `CASEBOOK.md`. The 2026-10-29 contribution records the account-specific watch success and the observed distribution/provisioning gaps. Canonical casebook numbering is assigned when the operator promotes that contribution; no unverified incident number is invented here.

## The same correction for every account

`scripts/agent-segmentation/render-agent-capabilities.py` renders the common section with the selected registry account's name, passwd home path, kind, login mode, skill root, liveness defaults and preload status. It reads metadata only; it never reads a token or another account's private files. The main onboarding renderer embeds that section, so every future pamphlet gets the same logic rather than a hand-maintained per-agent fork.

The vault publisher now uses registry-derived context instead of labelling every client as cloud-only. Its `--render-only` mode skips env loading and publication. The weekly publisher selects activated agent/service rows from the registry and leaves preloaded identities dormant. Home pamphlet installation remains an operator step, separate from vault publication and the existing signed skill-grants reconciliation. The generated capability rows describe a mechanism, not evidence that every account received an API token or action grant.

Operator handoff sections are generated for all registered agent/service accounts, including an explicitly marked future section for a preloaded account. Dropping a section into a pamphlet grants nothing and does not activate that account. Re-render/sign/reconcile from the canonical source to keep later refreshes from restoring stale text. Connector-served skill parity still requires the operator's normal signed runtime rollout; refreshing a pamphlet alone does not update a running image.

## Pre-pen-test scope register (2026-10-29)

This is preparation for a later authorized test, not a completed security review. Include the implemented segmentation and personal API-token mechanism in the test target inventory before starting. The successful flight-watch request is a functional positive test only.

| Boundary / claim | Current evidence | Later validation |
|---|---|---|
| Unix account and credential separation | Segmentation implementation; one own-account request; operator secrets deliberately excluded | Safely confirm other-account/operator boundaries without reading secret values |
| Explicit action scope | `require_admin(action)` calls `action_allowed`; `watchlist.flight.add` succeeded | Isolated wrong-action, wrong-tier, wildcard and legacy empty-scope cases; no live forbidden action to prove a denial |
| Scope versus read tier | `require_tier` and `resolve_tier` check the tier, not `allowed_actions`; the personal admin-tier token accessed Tier-1 reads in this run | Inventory every route's dependency; do not equate one permitted mutation action with universally narrow read access |
| Scope versus exact request approval | The allowlist matches an action name, not actor/flight/parameters/target/expiry as a signed approval would | Keep action grants separate from exact human approval binding and replay tests |
| Expiry | `mint-agent-api-token.sh` calls `generate_token` without `expires_at`; that function defaults to no expiry | Record actual token expiry/revocation policy; do not call this helper's personal tokens short-lived board/approval tokens |
| Lifecycle revocation | Account-labelled token revocation is implemented | Isolated expired/revoked/wrong-account and liveness/kill-order tests; no live kill/revoke here |
| Instruction delivery | Installed/connector skill drift observed; correction prepared | Verify source, granted copies, rendered home/vault pamphlets and running connector after signed rollout |
| Confidentiality | Request-time token reader, sanitized failures, redirect refusal and output-selection rules | Isolated missing/insecure/symlink token, reflected error and redirect cases; broader board/log/artifact leakage remains a separate review |

Do not start or claim the pen test merely because this document is updated. Confirm the reviewed source and deployed revision, permissions and fixtures through the existing pen-test workflow. COGS scope and valuation limits are recorded in [Agent governance cost/value inventory](COGS_AGENT_GOVERNANCE_2026-10-29.md).
