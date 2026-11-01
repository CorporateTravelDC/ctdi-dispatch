"""A failed refresh must leave the last complete served graph usable."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def bg(monkeypatch):
    monkeypatch.setenv('NEXTCLOUD_ADMIN_USER', 'fixture')
    return importlib.import_module('second_brain.knowledge_graph.build_graph')


@pytest.fixture
def graph():
    return {'meta': {'node_count': 1, 'edge_count': 0, 'organic_links': 0,
                     'retrofit_links': 0, 'unresolved_links': 0, 'isolated_notes': 1},
            'nodes': [{'id': 'note:fixture.md', 'label': 'Fixture'}], 'edges': []}


@pytest.fixture
def out(tmp_path):
    path = tmp_path / 'published'
    path.mkdir()
    return path


def previous(out):
    out.mkdir(exist_ok=True)
    (out / 'graph.json').write_text('{"prior":true}')
    (out / 'vault-graph.html').write_text('prior HTML')


def assert_previous(out):
    assert json.loads((out / 'graph.json').read_text()) == {'prior': True}
    assert (out / 'vault-graph.html').read_text() == 'prior HTML'
    assert sorted(p.name for p in out.iterdir()) == ['graph.json', 'vault-graph.html']


def test_render_failure_keeps_both_prior_files_and_cleans_staging(bg, graph, out, monkeypatch):
    previous(out)
    def fail(*args):
        raise OSError('synthetic render failure')
    monkeypatch.setattr(bg, 'render_html', fail)
    with pytest.raises(OSError, match='synthetic render failure'):
        bg.publish_graph(graph, str(out))
    assert_previous(out)


def test_html_replace_failure_does_not_advance_metadata(bg, graph, out, monkeypatch):
    previous(out)
    replace = bg.os.replace
    def fail_html(source, destination):
        if str(destination).endswith('vault-graph.html'):
            raise OSError('synthetic publication failure')
        replace(source, destination)
    monkeypatch.setattr(bg.os, 'replace', fail_html)
    with pytest.raises(OSError, match='synthetic publication failure'):
        bg.publish_graph(graph, str(out))
    assert_previous(out)


def test_complete_publication_replaces_html_before_json_and_preserves_modes(bg, graph, out, monkeypatch):
    previous(out)
    for p in out.iterdir():
        p.chmod(0o640)
    replace = bg.os.replace
    order = []
    def record(source, destination):
        order.append(Path(destination).name)
        replace(source, destination)
    monkeypatch.setattr(bg.os, 'replace', record)
    bg.publish_graph(graph, str(out))
    assert order == ['vault-graph.html', 'graph.json']
    assert json.loads((out / 'graph.json').read_text()) == graph
    assert 'note:fixture.md' in (out / 'vault-graph.html').read_text()
    assert all(p.stat().st_mode & 0o777 == 0o640 for p in out.iterdir())


def test_json_only_does_not_touch_html(bg, graph, out):
    previous(out)
    bg.publish_graph(graph, str(out), json_only=True)
    assert json.loads((out / 'graph.json').read_text()) == graph
    assert (out / 'vault-graph.html').read_text() == 'prior HTML'


def test_empty_fetched_note_is_not_mistaken_for_a_failed_fetch(bg, monkeypatch):
    monkeypatch.setattr(bg.webdav_client, '_base_url', lambda: 'http://fixture.invalid/dav')
    class Session:
        def get(self, url, timeout):
            return SimpleNamespace(status_code=200, content=b'')
    assert bg.fetch_all(Session(), ['empty.md']) == {'empty.md': ''}


@pytest.mark.parametrize('contents', [{}, {'a.md': ''}])
def test_partial_fetch_never_replaces_prior_graph(bg, out, monkeypatch, contents):
    previous(out)
    monkeypatch.setattr(bg, '_session', lambda: object())
    monkeypatch.setattr(bg, 'enumerate_vault', lambda s: ['a.md', 'b.md'])
    monkeypatch.setattr(bg, 'fetch_all', lambda *args: contents)
    monkeypatch.setattr(bg.sys, 'argv', ['graph', '--out', str(out)])
    assert bg.main() == 1
    assert_previous(out)


def test_empty_enumeration_preserves_previous_graph(bg, out, monkeypatch):
    previous(out)
    monkeypatch.setattr(bg, '_session', lambda: object())
    monkeypatch.setattr(bg, 'enumerate_vault', lambda s: [])
    monkeypatch.setattr(bg.sys, 'argv', ['graph', '--out', str(out)])
    assert bg.main() == 1
    assert_previous(out)


def test_scheduled_default_writes_the_served_directory_only(bg, graph, out, monkeypatch):
    source = out / 'read-only-source'
    source.mkdir()
    source.chmod(0o555)
    served = out / 'served'
    monkeypatch.setattr(bg, '_LIVE_DIR', str(served))
    monkeypatch.setattr(bg, '_PKG_DIR', str(source))
    monkeypatch.setattr(bg, '_session', lambda: object())
    monkeypatch.setattr(bg, 'enumerate_vault', lambda s: ['a.md'])
    monkeypatch.setattr(bg, 'fetch_all', lambda *args: {'a.md': ''})
    def build(*args, **kwargs):
        assert kwargs == {'require_semantic': True}
        return graph
    monkeypatch.setattr(bg, 'build', build)
    monkeypatch.setattr(bg.sys, 'argv', ['graph'])
    assert bg.main() == 0
    assert json.loads((served / 'graph.json').read_text()) == graph
    assert list(source.iterdir()) == []


def test_semantic_connection_failure_is_fatal_when_required(bg, monkeypatch):
    class Unavailable:
        def __enter__(self):
            raise bg.psycopg.OperationalError('synthetic database failure')
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(bg, 'get_conn', lambda: Unavailable())
    with pytest.raises(bg.psycopg.OperationalError, match='synthetic database failure'):
        bg._load_semantic(set(), required=True)
    _, edges = bg._load_semantic(set())
    assert all(edge['type'] != 'concept_tag' for edge in edges)


def test_overlay_streams_both_index_path_forms_and_records_one_compile_revision(bg, monkeypatch):
    from second_brain.semantic import model
    concept = SimpleNamespace(id='fixture', pref_label='Fixture', facet='domain')
    monkeypatch.setattr(model, 'load', lambda: SimpleNamespace(concepts=lambda: [concept]))
    class Cursor:
        closed = False
        def execute(self, sql):
            assert 'semantic_note_concepts' in sql
        def __iter__(self):
            for path in ['corporatetraveldc/a.md', 'b.md', 'outside.md']:
                yield {'path': path, 'concept_id': 'fixture', 'facet': 'domain', 'rule': 'fixture'}
        def close(self):
            self.closed = True
    cursor = Cursor()
    class Conn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql):
            if sql.startswith('SET TRANSACTION'):
                assert 'REPEATABLE READ, READ ONLY' in sql
                return None
            return SimpleNamespace(fetchall=lambda: [
                {'key': 'compiled_at', 'value': '2026-10-30T00:00:00+00:00'},
                {'key': 'compile_seq', 'value': '7'}])
        def cursor(self, name):
            assert name == 'knowledge_graph_concepts'
            return cursor
    monkeypatch.setattr(bg, 'get_conn', Conn)
    snapshot = {}
    _, edges = bg._load_semantic({'note:a.md', 'note:b.md'}, required=True, snapshot=snapshot)
    assert {(e['source'], e['target']) for e in edges} == {
        ('note:a.md', 'concept:fixture'), ('note:b.md', 'concept:fixture')}
    assert snapshot == {'semantic_compiled_at': '2026-10-30T00:00:00+00:00', 'semantic_compile_seq': 7}
    assert cursor.closed and cursor.itersize == 1000
