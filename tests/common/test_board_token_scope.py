"""
Scope tests for the 2026-09-06 second board-token scope (board-read).

board_token_valid()'s own footgun guard (2026-08-07) said: adding a second
scope without making the check scope-aware in the same change is a
privilege-escalation bug. These lock in the lattice:

    write token  -> satisfies board-write AND board-read
    read token   -> satisfies board-read ONLY
    read token   -> can never be refreshed into a write token
    revoke       -> immediate, by label or hash prefix
"""
import tempfile
from pathlib import Path

import pytest

import common.db as db


@pytest.fixture
def isolated_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    orig = db._db_path
    # conn() caches one connection per thread (2026-09-04); drop it on both
    # sides of the swap or every test in the process keeps writing to the
    # FIRST temp DB opened (never production -- but not isolated either).
    db.close_thread_connection()
    db._db_path = lambda: Path(tmp.name)
    db.init_db_all()
    try:
        yield
    finally:
        db.close_thread_connection()
        db._db_path = orig
        Path(tmp.name).unlink(missing_ok=True)


def _write_token() -> str:
    return db.board_consume_nonce(db.board_mint_nonce(ttl_s=600, label="t")["nonce"])["token"]


def test_write_token_satisfies_both_scopes(isolated_db):
    tok = _write_token()
    assert db.board_token_valid(tok, db.BOARD_SCOPE_WRITE)
    assert db.board_token_valid(tok, db.BOARD_SCOPE_READ)
    assert db.board_token_valid(tok)  # default is the strict end (write)


def test_read_token_satisfies_only_read(isolated_db):
    r = db.board_mint_read_token(ttl_s=3600, label="sched")
    assert r["scope"] == db.BOARD_SCOPE_READ
    assert r["token"].startswith("brd_")
    assert db.board_token_valid(r["token"], db.BOARD_SCOPE_READ)
    assert not db.board_token_valid(r["token"], db.BOARD_SCOPE_WRITE)
    assert not db.board_token_valid(r["token"])  # default (write) must reject it


def test_unknown_scope_string_satisfies_nothing(isolated_db):
    """A row with an unrecognised scope (future typo, manual insert) grants
    nothing rather than everything."""
    r = db.board_mint_read_token(ttl_s=3600, label="odd")
    with db.conn() as c:
        c.execute("UPDATE board_tokens SET scope='board-admin' WHERE label='odd'")
    assert not db.board_token_valid(r["token"], db.BOARD_SCOPE_READ)
    assert not db.board_token_valid(r["token"], db.BOARD_SCOPE_WRITE)


def test_read_token_cannot_be_refreshed(isolated_db):
    r = db.board_mint_read_token(ttl_s=3600, label="sched")
    out = db.board_refresh_token(r["token"])
    assert out["status"] == "invalid_token"
    # and it is still a valid READ token afterwards -- refusal is not revocation
    assert db.board_token_valid(r["token"], db.BOARD_SCOPE_READ)


def test_expired_read_token_is_invalid(isolated_db):
    r = db.board_mint_read_token(ttl_s=-1, label="old")
    assert not db.board_token_valid(r["token"], db.BOARD_SCOPE_READ)


def test_revoke_by_label_and_prefix(isolated_db):
    a = db.board_mint_read_token(ttl_s=3600, label="sched")
    b = db.board_mint_read_token(ttl_s=3600, label="other")
    assert db.board_revoke_token(label="sched") == 1
    assert not db.board_token_valid(a["token"], db.BOARD_SCOPE_READ)
    assert db.board_token_valid(b["token"], db.BOARD_SCOPE_READ)
    prefix = db._board_sha(b["token"])[:12]
    assert db.board_revoke_token(hash_prefix=prefix) == 1
    assert not db.board_token_valid(b["token"], db.BOARD_SCOPE_READ)
    assert db.board_revoke_token(label="sched") == 0  # already revoked
    with pytest.raises(ValueError):
        db.board_revoke_token()


def test_list_hides_secrets(isolated_db):
    r = db.board_mint_read_token(ttl_s=3600, label="sched")
    rows = db.board_list_tokens()
    assert len(rows) == 1
    assert rows[0]["label"] == "sched" and rows[0]["scope"] == db.BOARD_SCOPE_READ
    assert r["token"] not in str(rows)
    assert len(rows[0]["hash_prefix"]) == 12
