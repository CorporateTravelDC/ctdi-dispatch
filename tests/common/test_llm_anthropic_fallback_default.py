"""
Regression test for the 2026-08-26 C-8 fix (Opus blind review):
ANTHROPIC_FALLBACK_ENABLED used to default to "true" (open) when unset,
so any deployment that forgot to set it -- or a future deploy on THIS box
that dropped dispatch.env -- would silently allow cloud egress with zero
code change, with only the absence of ANTHROPIC_API_KEY actually
preventing it. This locks in the corrected default: fail-closed unless
explicitly opted in.
"""
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import common.llm as llm_mod


def _reload_with_env(env: dict):
    with patch.dict(os.environ, env, clear=False):
        importlib.reload(llm_mod)
        return llm_mod.ANTHROPIC_FALLBACK_ENABLED


def test_defaults_closed_when_unset():
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_FALLBACK_ENABLED"}
    with patch.dict(os.environ, env, clear=True):
        importlib.reload(llm_mod)
        assert llm_mod.ANTHROPIC_FALLBACK_ENABLED is False


def test_explicit_true_still_opens_the_gate():
    assert _reload_with_env({"ANTHROPIC_FALLBACK_ENABLED": "true"}) is True


def test_explicit_false_stays_closed():
    assert _reload_with_env({"ANTHROPIC_FALLBACK_ENABLED": "false"}) is False


def teardown_module(module):
    # Restore the module to whatever the real environment says, so this
    # test file doesn't leave process-global state changed for whatever
    # runs after it in the same pytest session.
    importlib.reload(llm_mod)
