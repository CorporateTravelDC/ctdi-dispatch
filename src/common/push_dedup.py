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
import hashlib
import json
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

    # -- internal state I/O -------------------------------------------------

    def _path(self) -> pathlib.Path:
        return pathlib.Path(config.state_dir()) / f"pusher-{self.name}-dedup.json"

    def _load(self) -> dict:
        if self._state is None:
            p = self._path()
            if p.exists():
                try:
                    self._state = json.loads(p.read_text())
                except Exception:
                    self._state = {}
            else:
                self._state = {}
        return self._state

    def _save(self) -> None:
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self._state or {}))

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
        state = self._load()
        state[key] = {"ts": time.time(), "hash": content_key}
        self._save()

    def get_raw(self, key: str) -> dict:
        """Return the raw stored dict for a key (used for numeric wx deltas)."""
        return self._load().get(key, {})

    def set_raw(self, key: str, data: dict) -> None:
        """Store an arbitrary dict for a key; sets ts automatically."""
        state = self._load()
        state[key] = {**data, "ts": time.time()}
        self._save()
