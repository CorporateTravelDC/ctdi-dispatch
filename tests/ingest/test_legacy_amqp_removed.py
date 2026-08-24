"""
Regression test locking in the removal of the legacy AMQP SWIM client
(2026-07-19).

ingest/swim.py (the pre-NMS FAA SWIM/SCDS subscriber over AMQP 1.0, via
python3-qpid-proton) and its config (SwimConfig/SwimFeedConfig in
ingest/config.py) have been permanently removed. swim_client.py (Solace
PubSub+ / NMS) is the only SWIM transport now -- there is no fallback to
the legacy path, by design, per operator instruction: "kill off all the
legacy paths permanently."

These tests exist so a future re-add (e.g. someone restoring swim.py from
git history "just in case", or re-adding a SwimConfig field for a new
feed) gets caught immediately instead of silently reintroducing a second,
overlapping nationwide ingest path.
"""
import importlib.util
import sys
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import ingest.config as config
import ingest.main as ingest_main


def test_ingest_swim_module_does_not_exist():
    assert importlib.util.find_spec("ingest.swim") is None, (
        "ingest/swim.py (legacy AMQP client) must stay removed -- "
        "NMS/Solace (swim_client.py) is the only SWIM transport"
    )


def test_config_has_no_swim_field():
    field_names = {f.name for f in fields(config.Config)}
    assert "swim" not in field_names
    assert "nms" in field_names, "NMS config must remain -- it's the live transport"


def test_config_module_has_no_legacy_swim_classes():
    assert not hasattr(config, "SwimConfig")
    assert not hasattr(config, "SwimFeedConfig")
    assert not hasattr(config, "_swim_feed")


def test_main_does_not_reference_legacy_swim_module():
    import inspect
    src = inspect.getsource(ingest_main)
    # main.py must not import the legacy module name as a bare `swim` symbol.
    # swim_client is fine (that's the live NMS transport); a bare "swim" import
    # or "swim.run(" call would mean the legacy path came back.
    assert "import swim\n" not in src.replace("import swim_client", "")
    assert "swim.run(" not in src


def test_main_has_no_legacy_amqp_task():
    import inspect
    src = inspect.getsource(ingest_main.main)
    assert "legacy AMQP" in src or "swim_nms" in src  # documented removal, NMS task present
    assert "cfg.swim.enabled" not in src
