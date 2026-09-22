"""
Regression test for the 2026-08-26 C-21 fix (Opus blind review):
PushDedup._merge_write() only ever added keys, never removed stale ones
-- confirmed live at 4,814 keys / 328 KB for the notam dedup file alone,
rewritten in full on every alert. This locks in the corrected contract:
each write also evicts entries whose dedup window closed long ago.
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common.push_dedup import PushDedup


def _dedup(tmp_path, name="t", secs=3600):
    with patch("common.push_dedup.config.state_dir", return_value=str(tmp_path)):
        d = PushDedup(name, dedup_secs=secs)
        d._load()
    d._path = lambda: tmp_path / f"pusher-{name}-dedup.json"
    return d


def test_stale_keys_are_evicted_on_write(tmp_path):
    d = _dedup(tmp_path, secs=3600)
    # Simulate a long-stale entry already on disk (older than 10x dedup_secs).
    stale_ts = time.time() - (3600 * 10) - 1
    d._path().write_text(json.dumps({"stale-key": {"ts": stale_ts, "hash": "old"}}))

    d.record("fresh-key", "new-hash")

    on_disk = json.loads(d._path().read_text())
    assert "stale-key" not in on_disk, "stale entry should have been evicted"
    assert "fresh-key" in on_disk


def test_recent_keys_survive_eviction(tmp_path):
    d = _dedup(tmp_path, secs=3600)
    recent_ts = time.time() - 60  # 1 minute old, well within the 10x window
    d._path().write_text(json.dumps({"recent-key": {"ts": recent_ts, "hash": "still-good"}}))

    d.record("fresh-key", "new-hash")

    on_disk = json.loads(d._path().read_text())
    assert "recent-key" in on_disk, "a recently-written entry must not be evicted"
    assert "fresh-key" in on_disk
