"""Source-derived regressions for the shared public engine contract."""
import pytest
from intentumdiff.rust_core import _c_abi_call, _load_backend


def test_c_abi_library_is_never_imported_as_pyo3(monkeypatch):
    monkeypatch.delenv('INTENTUMDIFF_RUST_CORE_CTYPES', raising=False)
    assert type(_load_backend()).__name__ == '_CtypesBackend'


@pytest.mark.parametrize('old,new', [('', 'x'), ('x', '')])
def test_explicit_modified_empty_file_stays_modified(old, new):
    assert _c_abi_call('infer_file_lifecycle', old, new, 'modified') == 'modified'


def test_markdown_move_is_not_also_ignored_trivia():
    diff = _c_abi_call('review_text', '# A\none\n# B\ntwo\n', '# B\ntwo\n# A\none\n', 'a.md', 'a.md')
    assert len(diff['changes']) == 1
    assert diff['changes'][0]['change_type'] == 'MOVE'
    assert not any(g['kind'] == 'IGNORED_STYLE' for g in diff['change_groups'])
    assert not diff['metadata'].get('ignored_style_changes')


def test_native_compile_context_matches_python_host(tmp_path):
    import json
    from pathlib import Path
    from intentumdiff.analysis.compile_commands import compile_commands_metadata
    (tmp_path / 'compile_commands.json').write_text(json.dumps([
        {'directory': str(tmp_path), 'file': 'sample.cpp', 'arguments': ['c++', '-DDEBUG', '-Iinclude']}
    ]))
    wasm = Path(__file__).resolve().parents[2] / 'src/intentumdiff/wasm'
    result = _c_abi_call('live_diff_contents', str(tmp_path), 'sample.cpp',
                         'int value = 1;\n', 'int value = 2;\n', '{}', str(wasm))
    expected = compile_commands_metadata(filename='sample.cpp', language='cpp', cwd=tmp_path)
    assert result['diff']['metadata']['compile_commands'] == expected


def test_public_text_review_returns_python_dto():
    from intentumdiff import review_text
    from intentumdiff.core.models import SemanticDiff
    diff = review_text('# Old\nsame\n', '# New\nsame\n', filename='doc.md')
    assert isinstance(diff, SemanticDiff)
    assert len(diff.changes) == 1
    assert diff.changes[0].old_node.label == '# Old'
    assert diff.changes[0].new_node.label == '# New'


@pytest.mark.parametrize('old,new', [('été\n', 'hiver\n'), ('# Old\nété\n', '# New\nété\n')])
def test_public_text_positions_use_utf8_bytes(old, new):
    from intentumdiff import review_text
    diff = review_text(old, new, filename='doc.md')
    assert diff.changes[0].old_node.position.end_col == 5
    assert diff.changes[0].new_node.position.end_col == 5


@pytest.mark.parametrize('pattern,filename', [('[a&&b].json', 'a.json'), ('[a~~b].json', '~.json'), ('[a--c].json', 'c.json')])
def test_schema_globs_keep_literal_set_characters(pattern, filename):
    profiles = _c_abi_call('schema_profiles', __import__('json').dumps({
        'operation': 'validate', 'document': {'language_id': 'custom', 'match': {'filename_patterns': [pattern]}, 'identity_fields': ['key']},
        'path': 'schema.json', 'raw_text': '',
    }))
    index = _c_abi_call('schema_profiles', __import__('json').dumps({
        'operation': 'match', 'profiles': [profiles['profile']], 'filename': filename, 'content': '{}',
    }))
    assert index == 0
