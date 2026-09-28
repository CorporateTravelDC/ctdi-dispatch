"""
Regression test for the 2026-08-25 C-5 fix (Opus blind review):
GET /api/v1/board served every thread anonymously, including `research`,
which second_brain_research_board_mirror.py populates with real vault
personal-research notes -- confirmed live, 6 real messages were readable
by anyone on the internet. The `coord` thread's anonymous read is a
deliberate, documented design decision (Cowork has no tailnet/vault
access); every other thread now requires at least Tier 1.

2026-09-06 addendum: a Tier-1 token cannot reach this route from Cowork's
side (the tunnel strips Authorization and nginx forces Tier 0 on that
host), so gated threads also accept the X-Board-Key credential Cowork
already holds -- same precedent as /api/v1/vault/research. Anonymous
reads of gated threads stay 403.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import web.main as web_main
from auth.auth import Tier


def _req(**headers) -> SimpleNamespace:
    """Minimal stand-in for starlette's Request: board_get only reads
    request.headers.get(...)."""
    return SimpleNamespace(headers=headers)


def test_coord_thread_stays_anonymous():
    with patch.object(web_main.db, "board_query", return_value=([], None)):
        result = asyncio.run(web_main.board_get(_req(), thread="coord", tier=Tier.T0))
    assert result.status_code == 200


def test_research_thread_rejects_anonymous_reader():
    with patch.object(web_main.db, "board_query", return_value=([], None)), \
            patch.object(web_main.db, "board_token_valid", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(web_main.board_get(_req(), thread="research", tier=Tier.T0))
    assert exc_info.value.status_code == 403


def test_research_thread_allows_tier1_reader():
    with patch.object(web_main.db, "board_query", return_value=([], None)):
        result = asyncio.run(web_main.board_get(_req(), thread="research", tier=Tier.T1))
    assert result.status_code == 200


def test_research_thread_allows_valid_board_key_at_tier0():
    """The Cowork path: Tier 0 (forced by the tunnel) + a valid board
    token in X-Board-Key -> 200, exactly like board writes and
    /api/v1/vault/research."""
    with patch.object(web_main.db, "board_query", return_value=([], None)), \
            patch.object(web_main.db, "board_token_valid", return_value=True):
        result = asyncio.run(web_main.board_get(
            _req(**{"X-Board-Key": "ctdc_svc_example"}), thread="research", tier=Tier.T0))
    assert result.status_code == 200


def test_research_thread_asks_db_for_read_scope():
    """board_get is a READ surface: it must request board-read (so a
    scheduled-run read token works) -- never the stricter write scope by
    accident, and never no scope at all."""
    seen = {}

    def _valid(presented, required_scope=web_main.db.BOARD_SCOPE_WRITE):
        seen["scope"] = required_scope
        return required_scope == web_main.db.BOARD_SCOPE_READ

    with patch.object(web_main.db, "board_query", return_value=([], None)), \
            patch.object(web_main.db, "board_token_valid", side_effect=_valid), \
            patch.object(web_main, "_BOARD_KEY", ""):
        result = asyncio.run(web_main.board_get(
            _req(**{"X-Board-Key": "ctdc_svc_example"}), thread="research", tier=Tier.T0))
    assert result.status_code == 200
    assert seen["scope"] == web_main.db.BOARD_SCOPE_READ


def test_require_board_key_default_is_write_scope():
    """The shared checker's default must be the STRICT scope, so board_post
    (which does not name one) can never be satisfied by a read token."""
    seen = {}

    def _valid(presented, required_scope=web_main.db.BOARD_SCOPE_WRITE):
        seen["scope"] = required_scope
        return False

    with patch.object(web_main.db, "board_token_valid", side_effect=_valid), \
            patch.object(web_main, "_BOARD_KEY", ""):
        with pytest.raises(HTTPException) as exc_info:
            web_main._require_board_key(_req(**{"X-Board-Key": "ctdc_svc_example"}))
    assert exc_info.value.status_code == 401
    assert seen["scope"] == web_main.db.BOARD_SCOPE_WRITE


def test_research_thread_rejects_bad_board_key_at_tier0():
    """A presented-but-invalid key is indistinguishable from anonymous:
    403, not 401, so the gated-thread response shape does not change."""
    with patch.object(web_main.db, "board_query", return_value=([], None)), \
            patch.object(web_main.db, "board_token_valid", return_value=False), \
            patch.object(web_main, "_BOARD_KEY", ""):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(web_main.board_get(
                _req(**{"X-Board-Key": "ctdc_svc_example"}), thread="research", tier=Tier.T0))
    assert exc_info.value.status_code == 403


def test_unknown_thread_defaults_to_gated_not_open():
    """A future thread name that isn't explicitly allowlisted must default
    to gated -- the fix is an allowlist of what's safe to leave open, not
    a denylist of what's known-sensitive today."""
    with patch.object(web_main.db, "board_query", return_value=([], None)), \
            patch.object(web_main.db, "board_token_valid", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(web_main.board_get(_req(), thread="some-new-thread", tier=Tier.T0))
    assert exc_info.value.status_code == 403
