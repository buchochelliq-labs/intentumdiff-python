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
    original_call = rust_core._c_abi_call
    def fail_application(operation, *args):
        if operation == 'apply_guardrail_policy':
            raise RuntimeError('apply_guardrail_policy engine failed')
        return original_call(operation, *args)
    monkeypatch.setattr(rust_core, '_c_abi_call', fail_application)
    with pytest.raises(RuntimeError, match='apply_guardrail_policy'):
        apply_guardrails_to_diff(diff, old_tree=tree, new_tree=tree,
                                old_source='{}', new_source='{}',
                                config=DiffConfig(guardrails_enabled=True, guardrail_policy_path=policy))


def _file_result(path, language, old, new):
    from intentumdiff.core.commit_differ import FileDiffResult
    from intentumdiff.core.models import SemanticDiff

    return FileDiffResult(
        file_diff=SemanticDiff(old_filename=path, new_filename=path, language=language),
        old_path=path, new_path=path, old_content=old, new_content=new,
    )


def test_commit_index_does_not_omit_engine_failure(monkeypatch):
    # core #298: the parse failure is transported to core, which reports it; it is never
    # silently dropped and never turns the index into a clean "complete" answer.
    from intentumdiff.core.commit_differ import CommitDiffer
    differ = CommitDiffer()
    def fail(*args, **kwargs):
        raise RuntimeError('engine parse failed')
    monkeypatch.setattr(differ._differ, 'parse', fail)
    result = differ.finalize_commit_diff(
        [_file_result('changed.py', 'python', 'x = 1', 'x = 2')], [],
        old_ref='HEAD~1', new_ref='HEAD',
    )
    assert result.cross_file_index_status == 'partial'
    assert result.cross_file_index_complete is False
    assert [(e.path, e.side) for e in result.index_errors] == [('changed.py', 'old')]
    assert 'engine parse failed' in result.index_errors[0].reason


def test_empty_sql_index_side_is_sent_as_absent(monkeypatch):
    from intentumdiff.core.commit_differ import CommitDiffer

    differ = CommitDiffer()
    def reject_empty(*args, **kwargs):
        raise AssertionError("absent source must not reach SQL parser")
    monkeypatch.setattr(differ._differ, "parse", reject_empty)
    entry = differ._index_input(_file_result("new.sql", "sql", "", ""))
    assert entry["old_tree"] is None and entry["new_tree"] is None
    assert entry["old_error"] is None and entry["new_error"] is None
