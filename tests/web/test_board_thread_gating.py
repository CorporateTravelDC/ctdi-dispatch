"""
Regression test for the 2026-08-25 C-5 fix (Opus blind review):
GET /api/v1/board served every thread anonymously, including `research`,
which second_brain_research_board_mirror.py populates with real vault
personal-research notes -- confirmed live, 6 real messages were readable
by anyone on the internet. The `coord` thread's anonymous read is a
deliberate, documented design decision (Cowork has no tailnet/vault
access); every other thread now requires at least Tier 1.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main
from auth.auth import Tier


def test_coord_thread_stays_anonymous():
    with patch.object(web_main.db, "board_query", return_value=([], None)):
        result = asyncio.run(web_main.board_get(thread="coord", tier=Tier.T0))
    assert result.status_code == 200


def test_research_thread_rejects_anonymous_reader():
    with patch.object(web_main.db, "board_query", return_value=([], None)):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(web_main.board_get(thread="research", tier=Tier.T0))
    assert exc_info.value.status_code == 403


def test_research_thread_allows_tier1_reader():
    with patch.object(web_main.db, "board_query", return_value=([], None)):
        result = asyncio.run(web_main.board_get(thread="research", tier=Tier.T1))
    assert result.status_code == 200


def test_unknown_thread_defaults_to_gated_not_open():
    """A future thread name that isn't explicitly allowlisted must default
    to gated -- the fix is an allowlist of what's safe to leave open, not
    a denylist of what's known-sensitive today."""
    with patch.object(web_main.db, "board_query", return_value=([], None)):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(web_main.board_get(thread="some-new-thread", tier=Tier.T0))
    assert exc_info.value.status_code == 403
