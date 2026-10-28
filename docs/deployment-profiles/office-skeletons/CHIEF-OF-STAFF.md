# Chief of Staff profile skeleton

> **Template, disabled until after the feature freeze** (staged 2026-10-20 UTC). Profile JSON:
> [`../profiles/chief-of-staff.json`](../profiles/chief-of-staff.json). Overview and rules: [README.md](README.md).

## Role

The Chief of Staff runs the principal's operating rhythm: initiatives, objectives and key results, decisions,
briefs and follow-through. It is a separate profile from the Executive Assistant since 2026-10-20 (operator).

- **Modules:** `briefings`, `decision_register`, `initiative_tracking`, `okr_tracking`, `operating_cadence` and
  `follow_up`.
- **Sources:** `document_repository`, `task_board` and `calendar`. The calendar source is read for cadence; the
  CoS does not book.
- **Glossary scopes:** `governance`, `office` and `scheduling`. Rendered with
  `scripts/render-glossary.py --profile chief-of-staff`.

## Approvals and data classes

- **Signed approvals:** `share_confidential_brief`, `commit_budget` and `close_initiative`.
- **PII classes:** `confidential_business_strategy` and `stakeholder_contacts`.

## Services (disabled)

| Service | Role in the profile |
|---|---|
| Nextcloud Deck | Initiative board: one card per initiative, linked to the trip tag and the ledger tag report |
| Nextcloud Collectives | Decision log and meeting notes (needs `circles` and `text`, both already enabled) |
| Nextcloud Tables | OKR register and decision register with an API |
| Vikunja (`corporatetraveldc-vikunja`, optional) | A stand-alone initiative/project API, if Deck is not enough; PostgreSQL `vikunja` database on the office instance, `VIKUNJA_DATABASE_TYPE=postgres` pinned (its default is SQLite) |

~~The daily ops brief and the second-brain vault already in Nextcloud supply the narrative for briefings.~~
(corrected 2026-10-25: the second-brain vault in Nextcloud supplies the narrative for briefings; the ops brief
itself is stored in Postgres `brief_archive` (`src/poller/skills/ops_brief.py:1248`), and only its latest excerpt
reaches Nextcloud, inside each daily vault note (`src/poller/skills/second_brain_daily.py:250`).) Nothing
reads them for this profile yet.
