"""
push_dedup -- shared forward-only, content-hash-driven push dedup for the
SWIM parsers, watchlist path, pusher and skill-level ntfy sends.

State stored in config.state_dir()/pusher-{name}-dedup.json.

Usage:
    from common.push_dedup import PushDedup, content_hash

    dedup = PushDedup("tfr")
    key   = content_hash(stable_key_string)
    if dedup.should_push("enrichment", key):
        send_ntfy(...)
        dedup.record("enrichment", key)

Semantics (redesigned 2026-09-03, operator directive after the UAL1369
TMI incident -- see should_push()'s docstring for the full writeup):

  should_push()          FORWARD-ONLY. Fires when a slot's content hash
                         first appears or genuinely changes from what is
                         stored. Elapsed time alone NEVER re-fires an
                         unchanged alert (the pre-2026-09-03 behavior --
                         "content changed OR dedup_secs elapsed" -- meant
                         every routine SWIM rebroadcast of an unchanged
                         condition re-paged once per window, forever).
  should_push_periodic() The old OR semantics, kept under an explicit name
                         for the few callers whose *design* is time-based:
                         TTL-style idempotency guards, episode gates on
                         alerts with no varying content (fdps proximity,
                         flight-landing), and deliberate still-active
                         heartbeats (severe ITWS wx, feed-health, active
                         VIP TFR). New callers should default to
                         should_push(); reach for this only when a
                         periodic re-reminder is genuinely the point.

hot=True bypasses dedup entirely -- use for VIP/POTUS priority-5 events.
"""
import fcntl
import hashlib
import json
import os
import pathlib
import time

from common import config

DEFAULT_DEDUP_SECS = 3600  # 1 hour

# Retention floor for forward-only memory. Forward-only suppression is only
# as good as the state's lifetime: once a slot's entry is evicted (or aged
# past retention), the next unchanged rebroadcast looks brand-new and
# re-fires. The pre-redesign eviction horizon (dedup_secs * 10) was fine
# when dedup_secs was the re-fire window anyway, but under forward-only
# semantics it would have quietly re-introduced the time-based re-fire on a
# 10x cadence (e.g. the watchlist's 300s window -> re-fire every ~50 min
# for a still-active TMI constraint). 7 days comfortably outlives any
# single flight/train/vessel activity span and any routine
# rebroadcast-while-active condition, while still bounding state growth --
# an unchanged, continuously rebroadcast condition re-fires at most once
# per retention period, by design (a deterministic slow heartbeat rather
# than one dependent on GC timing).
_MIN_RETENTION_SECS = 7 * 86400


def content_hash(text: str) -> str:
    """12-char MD5 hex digest of text -- stable key for dedup comparison."""
    return hashlib.md5(text.encode()).hexdigest()[:12]


def bucket_count(n: int, band: int = 5) -> str:
    """Coarse-bucket a churny integer (track/queue counts) for use inside a
    content hash, so continuous +/-1 jitter hashes identically while a real
    jump to a different band still reads as changed content -- the numeric
    counterpart of shared/watchlist.py's _bucket_timestamp(). A value
    sitting exactly on a band boundary can ping-pong across it (19<->20);
    that residual chatter is accepted, same as the timestamp bucketing."""
    return f"{(n // band) * band}-{(n // band) * band + band - 1}"


class PushDedup:
    """
    Per-topic dedup state manager.

    name: short identifier used in the state file name (e.g. "tfr", "wx", "route")
    dedup_secs: re-fire window for should_push_periodic() callers only;
                defaults to 1 hour. Forward-only should_push() ignores it.
    retention_secs: how long a recorded slot is remembered (suppression
                lifetime + on-disk eviction horizon). Defaults to
                max(dedup_secs * 10, 7 days) -- the old 10x-window eviction,
                floored so forward-only memory outlives any routine
                rebroadcast-while-active condition. Pass explicitly for
                TTL-style guards that want their state gone quickly.
    """

    def __init__(self, name: str, dedup_secs: int = DEFAULT_DEDUP_SECS,
                 retention_secs: int | None = None) -> None:
        self.name = name
        self.dedup_secs = dedup_secs
        self.retention_secs = (retention_secs if retention_secs is not None
                               else max(dedup_secs * 10, _MIN_RETENTION_SECS))
        self._state: dict | None = None
        self._loaded_mtime: float | None = None

    # -- internal state I/O -------------------------------------------------
    #
    # 2026-08-16 drift audit -- cross-process correctness fix.
    #
    # This state file is shared by MULTIPLE long-running processes: the
    # per-feed ingest containers (ingest-fdps, ingest-tfms, ...) and the
    # poller all call watchlist_event_hit()/skill dedups against the same
    # DISPATCH_STATE_DIR volume. The previous implementation read the file
    # ONCE per process (_state cached for the process lifetime) and, on
    # record(), wrote the whole cached dict back. Consequences:
    #   (a) process B never saw process A's recorded events -> the exact
    #       cross-container double-fire the watchlist docstring claims to
    #       prevent could still happen;
    #   (b) every save from B overwrote A's records with B's stale copy,
    #       so records were silently lost over time.
    # Fix: reads reload when the file's mtime moved (so a process sees peer
    # writes), and every mutation is an flock-guarded read-modify-write that
    # merges onto the CURRENT on-disk dict and replaces it atomically
    # (temp file + os.replace). flock serializes concurrent writers so no
    # update is lost; the state volume is local (/var/lib), where flock is
    # reliable. Public API (should_push/record/get_raw/set_raw) is unchanged.

    def _path(self) -> pathlib.Path:
        return pathlib.Path(config.state_dir()) / f"pusher-{self.name}-dedup.json"

    def _read_disk(self) -> dict:
        p = self._path()
        try:
            return json.loads(p.read_text())
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _load(self) -> dict:
        """Return in-memory state, reloading if the file changed on disk.

        mtime-aware so a process picks up peer processes' writes instead of
        serving its stale first-read forever.
        """
        p = self._path()
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if self._state is None or mtime != self._loaded_mtime:
            self._state = self._read_disk() if mtime is not None else {}
            self._loaded_mtime = mtime
        return self._state

    def _merge_write(self, key: str, entry: dict) -> None:
        """Atomically merge one key into the on-disk dict under an exclusive
        lock, then refresh the in-memory cache to match.

        2026-08-26 fix (Opus blind review C-21): this only ever ADDED keys
        -- nothing ever removed a stale one (a NOTAM ID long expired, a
        one-off skill-name key from months ago), so the file only grew,
        confirmed live at 4,814 keys / 328 KB for the notam dedup alone,
        rewritten in full on every single alert. Each write evicts
        anything older than this instance's retention_secs before
        persisting -- 2026-09-03: that horizon is now retention_secs (see
        __init__) rather than a bare dedup_secs * 10, because under
        forward-only should_push() semantics the entry's lifetime IS the
        suppression guarantee, and should_push() treats an over-horizon
        entry as forgotten anyway, so eviction and suppression expire at
        the same deterministic moment instead of GC timing deciding.
        """
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_path = p.with_name(p.name + ".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                disk = self._read_disk()   # current truth, incl. peer writes
                disk[key] = entry
                stale_cutoff = time.time() - self.retention_secs
                disk = {
                    k: v for k, v in disk.items()
                    if k == key or v.get("ts", 0) >= stale_cutoff
                }
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(json.dumps(disk))
                os.replace(tmp, p)         # atomic swap
                self._state = disk
                try:
                    self._loaded_mtime = p.stat().st_mtime
                except OSError:
                    self._loaded_mtime = None
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    # -- public API ----------------------------------------------------------

    def should_push(self, key: str, content_key: str, hot: bool = False) -> bool:
        """
        Return True if push should proceed. FORWARD-ONLY (2026-09-03).

        key:         stable slot identifier (TFR ID, station name, skill name, ...)
        content_key: hash of the meaningful content -- use content_hash()
        hot:         if True, bypass dedup entirely (VIP/POTUS priority-5)

        Fires on a slot's first sighting and whenever its content hash
        differs from what record() last stored; an unchanged hash stays
        suppressed no matter how much time has passed (up to
        retention_secs, past which the entry is as good as evicted and a
        rebroadcast fires once more). The previous semantics ("content
        changed OR dedup_secs elapsed") re-fired byte-identical alerts
        purely on the clock: root-caused live 2026-09-02 via UAL1369,
        whose three concurrent unchanged TFMS TMI flow-constraint
        assignments each re-paged 7 times in 42 minutes -- TFMS
        rebroadcasts the same active TMI roughly every 5-8 min as routine
        SWIM chatter, which straddled the watchlist's 300s window, so
        every rebroadcast tripped the elapsed-time branch despite the
        (correctly) unchanged content hash. Callers whose design is
        genuinely time-based use should_push_periodic() below.
        """
        if hot:
            return True
        last = self._load().get(key, {})
        ts = last.get("ts")
        if not ts:
            return True
        if (time.time() - ts) >= self.retention_secs:
            return True  # past memory horizon -- treat as never seen
        return last.get("hash") != content_key

    def should_push_periodic(self, key: str, content_key: str,
                             hot: bool = False) -> bool:
        """
        Legacy time-windowed semantics, kept under an explicit name
        (2026-09-03 redesign): True when the content hash changed OR
        dedup_secs elapsed since the last record() -- i.e. "at most one
        unchanged re-fire per window". ONLY for callers where periodic
        re-firing is the deliberate design: TTL idempotency guards
        (ntfy ambiguous-status), episode gates on constant-content alerts
        (fdps proximity, flight-landing), and still-active heartbeats
        (severe ITWS weather, ingest feed health, active VIP TFRs). Every
        state-of-the-world alert with real content belongs on
        should_push() instead.
        """
        if hot:
            return True
        last = self._load().get(key, {})
        content_changed = last.get("hash") != content_key
        window_elapsed = (time.time() - last.get("ts", 0)) >= self.dedup_secs
        return bool(content_changed or window_elapsed or not last.get("ts"))

    def record(self, key: str, content_key: str) -> None:
        """Record a successful push so subsequent calls respect the window."""
        self._merge_write(key, {"ts": time.time(), "hash": content_key})

    def get_raw(self, key: str) -> dict:
        """Return the raw stored dict for a key (used for numeric wx deltas)."""
        return self._load().get(key, {})

    def set_raw(self, key: str, data: dict) -> None:
        """Store an arbitrary dict for a key; sets ts automatically."""
        self._merge_write(key, {**data, "ts": time.time()})
