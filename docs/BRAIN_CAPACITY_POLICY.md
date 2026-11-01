# Heavy-job capacity policy

Operator decisions, October 30–31, 2026. This source is a draft until installed
from the operator's signed checkout and proved with fresh runtime receipts.

## Resource contract

The registry in `config/heavy-job-policy.json` covers 26 long or heavy jobs:
Second Brain nightly/weekly work, semantic and knowledge graph compilation,
weekly cross-link work, RSS, index scans, archival and the related scheduled
reports. The renderer produces 25 Quadlet drop-ins and one host-service drop-in.
Run `python3 -B scripts/render-heavy-job-profiles.py --check` to check parity.

Each admitted job has:

- **2 GiB soft RAM pressure threshold; 2.5 GiB hard RAM ceiling.**
- **Zero swap initially.** At 90% of the hard RAM ceiling, **2.25 GiB**, grant
  **1 GiB swap**, at the next one-second observation without a cooldown.
- Subsequent **256 MiB swap increments** when at least 80% of its present swap
  allowance is used and RAM remains at least 2 GiB. Later grants are at least
  five seconds apart.
- **4 GiB maximum swap**, for **6.5 GiB combined RAM plus swap**. RAM is never
  automatically raised. All sizes use binary units.

The threshold uses the service's cgroup `memory.current`, including charged
cache, rather than process RSS. MemoryHigh triggers reclaim/throttling; it is
not a reservation. The kernel chooses which pages to swap. Granting an allowance
does not allocate or immediately populate that amount of swap.

Containers have fixed inner limits of 2.5 GiB RAM and 6.5 GiB RAM plus swap.
The service cgroup supplies the effective per-run swap allowance. Admission sets
that allowance to zero before the workload starts. The manager changes only
`MemorySwapMax`, through systemd runtime properties, and verifies the kernel
readback. This keeps the budget through ExecCondition, ExecStart and cleanup.
The next fresh job resets it to zero. Host swap devices and swappiness are not
changed. A one-second observer and finite swap do not guarantee survival of a
sudden memory burst.

## Shared queue and host swap budget

A persistent FIFO queue admits **at most two registered jobs at once**.
Waiting jobs leave ExecCondition cleanly and are retried by the manager when a
slot becomes available. Existing model-lane locks remain, so long model calls
are still serialized. A lease lasts through all container cleanup; a third job
cannot enter while the preceding service is deactivating.

Admission reserves the initial 1 GiB grant even before swap is enabled. Each
later grant reserves its increase under the same process-shared file lock.
Other host swap use plus the registered jobs' reservations must fit within
**60% of currently enabled host swap**, with at least 512 MiB free after a
reservation. Existing ancestor cgroup ceilings must permit the grant too.

This enforces admission and growth for these managed jobs. Independent services
can increase their own swap use after admission; the controller detects and
alerts on that breach and stops making grants. It does not kill unrelated work
or claim an absolute whole-host 60% guarantee. Two jobs each reaching their
4 GiB swap ceiling is permitted only when the global budget allows it.

Manager failure restarts the observer and alerts, without stopping admitted
work. Stale manager heartbeat, unknown running members or invalid state block
new admissions. Queued jobs survive a reboot. Interrupted admitted jobs are
reported and are not automatically replayed, because some perform writes.
Existing rolling/fixed maintenance conditions, timeout limits and schedules
are preserved. A lock-contention exit is recorded as skipped, not completed.
The retrofit-links timer remains held pending its separate dry-run review.
Operational briefs, the model server and continuous ingest are outside this
registry. No service is stopped to obtain a slot.

## Review and receipts

Capacity is reviewed every **15 elapsed days since the last healthy review**.
A persistent daily calendar checks whether it is due, catches up within about
24 hours online and retries unhealthy reviews without advancing that date.
It does not automatically increase RAM limits.

`/var/lib/corporatetraveldc/reports/brain-capacity/` contains dated reviews,
`latest.json` and `last-healthy.json`. Checks include all members' results,
effective RAM/swap policy, concurrency, host budget and semantic freshness.
Missing history is visible; held jobs are distinguished from expected runs.

`/var/lib/corporatetraveldc/reports/heavy-jobs/<unit>/` contains per-invocation
receipts: initial zero allowance, attempted/verified grants, charged memory/swap
peaks, invocation identity and actual workload outcome. Controller success and
workload success are distinct. At the maximum swap allowance the review calls
for capacity attention. Latest retained peaks are not a 15-day maximum.
No note bodies, credentials, raw journals or process arguments enter reports.

The temporary October 30 semantic trial advanced revision 47 to 48 in about
516 seconds, with a 1,742,340,096-byte peak (1.622 GiB). Its override was removed.
That proves the earlier 2 GiB trial only; it did not exercise this shared queue
or swap escalation. These need separate live acceptance.

## Operator installation and live pilot

Include this policy with the reconciled recovery source, smoke-test it, sign
and commit as the operator, then build from that signed tree. Any older combined
release pins must be regenerated for the final source. The v4 semantic-only
swap helper and older no-swap installer are superseded; do not run them.

From the reviewed signed production checkout:

```bash
cd /opt/corporatetraveldc/private/ctdi-dispatch-internal
python3 -B scripts/install-heavy-job-policy.py --pilot
```

The installer checks the signed manifest and generated profiles, waits for an
operator retry if any managed job is active, preserves backups and refuses
unknown local policy or unrelated PodmanArgs. It enables the manager and review
timer, without changing member timers. The optional pilot runs semantic and RSS
through the shared queue, samples every 15 seconds and requires fresh successful
workload receipts. Semantic acceptance also requires an advancing, coherent
sentinel/database/success-ledger sequence. Its receipt is readable by the agent
in `/srv/ctdc-codex/`. Existing failed jobs remain visible to the capacity review.

Graph, index, archive and trains/yachts recovery, a full fleet refresh and the
shared-context migration each require their own acceptance. A successful build
or a cleared failed-unit flag is not evidence of successful scheduled work.
