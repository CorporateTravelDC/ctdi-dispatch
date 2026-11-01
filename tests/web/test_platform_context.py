"""Existing authenticated readers expose one fixed resource, never arbitrary files."""
import asyncio
import ast
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest
from common import platform_context as pc
from web.routes import agent_gateway as gateway
from fastapi import HTTPException
from starlette.responses import JSONResponse

wm = types.ModuleType('web.main')


def load_http_reader():
    # Execute the actual route body in isolation, without starting the web
    # app's background services or requiring its optional SSE dependency.
    path = Path(__file__).resolve().parents[2] / 'src/web/main.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == 'vault_research_read')
    node.decorator_list = []
    wm.Request = object
    wm.Query = lambda *a, **kw: None
    wm.JSONResponse = JSONResponse
    wm.HTTPException = HTTPException
    wm._ScrubGateBlocked = RuntimeError
    wm.db = SimpleNamespace(BOARD_SCOPE_READ='board-read')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), wm.__dict__)


load_http_reader()


@pytest.fixture
def shared(monkeypatch):
    out = {'resource': pc.RESOURCE, 'source_kind': 'shared_file',
           'path': str(pc.CONTEXT_PATH), 'sha256': 'f' * 64, 'content': '# Synthetic shared instructions\n'}
    monkeypatch.setattr(pc, 'read_context', lambda **kw: out)
    monkeypatch.setattr(wm, '_scrub_gate', lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(wm, '_board_auth', None, raising=False)
    import web
    monkeypatch.setattr(web, 'main', wm, raising=False)
    monkeypatch.setitem(sys.modules, 'web.main', wm)
    return out


@pytest.mark.parametrize('account', ['claude-fixture', 'codex-fixture', 'cloud-fixture'])
def test_connector_reads_identical_installed_revision(shared, account):
    assert gateway._call('research_read', {'path': pc.RESOURCE}, {'account': account}) == shared


def test_http_reader_authenticates_before_reading(shared, monkeypatch):
    calls = []
    async def authorized(*args, **kwargs):
        calls.append(args[1])
        return 'fixture'
    monkeypatch.setattr(wm, '_board_auth', authorized)
    out = asyncio.run(wm.vault_research_read(SimpleNamespace(), path=pc.RESOURCE))
    assert calls == ['GET /api/v1/vault/research']
    assert json.loads(out.body) == shared
    assert out.headers['cache-control'] == 'no-store'


def test_unauthorized_http_read_never_accesses_shared_file(shared, monkeypatch):
    async def denied(*args, **kwargs):
        raise wm.HTTPException(401, 'synthetic denied request')
    monkeypatch.setattr(wm, '_board_auth', denied)
    monkeypatch.setattr(pc, 'read_context', lambda **kw: pytest.fail('unauthenticated filesystem access'))
    with pytest.raises(wm.HTTPException) as exc:
        asyncio.run(wm.vault_research_read(SimpleNamespace(), path=pc.RESOURCE))
    assert exc.value.status_code == 401


def test_context_unavailable_is_503_not_stale_content(shared, monkeypatch):
    async def authorized(*args, **kwargs):
        return 'fixture'
    monkeypatch.setattr(wm, '_board_auth', authorized)
    def missing(**kw):
        raise pc.ContextUnavailable('shared context file unavailable')
    monkeypatch.setattr(pc, 'read_context', missing)
    with pytest.raises(wm.HTTPException) as exc:
        asyncio.run(wm.vault_research_read(SimpleNamespace(), path=pc.RESOURCE))
    assert exc.value.status_code == 503


def test_shared_resource_uses_existing_scrub_gate(shared, monkeypatch):
    def blocked(*a, **kw):
        raise ValueError('synthetic scrub rejection')
    monkeypatch.setattr(wm, '_scrub_gate', blocked)
    with pytest.raises(ValueError, match='synthetic scrub rejection'):
        gateway._call('research_read', {'path': pc.RESOURCE}, {'account': 'fixture'})


@pytest.mark.parametrize('path', ['platform-context/../x', '../platform-context',
                                  '/srv/ctdc-context/PLATFORM_CONTEXT.md'])
def test_only_exact_resource_name_gets_shared_file_branch(shared, monkeypatch, path):
    monkeypatch.setattr(pc, 'read_context', lambda **kw: pytest.fail('path alias reached shared file reader'))
    monkeypatch.setattr(gateway, '_research_path_ok', lambda *a: (_ for _ in ()).throw(ValueError('outside research scope')))
    with pytest.raises(ValueError, match='outside research scope'):
        gateway._call('research_read', {'path': path}, {'account': 'fixture'})
