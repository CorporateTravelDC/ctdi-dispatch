#!/usr/bin/env bash
# setup-disk-swap.sh -- NVMe overflow swapfile BELOW zram (2026-09-06).
#
# Why: the box's only swap is 8 G of zram (compressed RAM). That is the right
# first tier -- but it is still RAM, so it cannot make room for anything;
# it only buys ~2-3x on what is already there. corporatetraveldc-pgsql
# (docs/POSTGRES_MIGRATION.md) is capped at 1.5 G RAM + 1.5 G swap, and
# production.slice was already at ~10.5 G of its 13.8 G cap with the two
# resident llama tiers holding 7.2 G. An NVMe swapfile is what actually
# lets that fit: zram (priority 100) still absorbs first, and this file
# (priority 10) only takes what zram cannot. A swapped-out Postgres is
# slow, not broken; the llama tiers keep their MemoryLow floors and are
# never the thing that spills.
#
# Root filesystem is BTRFS. A btrfs swapfile MUST be NOCOW, uncompressed,
# fully preallocated, and must not live inside a subvolume that gets
# snapshotted -- `btrfs filesystem mkswapfile` (btrfs-progs >= 6.1; this box
# has 7.1) does all of that correctly; a hand-rolled `fallocate`+`mkswap`
# on btrfs fails at `swapon` with "Invalid argument". It also goes in its own
# subvolume so snapshots of /root never try to include an 8 G swapfile.
#
# Why 8 G (measured 2026-09-06, not a guess): every container quadlet on the
# box sets --memory-swap equal to Memory, i.e. a swap allowance of ZERO --
# containers are OOM-killed, never paged. The only things that can reach
# this file are corporatetraveldc-pgsql (the one quadlet with a swap
# allowance, capped at 1.5 G) and uncontained host processes (the llama
# tiers, which have MemoryLow floors, and user services/shells), and only
# once zram's 8 G of uncompressed pages is full (~1.2 G of real RAM at the
# measured ~7:1 ratio). Measured worst case is ~1.5 G + host spill, call it
# 3-4 G; 8 G is ~2x that. Bigger buys nothing: 8 G of ACTIVE NVMe swap on a
# Pi 5 is already a thrashing box, and a larger file only stretches the
# thrash before the OOM kill. Cumulative zram traffic since boot was ~17 GB
# out / ~14 GB in, so the pressure this buffers is real, not hypothetical.
#
# Root only (swapon, fstab, subvolume create). Idempotent: re-running with
# the swap already active is a no-op. Undo: `swapoff <file>`, delete the
# fstab line, `btrfs subvolume delete /var/swap`.
set -euo pipefail

SWAP_DIR="${SWAP_DIR:-/var/swap}"
SWAP_FILE="${SWAP_FILE:-$SWAP_DIR/nvme-overflow.swap}"
SWAP_SIZE="${SWAP_SIZE:-8g}"
# zram0 is priority 100. Lower number = used later. Keep this well below.
SWAP_PRIO="${SWAP_PRIO:-10}"

if [[ $EUID -ne 0 ]]; then
    echo "setup-disk-swap: must run as root (sudo)" >&2
    exit 1
fi

if grep -q "^$SWAP_FILE " /proc/swaps; then
    echo "setup-disk-swap: $SWAP_FILE already active -- nothing to do"
    swapon --show
    exit 0
fi

# -T: the filesystem CONTAINING the path. Without it findmnt only matches an
# exact mountpoint, and /var is not one on this box (it lives in the root
# subvolume) -- the guard returned '' and refused on the first three runs.
fstype="$(findmnt -no FSTYPE -T "$(dirname "$SWAP_DIR")")"
if [[ "$fstype" != "btrfs" ]]; then
    echo "setup-disk-swap: expected btrfs at $(dirname "$SWAP_DIR"), found '$fstype' -- this script is btrfs-specific, refusing" >&2
    exit 1
fi

if [[ ! -d "$SWAP_DIR" ]]; then
    echo "setup-disk-swap: creating subvolume $SWAP_DIR (kept out of snapshots)"
    btrfs subvolume create "$SWAP_DIR"
fi
chmod 700 "$SWAP_DIR"

if [[ ! -f "$SWAP_FILE" ]]; then
    echo "setup-disk-swap: creating $SWAP_SIZE NOCOW swapfile at $SWAP_FILE (preallocating -- ~10-20 s on NVMe)"
    btrfs filesystem mkswapfile --size "$SWAP_SIZE" "$SWAP_FILE"
fi
chmod 600 "$SWAP_FILE"

swapon -p "$SWAP_PRIO" "$SWAP_FILE"

fstab_line="$SWAP_FILE none swap defaults,pri=$SWAP_PRIO 0 0"
if ! grep -qF "$SWAP_FILE" /etc/fstab; then
    echo "setup-disk-swap: adding fstab entry"
    printf '%s\n' "# NVMe overflow swap below zram (scripts/setup-disk-swap.sh, 2026-09-06)" "$fstab_line" >> /etc/fstab
fi

echo "setup-disk-swap: done"
swapon --show
