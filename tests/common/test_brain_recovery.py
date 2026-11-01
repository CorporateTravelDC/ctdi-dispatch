"""Neither a passing drift check nor stale success can satisfy recovery."""
import copy
import pytest
from common.brain_recovery import ProofFailed, semantic_passed, graph_passed

START = '2026-10-30T01:00:00+00:00'


@pytest.fixture
def evidence():
    unit = {'Result': 'success', 'ExecMainStatus': '0', 'ActiveState': 'inactive',
            'SubState': 'dead', 'started_at': START}
    before = {'compile_seq': 4}
    semantic = {'compile_seq': 5, 'compiled_at': '2026-10-30T01:01:00+00:00',
                'ledger_ok': True, 'ledger_compile_seq': 5, 'assignments': 80}
    graph = {'generated_at': '2026-10-30T01:02:00+00:00',
             'semantic_compile_seq': 5, 'semantic_compiled_at': semantic['compiled_at'],
             'node_count': 20, 'semantic_note_links': 40}
    return before, semantic, graph, unit


def test_actual_success_requires_committed_advance_and_graph_parity(evidence):
    before, semantic, graph, unit = evidence
    semantic_passed(before, semantic, unit, START)
    graph_passed({'generated_at': '2026-10-25T00:00:00+00:00'}, graph, semantic, unit, START)


@pytest.mark.parametrize('field,value', [('Result', 'exit-code'), ('ExecMainStatus', '137'),
                                         ('started_at', '2026-10-25T00:00:00+00:00'),
                                         ('SubState', 'running')])
def test_failed_or_stale_unit_cannot_count_as_recovery(evidence, field, value):
    before, semantic, _, unit = evidence
    unit[field] = value
    with pytest.raises(ProofFailed):
        semantic_passed(before, semantic, unit, START)


@pytest.mark.parametrize('field,value', [('compile_seq', 4), ('ledger_ok', False),
                                         ('ledger_compile_seq', 4), ('assignments', 0),
                                         ('compiled_at', '2026-10-25T00:00:00+00:00')])
def test_semantic_pass_requires_real_database_evidence(evidence, field, value):
    before, semantic, _, unit = evidence
    semantic[field] = value
    with pytest.raises(ProofFailed):
        semantic_passed(before, semantic, unit, START)


@pytest.mark.parametrize('field,value', [('generated_at', '2026-10-25T00:00:00+00:00'),
                                         ('semantic_compile_seq', 4), ('semantic_note_links', 0),
                                         ('semantic_compiled_at', '2026-10-25T00:00:00+00:00'),
                                         ('node_count', 0)])
def test_fresh_looking_graph_with_stale_or_missing_links_is_rejected(evidence, field, value):
    _, semantic, graph, unit = evidence
    graph[field] = value
    with pytest.raises(ProofFailed):
        graph_passed({'generated_at': '2026-10-25T00:00:00+00:00'}, graph, semantic, unit, START)
