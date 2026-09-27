"""Engine failures must never be indistinguishable from a clean review."""
import json
from types import SimpleNamespace

import pytest

from intentumdiff import rust_core
from intentumdiff.core.index import SemanticIndex

OPERATIONS = [
    ('try_rust_build_symbol_table', 'build_symbol_table_json', ('[]',), '{}'),
    ('try_rust_build_reference_table', 'build_reference_table_json', ('[]',), '{}'),
    ('try_rust_diff_symbol_tables', 'diff_symbol_tables_json', ('{}', '{}'), '[]'),
    ('try_rust_evaluate_guardrail_rules', 'evaluate_guardrail_rules_json', ({},), '[]'),
]


@pytest.mark.parametrize('adapter,handler,args,empty', OPERATIONS)
@pytest.mark.parametrize('failure', ['load', 'missing', 'error', 'malformed', 'wrong_shape'])
def test_required_engine_operation_cannot_return_empty_success(monkeypatch, adapter, handler, args, empty, failure):
    def load():
        if failure == 'load':
            raise RuntimeError('missing cdylib')
        if failure == 'missing':
            return SimpleNamespace()
        response = {'error': '{"error":"engine rejected input"}',
                    'malformed': '{', 'wrong_shape': 'null'}[failure]
        return SimpleNamespace(**{handler: lambda *unused: response})

    monkeypatch.setattr(rust_core, '_load_backend', load)
    with pytest.raises(RuntimeError, match=handler):
        getattr(rust_core, adapter)(*args)


@pytest.mark.parametrize('adapter,handler,args,empty', OPERATIONS)
def test_successful_empty_engine_result_is_preserved(monkeypatch, adapter, handler, args, empty):
    monkeypatch.setattr(rust_core, '_load_backend', lambda: SimpleNamespace(**{handler: lambda *unused: empty}))
    result = getattr(rust_core, adapter)(*args)
    assert result == (empty if 'build_' in adapter else json.loads(empty))


def test_index_failure_does_not_publish_partial_state(monkeypatch):
    monkeypatch.setattr(rust_core, 'try_rust_build_symbol_table', lambda *args: '{}')
    def fail(*args):
        raise RuntimeError('reference extraction failed')
    monkeypatch.setattr(rust_core, 'try_rust_build_reference_table', fail)
    index = SemanticIndex()
    with pytest.raises(RuntimeError, match='reference extraction'):
        index.build()
    with pytest.raises(RuntimeError, match='Call build'):
        _ = index.symbols
    index.add_tree('retry.py', 'python', None)


def test_public_cross_file_diff_propagates_engine_failure(monkeypatch):
    from intentumdiff.analysis.cross_file import detect_cross_file_changes
    old, new = SemanticIndex().build(), SemanticIndex().build()
    monkeypatch.setattr(rust_core, '_load_backend', lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match='diff_symbol_tables_json'):
        detect_cross_file_changes(old, new)


def test_guardrail_application_cannot_pass_on_engine_failure(tmp_path, monkeypatch):
    from intentumdiff.analysis.guardrails import apply_guardrails_to_diff
    from intentumdiff.core.models import DiffConfig, SemanticDiff, SemanticNode, NodePosition
    policy = tmp_path / 'policy.yaml'
    policy.write_text('guardrails:\n  protected:\n    - language: json\n      path: secret\n', encoding='utf-8')
    tree = SemanticNode(id='root', node_type='document', label='',
                        position=NodePosition(start_line=0, start_col=0, end_line=0, end_col=2),
                        structural_hash='x')
    diff = SemanticDiff(old_filename='data.json', new_filename='data.json', language='json', changes=[])
    monkeypatch.setattr(rust_core, '_load_backend', lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match='evaluate_guardrail_rules_json'):
        apply_guardrails_to_diff(diff, old_tree=tree, new_tree=tree,
                                old_source='{}', new_source='{}',
                                config=DiffConfig(guardrails_enabled=True, guardrail_policy_path=policy))


def test_commit_index_does_not_omit_engine_failure(monkeypatch):
    from intentumdiff.core.commit_differ import CommitDiffer
    differ = CommitDiffer()
    def fail(*args, **kwargs):
        raise RuntimeError('engine parse failed')
    monkeypatch.setattr(differ._differ, 'parse', fail)
    with pytest.raises(RuntimeError, match='engine parse failed'):
        differ._build_index([('changed.py', 'python', 'x = 2')])


def test_empty_sql_index_side_uses_canonical_tree(monkeypatch):
    from intentumdiff.core.commit_differ import CommitDiffer
    from intentumdiff._differ_presentation import _empty_semantic_tree

    differ = CommitDiffer()
    def reject_empty(*args, **kwargs):
        raise AssertionError("absent source must not reach SQL parser")
    monkeypatch.setattr(differ._differ, "parse", reject_empty)
    tree = differ._parse_to_tree("new.sql", "sql", "")
    assert tree == _empty_semantic_tree("sql")
