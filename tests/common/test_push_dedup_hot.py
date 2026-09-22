"""Regression tests for the 2026-08-16 drift-audit hot=True dedup bypass.

Two call sites (pusher push_vip_tfrs, poller route_impact) passed a
hardwired hot=True to PushDedup.should_push because the pushes themselves
were "hot" priority-5 -- but the contract says hot=True bypasses dedup
entirely, so both suppression windows were no-ops. These tests pin the
PushDedup contract itself and the fixed push_vip_tfrs behavior.
"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from common.push_dedup import PushDedup, content_hash


def _dedup(tmp_path, name="t", secs=3600):
    with patch("common.push_dedup.config.state_dir", return_value=str(tmp_path)):
        d = PushDedup(name, dedup_secs=secs)
        # force state load/save inside the patched dir
        d._load()
    # keep subsequent _save() writing to the same tmp dir
    d._path = lambda: tmp_path / f"pusher-{name}-dedup.json"  # type: ignore
    return d


class TestPushDedupContract:
    def test_hot_true_always_bypasses(self, tmp_path):
        d = _dedup(tmp_path)
        d.record("slot", "h1")
        # within the window, same content: plain call suppresses...
        assert d.should_push("slot", "h1") is False
        # ...but hot=True bypasses dedup entirely -- this is WHY a
        # hardwired hot=True at a call site defeats the whole mechanism.
        assert d.should_push("slot", "h1", hot=True) is True

    def test_content_change_breaks_through(self, tmp_path):
        d = _dedup(tmp_path)
        d.record("slot", "h1")
        assert d.should_push("slot", "h2") is True

    def test_window_elapse_breaks_through(self, tmp_path):
        d = _dedup(tmp_path, secs=1)
        d.record("slot", "h1")
        assert d.should_push("slot", "h1") is False
        time.sleep(1.05)
        assert d.should_push("slot", "h1") is True

    def test_distinct_slots_do_not_cross_contaminate(self, tmp_path):
        # The shape of the tfms/fdps shared-slot bug: distinct entities
        # must each get their own slot, not overwrite one literal key.
        d = _dedup(tmp_path)
        d.record(content_hash("APREQ:PDX"), "c1")
        d.record(content_hash("MIT:ATL"), "c2")
        assert d.should_push(content_hash("APREQ:PDX"), "c1") is False
        assert d.should_push(content_hash("MIT:ATL"), "c2") is False


class TestCrossProcessSharing:
    """2026-08-16 drift audit: the state file is shared by the ingest
    containers and the poller. Two PushDedup instances over the same file
    (standing in for two processes) must not clobber each other's records,
    and each must see the other's writes."""

    def _two_procs(self, tmp_path, name="shared"):
        # Two independent instances, same file -> two processes. Pin _path
        # on each so all I/O stays in tmp_path regardless of config patching.
        a = PushDedup(name)
        b = PushDedup(name)
        target = tmp_path / f"pusher-{name}-dedup.json"
        a._path = lambda: target  # type: ignore
        b._path = lambda: target  # type: ignore
        return a, b

    def test_record_from_peer_is_visible(self, tmp_path):
        a, b = self._two_procs(tmp_path)
        # A fires and records an event.
        assert a.should_push("flight:AAL1", content_hash("landed")) is True
        a.record("flight:AAL1", content_hash("landed"))
        # B (a different process) must now SEE it and suppress the dup.
        assert b.should_push("flight:AAL1", content_hash("landed")) is False

    def test_peer_writes_are_not_clobbered(self, tmp_path):
        a, b = self._two_procs(tmp_path)
        # A records slot X; B records slot Y. Neither may erase the other.
        a.record("slotX", "cx")
        b.record("slotY", "cy")
        # A must still see its own X AND B's Y; same for B.
        assert a.should_push("slotX", "cx") is False   # A's own, intact
        assert a.should_push("slotY", "cy") is False   # B's, visible to A
        assert b.should_push("slotX", "cx") is False   # A's, visible to B
        assert b.should_push("slotY", "cy") is False   # B's own, intact

    def test_interleaved_writes_all_survive(self, tmp_path):
        a, b = self._two_procs(tmp_path)
        for i in range(25):
            a.record(f"a{i}", "c")
            b.record(f"b{i}", "c")
        # Every one of the 50 records must be present on disk.
        import json
        state = json.loads((tmp_path / "pusher-shared-dedup.json").read_text())
        assert len([k for k in state if k.startswith("a")]) == 25
        assert len([k for k in state if k.startswith("b")]) == 25


class TestPushVipTfrs:
    def _run(self, tmp_path, tfrs):
        import pusher.main as pm
        with patch.object(pm.db, "get_active_tfrs", return_value=tfrs), \
             patch.object(pm.db, "mark_tfr_notified"), \
             patch.object(pm, "hot_push", return_value=True) as hp, \
             patch("common.push_dedup.config.state_dir", return_value=str(tmp_path)):
            pm._tfr_dedup = PushDedup("tfr")
            pm._tfr_dedup._path = lambda: tmp_path / "pusher-tfr-dedup.json"  # type: ignore
            first = pm.push_vip_tfrs()
            second = pm.push_vip_tfrs()  # same cycle content -- must dedup now
            tfrs[0]["enriched_text"] = "changed narrative"
            third = pm.push_vip_tfrs()  # changed content -- fires again
            return first, second, third, hp

    def test_vip_tfr_second_cycle_suppressed(self, tmp_path):
        tfrs = [{"tfr_id": "6/1234", "is_vip": 1, "enriched_text": "POTUS TFR"}]
        first, second, third, hp = self._run(tmp_path, tfrs)
        assert first == 1, "first sighting must push"
        assert second == 0, "unchanged VIP TFR 30s later must be suppressed"
        assert third == 1, "changed enrichment must break through"
        # 2 hot_push calls per fired cycle (tfr-alert + hot-alerts)
        assert hp.call_count == 4

    def test_non_vip_never_pushes(self, tmp_path):
        tfrs = [{"tfr_id": "6/9999", "is_vip": 0, "enriched_text": "routine"}]
        first, second, third, hp = self._run(tmp_path, tfrs)
        assert (first, third) == (0, 0)
        assert hp.call_count == 0
