"""
push_dedup -- shared 1-hour push dedup for pusher and skill-level ntfy sends.

State stored in config.state_dir()/pusher-{name}-dedup.json.

Usage:
    from common.push_dedup import PushDedup, content_hash

    dedup = PushDedup("tfr")
    key   = content_hash(stable_key_string)
    if dedup.should_push("enrichment", key):
        send_ntfy(...)
        dedup.record("enrichment", key)

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


def content_hash(text: str) -> str:
    """12-char MD5 hex digest of text -- stable key for dedup comparison."""
    return hashlib.md5(text.encode()).hexdigest()[:12]


class PushDedup:
    """
    Per-topic dedup state manager.

    name: short identifier used in the state file name (e.g. "tfr", "wx", "route")
    dedup_secs: suppression window; defaults to 1 hour
    """

    def __init__(self, name: str, dedup_secs: int = DEFAULT_DEDUP_SECS) -> None:
        self.name = name
        self.dedup_secs = dedup_secs
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
        rewritten in full on every single alert. Entries whose dedup
        window closed long ago have zero ongoing value, so each write now
        also evicts anything older than 10x this instance's own
        dedup_secs (generous margin past the window that actually matters
        for correctness) before persisting.
        """
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_path = p.with_name(p.name + ".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                disk = self._read_disk()   # current truth, incl. peer writes
                disk[key] = entry
                stale_cutoff = time.time() - (self.dedup_secs * 10)
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
        Return True if push should proceed.

        key:         stable slot identifier (TFR ID, station name, skill name, ...)
        content_key: hash of the meaningful content -- use content_hash()
        hot:         if True, bypass dedup entirely (VIP/POTUS priority-5)
        """
        if hot:
            return True
        last = self._load().get(key, {})
        content_changed = last.get("hash") != content_key
        hour_elapsed = (time.time() - last.get("ts", 0)) >= self.dedup_secs
        return bool(content_changed or hour_elapsed or not last.get("ts"))

    def record(self, key: str, content_key: str) -> None:
        """Record a successful push so subsequent calls respect the window."""
        self._merge_write(key, {"ts": time.time(), "hash": content_key})

    def get_raw(self, key: str) -> dict:
        """Return the raw stored dict for a key (used for numeric wx deltas)."""
        return self._load().get(key, {})

    def set_raw(self, key: str, data: dict) -> None:
        """Store an arbitrary dict for a key; sets ts automatically."""
        self._merge_write(key, {**data, "ts": time.time()})
