# CFO profile skeleton

> **Template, disabled until after the feature freeze** (staged 2026-10-20 UTC). Profile JSON:
> [`../profiles/cfo.json`](../profiles/cfo.json). Overview and rules: [README.md](README.md).

## Role

The Chief Financial Officer owns budgets, cash, controls and financial reporting. The profile's modules are
`budgeting`, `cash_forecast`, `close_checklist`, `board_reporting`, `variance_analysis` and `controls_register`.
Its sources are `accounting_ledger`, `bank_statement_import` and `erp`. ~~Each name is mapped to the glossary's
`finance` scope; `board_reporting`, `close_checklist` and `controls_register` also map to `governance`.~~
(corrected 2026-10-25: each name is mapped to the glossary's `finance` scope; `board_reporting`, `close_checklist`
and `controls_register` also map to `governance`, and `budgeting` also maps to `office`, so the CFO slice is
agents, finance, governance, office and platform -- `render-glossary.py --list-profiles` at 5f7fca01.) The
slice is rendered with `scripts/render-glossary.py --profile cfo`.

## Approvals and data classes

- **Signed approvals** (`workflows.approvals`): `approve_payment`, `change_bank_details` and
  `release_financial_report`. They are bound to the platform's signed approval mechanism. Pack gate 8 is still
  open: no approval kind exists for these yet.
- **PII classes:** `financial_records`, `payroll` and `banking_metadata`. Pack gate 7 requires financial records
  to be isolated; ~~this is why the ledger has its own database instance.~~ (corrected 2026-10-25: this is why the
  ledger's database, `firefly`, lives on the separate office instance `corporatetraveldc-pgsql-office`, which it
  shares with Paperless-ngx and Vikunja but not with production.)

## Services (disabled)

| Service | Role in the profile | Notes |
|---|---|---|
| Firefly III (`corporatetraveldc-firefly`) | The ledger: accounts, budgets (Travel, Ground), categories, one tag per trip, rules; REST `/api/v1` with personal access tokens | PostgreSQL `firefly` database on the office instance; the image's default MySQL and any SQLite are excluded by the pinned `DB_CONNECTION=pgsql`. No native OIDC (unverified 2026-10-25: a Firefly III capability, not checked here; the quadlet pins `AUTHENTICATION_GUARD=web`, local login): local login, or header auth behind an authenticating proxy, at activation. |
| Paperless-ngx (`corporatetraveldc-paperless`), shared with the EA | The receipt of record for every ledger line | The ledger line carries the document link in its notes. |

The pack's `integrations.optional` lists both, plus the office database instance, with `state: disabled`.
`integrations.forbidden` names a SQLite backend, the platform env files and the production database.

## What the demo shows

The demo shows a budget against actuals per trip tag, which is variance analysis. It shows one approval over a
threshold routed to a human signature. It also shows the platform's own per-trip data costs as a cost-of-goods
line, for example the AeroAPI gate-call ledger. All demo data is synthetic. See
[`../compositions/EXEC-OFFICE-CROSSOVER.md`](../compositions/EXEC-OFFICE-CROSSOVER.md).

## Not built

- No bridge job from Paperless to Firefly.
- No Firefly Data Importer, since bank import comes later.
- No recurring-transactions timer: Firefly needs a cron call, and its timer is written at activation.
- No profile consumer. ~~Nothing at runtime reads `cfo.json` yet; the METAR fetcher is the only profile consumer.~~
  (corrected 2026-10-25: nothing acts on `cfo.json`. The only runtime importer of the profile loader is the METAR
  fetcher, `src/poller/fetchers/metar.py:31`, and it loads the bundled reference profile because `DISPATCH_PROFILE`
  is not set. `cfo.json` is read only as text, by the agent-pamphlet renderer
  (`scripts/agent-segmentation/render-profiles.py:71`) and by `scripts/render-glossary.py`.)
