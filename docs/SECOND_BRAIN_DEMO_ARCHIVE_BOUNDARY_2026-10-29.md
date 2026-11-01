# Second Brain demo archive — promoted input only

Operator decision, 2026-10-29 UTC: use **all cleared data already promoted into
the isolated demo database**, including scrubbed snapshots and synthetic replay
gap-fill rows. Raw production recorder snapshots are excluded. This note
describes the prepared repair; it is not a claim that it has been deployed.

## Source and output

`src/demo/promoted_archive.py` uses only `demo_pg_conn()` and the isolated demo
store's `snapshots`, `meta` and `scrub_ledger` tables. It never opens the raw
`demo_snapshots` source and has no production-data fallback. The existing
`scripts/scrub-demo-source.py` remains the one promotion writer; this repair
does not initiate a scrub, backfill, purge, migration or promotion.

The source reader requires a completed ledger receipt matching both
`meta.window_end` and `meta.promoted_at`. It reads metadata and payloads in one
repeatable-read, read-only transaction. Missing/inconsistent promotion state
raises and prevents the archive write. These are consistency checks against
the trusted promotion metadata, not a new per-row cryptographic receipt or a
fresh audit-chain security certification.

The selected window is the trailing 30 hours ending at that promotion's
window boundary, not the current wall-clock time. Both `synthetic=false`
scrubbed rows and `synthetic=true` replay fill are included. At the same capture
time, the representative scrubbed row wins over a replay-fill row. Counts
include both kinds and report the replay-fill count explicitly. Content older
than the lower bound or newer than the promotion boundary is excluded.

The existing daily vault destination remains `01-Sources/demo-archive/`.
Notes and index tags identify this as cleared promoted demo material, with
`authority: demonstration_only`, the promotion sequence/hash and timestamps.
It must not be represented as current operational feed evidence. The existing
scrub gate remains in the write path; a block/error exits unsuccessfully.

## Bounded resource use

The query computes counts/times in the database and transfers one representative
payload per endpoint, capped at 128 KiB before Python receives it. Decode is
capped at 64 KiB before JSON parsing, and each rendered excerpt at 2,000
characters. Oversized data is labelled truncated or decode-failed rather than
silently promoted to a full snapshot. Counts and timestamps remain available.
This addresses memory growth without claiming that a live exit 137 has been
proved to be an OOM or that production recovery has already occurred.

## Configuration and operator rollout

The private archiver keeps its existing production connection for the vault
index/usage bookkeeping; its archive input uses the demo pool exclusively.
It serves nothing publicly. The quadlet adds the demo socket and a dedicated
`/etc/corporatetraveldc/svc/demo-archive.env` containing only five DEMO_PG
connection keys. It does not load the whole demo env into the job: that would
also replace role/isolation settings needed by the index connection.

`scripts/service-env/generate.py` selects `/etc/corporatetraveldc/demo.env`
for this profile even when all profiles are generated. It requires the existing
demo application role/database, excludes admin/session tokens and writes
nothing if the demo connection configuration is missing or unsuitable. Other
profiles retain their normal source. The agent has not read or generated
production credential files.

After reviewing and signing the repair, the operator's profile-generation step
is:

```bash
sudo python3 scripts/service-env/generate.py --write demo-archive
```

Use the existing approved sign/build/provenance/rollout sequence. Do not execute
the staged unsigned module or clear a failure marker as proof of recovery.
Verify the generated profile by key names/permissions only, then validate the
job's completed promotion reference, classified vault output and successful
exit. Check the semantic compile separately after its bounded-memory repair.

## Verification limits

Isolated fixtures cover both cleared row kinds, completed/incomplete promotion
metadata, the promotion-relative window, future-row exclusion, representative
selection, payload/decode bounds, blocked/no-source write refusal, source
accessor separation and per-profile env generation. The SQL runs against a
fixture adapter; live PostgreSQL transaction/role/mount integration and the
scheduled unit's recovery require the operator rollout. No live archive,
promotion, service restart or credential change was executed by the agent.
