"""2026-10-14: the failover kickover guardrail runs on the host but wrote its refresh_feed
triggers to the host's /run/corporatetraveldc/triggers, which the poller container never sees
(it reads the podman volume corporatetraveldc-triggers). Since 09-18 no forced refresh reached
the poller. The guardrail now writes into the volume's host mountpoint."""
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts/failover-kickover-guardrail.py"


@pytest.fixture
def g():
    spec = importlib.util.spec_from_file_location("kick", PATH)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_uses_the_poller_volume_not_the_host_run_dir(g, monkeypatch):
    monkeypatch.delenv("DISPATCH_TRIGGER_DIR_HOST", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="/home/x/.local/share/containers/storage/volumes/corporatetraveldc-triggers/_data\n", stderr=""))
    assert str(g.host_trigger_dir()) == "/home/x/.local/share/containers/storage/volumes/corporatetraveldc-triggers/_data/triggers"


def test_explicit_override_wins(g, monkeypatch, tmp_path):
    monkeypatch.setenv("DISPATCH_TRIGGER_DIR_HOST", str(tmp_path))
    assert g.host_trigger_dir() == tmp_path


def test_unresolvable_volume_fails_loudly_instead_of_writing_nowhere(g, monkeypatch):
    monkeypatch.delenv("DISPATCH_TRIGGER_DIR_HOST", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=125, stdout="", stderr="no such volume"))
    with pytest.raises(RuntimeError, match="trigger volume"):
        g.host_trigger_dir()
