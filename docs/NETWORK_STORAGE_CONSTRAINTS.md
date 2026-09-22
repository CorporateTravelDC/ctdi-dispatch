# Network-attached storage — constraints for a future operator

Written 2026-09-19 alongside the reference-table + second-brain-index
Postgres migration (`docs/POSTGRES_MIGRATION.md`). Scope: what happens if a
future operator (not necessarily on a Pi, not necessarily in a regulated
industry with cloud restrictions) wants to grow storage onto a local NAS
cluster or a mapped network drive, reached over LAN/direct Ethernet/USB,
rather than local disk. This is not today's deployment — nothing here
changes CorporateTravelDC's own storage, which stays local SSD/NVMe on the
Pi. It is guardrail documentation for someone who forks this platform and
wants to scale storage without waiving durability.

## 1. The two kinds of "networked storage" are not interchangeable

**File-level network shares — SMB/CIFS (a mapped drive letter, "Z:\"),
NFS, WebDAV mounts.** The OS talks to a remote file-serving protocol; the
remote server decides how/when bytes actually hit disk, and its locking
and cache-coherency semantics are not the same as a local filesystem's.

**Block-level network storage — iSCSI, NVMe-oF, Fibre Channel.** The NAS
exports a raw block device over the wire; the client OS puts its own local
filesystem (ext4/xfs/zfs) on top, exactly as it would for a local disk.
From every process's point of view — including Postgres's — this **is**
local disk. fsync, `O_DIRECT`, POSIX advisory locks: all real, all honored
by the local filesystem driver, none of it deferred to a remote peer with
different guarantees.

This is not an NFS-specific problem, which is the correction to make here:
Postgres's own documentation is just as explicit that its data directory
should not live on **any** file-level network share (NFS or SMB/CIFS
alike) — same class of risk (unclean shutdown or network partition mid-write
can silently violate durability, not just fail loudly), same verdict.

## 2. What this means for a NAS-backed deployment

**Not supported, for the live Postgres data directory (`PGDATA`):**
- A mapped network drive / "Z: drive" (SMB/CIFS)
- An NFS mount
- Any other file-level network filesystem

Flag this explicitly as a hard constraint of the platform as designed, not
a soft recommendation. A future operator who puts `PGDATA` on one of these
anyway is operating outside every durability guarantee this platform's
Postgres migration was built to get (see `docs/POSTGRES_MIGRATION.md` §1) —
they would need to either accept that as a known, documented risk of their
deployment, or get it in writing as an explicit waiver from whoever owns
that decision. This document is that flag.

**Supported, within the platform's existing guardrails:**

1. **iSCSI/NVMe-oF block device from the NAS, local filesystem on top.**
   The NAS presents a LUN; the compute host (Pi or successor) mounts it
   like any local disk, formats it, points `PGDATA` at it. Postgres never
   knows the bytes are network-backed. This is the standard, blessed way
   to run Postgres against a SAN/NAS and requires no application changes —
   same unix-socket connection path this platform already uses
   (`docs/POSTGRES_MIGRATION.md` §3.1).

2. **A Postgres instance running natively on (or attached directly to)
   the NAS/cluster, reached over the wire via streaming or logical
   replication**, rather than sharing a filesystem at all. This is
   Postgres's own native scale-out mechanism — the platform's app
   containers would speak the normal Postgres wire protocol (TCP, not a
   socket) to a remote primary or read replica. This is the option that
   composes cleanest with "grow to a NAS cluster, not just bigger local
   disk," since it also gets you a real replica/HA story for free, not
   just more capacity.

3. **A dedicated small compute node co-located with the NAS**, running
   Postgres against block storage from the NAS via option 1, exposed to
   the rest of the platform over the LAN via option 2's wire protocol.
   Combines both — the practical answer if "the NAS" is really "a small
   cluster," not a single box.

Options 1-3 all stay inside the platform's existing guardrails: local
fsync semantics preserved, unix-socket-first connection pattern preserved
where the DB and app share a host, wire-protocol access where they don't.
None of them require touching `translate_sql()`, `db_backend.py`'s
connection pooling, or any application code — this is purely a
storage/ops decision, orthogonal to the SQLite→Postgres work.

**One thing that is fine on file-level network storage, explicitly:**
backups. WAL archives, `pg_dump`/`pg_basebackup` output, the manifest
snapshots this platform signs — none of that is being actively written to
by a live database process, so the durability concerns above don't apply.
A Z-drive or NFS mount is a perfectly reasonable backup target. The
constraint is specifically about the *live, actively-written* data
directory, not about where you park copies of it afterward.

## 3. Throughput vs. latency — the actual risk on Ethernet Direct / USB-C

Raw throughput is usually not the binding constraint for this platform's
workload (a relational OLTP-shaped stream of mostly small writes, not
sustained bulk transfer) — Gigabit Ethernet (~125 MB/s), 2.5GbE (~312 MB/s),
and USB-C 3.2 Gen2 (~1.25 GB/s theoretical, materially less in practice)
all comfortably exceed the platform's measured steady-state write rate.

**Latency is the real risk**, and it's easy to miss because it doesn't
show up as a throughput number. Every Postgres transaction commit does a
synchronous `fsync()` to WAL before it can report success — that's the
durability guarantee. On local NVMe that's sub-millisecond. Over a network
link (even a fast, low-latency one), every one of those fsyncs now
round-trips a network hop first. That directly inflates commit latency,
and at high transaction rates it can quietly undermine the very property
(fast, safe, concurrent commits) that justified moving off SQLite in the
first place — this is a correctness-adjacent concern, not just a
performance one.

Bulk operations (initial data loads, a large backfill like geometric
reasoning's Phase 0) are the one place raw throughput *does* matter more
than latency, since those are large sequential transfers rather than many
small commits — link speed genuinely gates how long those windows take.

**Bottom line for a future operator:** block-level (iSCSI/NVMe-oF) or
native replication (options 1/2 above) preserve local-equivalent commit
latency by design — the OS or Postgres itself owns the fsync semantics
either way. File-level shares don't, regardless of how fast the link is,
which is why they're the one that gets flagged as unsupported rather than
"slow."
