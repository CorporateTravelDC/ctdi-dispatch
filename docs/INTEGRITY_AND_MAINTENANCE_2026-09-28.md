# Integrity & Maintenance Reference

**Written 2026-09-28.** Code-validated. A short reference for three
maintenance/integrity subsystems:

1. maintenance-window enforcement
2. audit-log tamper-evidence (schema 0062)
3. site-origin provenance (schema 0061)

Commit `97f4e80` ("Add maintenance window enforcement, audit tamper-evidence,
site-origin provenance, and bulk-migration timeout fixes", 2026-09-21) shipped
the **shell** window guard (`scripts/maintenance-window-guard.sh`) and both
migrations `0061_site_origin.sql` / `0062_audit_tamper_evidence.sql`. The
**Python** mirror `src/common/maintenance_window.py` was added separately in
commit `2af693e` ("audit retention: archive+sign off-box, retire in-place
prune") — validated against `git log`, since the module is not part of
`97f4e80` despite that commit carrying the "maintenance window enforcement"
title.

Chose a new standalone doc rather than appending to `COMPLIANCE_SECURITY.md`
because these are cross-cutting mechanics (scheduling, audit chain, graph
provenance) rather than a compliance-control narrative.

---

## 1. Maintenance-window enforcement — `src/common/maintenance_window.py`

An in-process mirror of `scripts/maintenance-window-guard.sh` so a job scheduled
*inside* the poller (a single long-lived asyncio process) gets the same gate the
host-level `ExecCondition=` hook gives systemd units
(`maintenance_window.py:1-25`). The window confines long report-tier work to the
quiet hours so it never contends with the latency-sensitive alert path.

- **Config** — three shared env vars, identical to the shell guard
  (`maintenance_window.py:81-87`): `CTDC_MAINTENANCE_WINDOW_START`,
  `CTDC_MAINTENANCE_WINDOW_END`, `CTDC_MAINTENANCE_WINDOW_TZ`. Defaults
  `23:00`–`05:00` `America/New_York` (`maintenance_window.py:35-37`).
- **`is_open(at_minutes=None)`** (`maintenance_window.py:90-113`) — the gate.
  Three cases handled explicitly:
  - **midnight wrap** (`start > end`, e.g. 23:00–05:00): `now >= start or
    now < end` — a naive between-check would never be true
    (`maintenance_window.py:113`).
  - **`start == end`**: treated as **always open** — a misconfiguration that
    silently disabled every governed job would be worse, because skipped work
    raises nothing (`maintenance_window.py:109-110`, `12-21`).
  - **malformed value**: `is_open()` **fails OPEN** and logs an error — a bad
    value must never become an invisible permanent skip
    (`maintenance_window.py:98-105`; `_to_minutes` returns `None` rather than
    coercing to 0, `maintenance_window.py:40-55`).
- **TZ handling** — `_local_now_minutes()` sets `TZ` around `localtime()` and
  calls `time.tzset()` (required, else libc keeps its cached zone), restoring the
  prior `TZ` afterwards (`maintenance_window.py:58-78`).
- **Drift discipline** — the module is deliberately a mirror, not a second source
  of truth; both sides read the same env vars, and the docstring mandates
  changing `scripts/maintenance-window-guard.sh` in the same commit
  (`maintenance_window.py:11-25`). This generalizes the earlier thermal-guard
  pattern into a shared maintenance-awareness gate.

---

## 2. Audit-log tamper-evidence — `0062_audit_tamper_evidence.sql`

Closes the finding in `docs/ANP_FEDERATION_RESEARCH_2026-09-21.md`: `audit_log`
was append-only by **convention and code path only** — no triggers, no rules, no
grant restrictions; a direct UPDATE/DELETE was neither prevented nor detectable
(`0062_…sql:4-9`). Requires `pgcrypto` for `digest()`, and fails loudly at
`CREATE EXTENSION` if absent rather than silently installing an unhashed trigger
(`0062_…sql:22-24`).

Three parts:

1. **Hash chain** (`0062_…sql:28-…`) — adds `prev_hash` and `row_hash` columns.
   Trigger `audit_log_chain()` commits each row to its predecessor:
   `row_hash = digest(prev_hash | event_time | action | tier | token_prefix |
   remote_addr | …)`. The actor identity (`token_prefix`, `tier`,
   `remote_addr`) is hashed **inside** the content, so it cannot be altered after
   the fact without breaking the chain (`0062_…sql:31-35`). Appends are
   serialized with a transaction-scoped `pg_advisory_xact_lock` so two writers
   (web and poller are separate containers, both audit) cannot fork the chain
   (`0062_…sql:40-48`). `id` is deliberately **not** hashed — it is
   IDENTITY-assigned and a rolled-back txn burns one, so hashing it would make
   the chain depend on gap-free sequences Postgres does not promise; ordering
   carries the sequence, content carries the meaning (`0062_…sql:50-55`).
2. **Signed checkpoints** — `audit_checkpoints` (`0062_…sql:73-89`) pins the
   chain head at a moment with a **detached ASCII-armored signature** over it,
   using the same agent signing key as `scripts/sign-manifest.sh`. Without the
   signature the chain would be only internally consistent — anyone who can
   rewrite history could recompute it (`0062_…sql:74-79`).
3. **Signed archive stubs** — `audit_archive_stubs` (`0062_…sql:91-117`).
   Retention decision (operator, 2026-09-21): audit rows are **not pruned in
   place** — anything past the retention horizon is checkpointed, signed,
   exported off-box (NAS/URI), and replaced on-device by a condensed **signed
   STUB** carrying `archive_sha256` + `archive_uri` + `signature`. The stub keeps
   the audit question answerable without keeping every row resident; deleting
   audit rows with no signed successor is the one thing this design never does
   (`0062_…sql:11-18`, `91-99`).

---

## 3. Site-origin provenance — `0061_site_origin.sql`

Adds `site_origin TEXT NOT NULL DEFAULT 'local'` to the semantic-layer graph
tables `semantic_note_derivations` and `semantic_note_instance_refs`
(`0061_…sql:31-42`). Idempotent (`ADD COLUMN IF NOT EXISTS`).

- **Why before Phase 2/3** — adding it now is free (every existing row takes the
  DEFAULT in one ALTER); adding it *after* Phase 2/3 generate causal/cluster
  edges means backfilling a provenance value that is no longer trivially
  knowable, since a derived edge's origin site cannot be reconstructed from the
  edge itself once multiple sites contribute. Cheap now, expensive later — the
  whole reason it lands ahead of the phases (`0061_…sql:3-10`). See
  `docs/GEOMETRIC_REASONING_DESIGN_2026-09-17.md` AS-BUILT section.
- **Not a commitment to ANP/federation** — that research concluded ANP's
  identity layer is MVP-stage and not a safe production bet before 2027; this one
  column is worth doing on `main` regardless because its cost is asymmetric in
  time and it is protocol-independent — "which site produced this row" is
  meaningful even for a single-site deployment (distinguishes a restored backup,
  a migrated host, or a rebuilt vault) (`0061_…sql:12-19`).
- **Value semantics** — `'local'` is the sentinel for "produced by this
  deployment before any site identity was configured"; once a deployment has a
  real site id, `compile.py` writes that instead. Deliberately `TEXT`, not an
  enum/FK — federated site identifiers are assigned by whoever runs the
  federation, not by this schema (`0061_…sql:21-28`).
- **Not indexed** — every row in a single-site deployment carries the same value,
  so an index would be write overhead with no selectivity; add one when a second
  site actually contributes (`0061_…sql:44-48`). Same "don't index a low-cardinality
  per-entry column" reasoning cited by 0065's lock columns.
