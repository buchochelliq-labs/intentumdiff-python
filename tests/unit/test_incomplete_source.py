"""Incomplete syntax must not erase changes to whitespace or literal values."""
import pytest
from intentumdiff._differ_presentation import _token_fallback_diff

@pytest.mark.parametrize('old,new', [
    ('def broken(:\n x = "hello  world"\n', 'def broken(:\n x = "hello world"\n'),
    ('def broken(:\n    x = 1\n', 'def broken(:\n  x = 1\n'),
    ('é\r\n"unfinished  ', 'é\r\n"unfinished '),
    ('', 'def broken('), ('def broken(', ''),
])
def test_fallback_preserves_source_changes(old, new):
    diff = _token_fallback_diff(old, new, 'a.py', 'a.py', 'python')
    assert len(diff.changes) == 1
    assert diff.is_fallback and not diff.is_style_only
    assert diff.metadata['engine_owner'] == 'rust'
    r = diff.metadata['source_ranges']
    a, b = old.encode(), new.encode()
    assert a[:r['old_start_byte']] + b[r['new_start_byte']:r['new_end_byte']] + a[r['old_end_byte']:] == b
    assert diff.change_groups[0].kind.value == 'MEANINGFUL_CHANGE'
    assert diff.changes[0].confidence < 1

@pytest.mark.parametrize('diagnostics', [False, True], ids=['native', 'routed'])
def test_live_edit_sequence_recovers_semantic_analysis(diagnostics, monkeypatch):
    from contextlib import closing
    from intentumdiff import DiffConfig, SemanticDiffer
    monkeypatch.setenv('INTENTUMDIFF_ENFORCE_RUST_ONLY_ENGINE', '1')
    valid = 'def value():\n    return 1\n'
    with closing(SemanticDiffer(DiffConfig(diagnostics=diagnostics))) as differ:
        for current in ['def value(', 'def value(:\n    return "hello  world"\n', 'def value(:\n    return "hello world"\n']:
            diff = differ.diff_strings(valid, current, 'edit.py')
            assert diff.is_fallback and diff.has_semantic_changes
            assert not diff.is_style_only
            assert diff.metadata['engine_owner'] == 'rust'
            assert all(c.refactoring_kind is None for c in diff.changes)
        restored = differ.diff_strings(valid, valid.replace('1', '2'), 'edit.py')
        assert restored.has_semantic_changes and not restored.is_fallback


def test_unchanged_incomplete_source_has_no_changes():
    diff = _token_fallback_diff('def broken(', 'def broken(', 'a.py', 'a.py', 'python')
    assert diff.is_fallback and not diff.has_semantic_changes
    assert not diff.changes and not diff.change_groups

@pytest.mark.parametrize('diagnostics', [False, True])
def test_unchanged_invalid_source_is_not_style_only(diagnostics):
    from contextlib import closing
    from intentumdiff import DiffConfig, SemanticDiffer
    with closing(SemanticDiffer(DiffConfig(diagnostics=diagnostics))) as differ:
        diff = differ.diff_strings('def broken(', 'def broken(', 'a.py')
    assert not diff.has_semantic_changes
    assert diff.is_fallback and not diff.is_style_only

@pytest.mark.parametrize('filename,old,new', [
    ('a.py', 'def broken(:\n x="hello  world"\n', 'def broken(:\n x="hello world"\n'),
    ('a.js', 'function f( { return "hello  world";', 'function f( { return "hello world";'),
])
def test_native_live_protocol_preserves_fallback(filename, old, new, tmp_path):
    import json
    from pathlib import Path
    import intentumdiff
    from intentumdiff.rust_core import _load_backend
    wasm = str(Path(intentumdiff.__file__).parent / 'wasm')
    result = json.loads(_load_backend().live_diff_contents_json(
        str(tmp_path), filename, old, new, '{}', wasm))
    diff = result['diff']
    assert diff['is_fallback'] and not diff['is_style_only']
    assert diff['has_semantic_changes'] and len(diff['changes']) == 1
    assert diff['metadata']['semantic_contract'] == 'rust_source_fallback_v1'
    assert diff['change_groups'][0]['metadata']['semantic_equivalence'] == 'unknown'
