"""Check actual native Rust/public Python output against shared source expectations.

Run after building core's migration_probe example. Evidence output is optional.
Only route-specific metadata, tree IDs/hashes and audit-only NOISE_SUPPRESSED
counts are excluded: those describe execution, not source changes.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from contextlib import closing
from intentumdiff import SemanticDiffer, review_text


def projection(diff):
    def node(value):
        if value is None:
            return None
        return {k: value[k] for k in ('node_type', 'label', 'position')}
    return {
        'changes': [{'change_type': c['change_type'], 'old_node': node(c.get('old_node')),
                     'new_node': node(c.get('new_node')), 'refactoring_kind': c.get('refactoring_kind')}
                    for c in diff['changes']],
        'groups': [{'kind': g['kind'], 'raw_change_indices': g.get('raw_change_indices', [])}
                   for g in diff['change_groups'] if g['kind'] != 'NOISE_SUPPRESSED'],
        'has_semantic_changes': diff['has_semantic_changes'],
        'is_style_only': diff['is_style_only'], 'is_fallback': diff['is_fallback'],
    }


def check_expectations(case, diff):
    changes = diff['changes']
    labels = [c[side]['label'] for c in changes for side in ('old_node', 'new_node') if c.get(side)]
    for label in case.get('required_labels', []):
        assert label in labels, (case['id'], label, diff)
    if 'exact_changes' in case:
        assert len(changes) == case['exact_changes'], (case['id'], diff)
    if 'end_columns' in case:
        assert [changes[0][side]['position']['end_col'] for side in ('old_node', 'new_node')] == case['end_columns'], (case['id'], diff)
    if 'moves' in case:
        assert sum(c['change_type'] == 'MOVE' for c in changes) == case['moves'], (case['id'], diff)
    for group in diff['change_groups']:
        assert group['kind'] not in case.get('forbidden_group_kinds', []), (case['id'], diff)
    if case.get('forbid_ignored_style'):
        assert not diff['metadata'].get('ignored_style_changes'), (case['id'], diff)
    if 'expected' in case:
        expected = case['expected']
        assert len(changes) == len(expected['changes']), diff
        for actual, wanted in zip(changes, expected['changes']):
            assert actual['change_type'] == wanted['change_type'], diff
            for side in ('old', 'new'):
                node = actual[side + '_node']
                assert node['label'] == wanted[side + '_label'], diff
                assert [node['position']['start_line'], node['position']['start_col']] == wanted[side + '_start'], diff
        assert not any(c['change_type'] in expected['forbidden_change_types'] for c in changes), diff
    assert diff['has_semantic_changes'] and not diff['is_style_only'], (case['id'], diff)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--core-dir', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--wasm-dir', type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    core, native, wasm = args.core_dir.resolve(), args.native.resolve(), args.wasm_dir.resolve()
    cases = json.loads((core / 'tests/fixtures/migration/markdown.json').read_text(encoding='utf-8'))
    cases = [dict(c, filename='doc.md', mode='text') for c in cases]
    schema = json.loads((core / 'tests/corpus/schema_custom_identity.json').read_text(encoding='utf-8'))
    cases.append(dict(schema, id='schema-custom-identity', mode='sources'))
    records = []
    original = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        schemas = root / '.intentumdiff/schemas'
        schemas.mkdir(parents=True)
        (schemas / 'acme.json').write_text(json.dumps(schema['descriptor']), encoding='utf-8')
        os.chdir(root)
        try:
            with closing(SemanticDiffer()) as differ:
                for case in cases:
                    request = dict(case, repo=str(root), wasm=str(wasm))
                    result = subprocess.run([str(native)], input=json.dumps(request), text=True, encoding='utf-8', capture_output=True, check=True)
                    rust = json.loads(result.stdout)
                    python = (review_text(case['old'], case['new'], filename=case['filename']) if case['mode'] == 'text' else differ.diff_strings(case['old'], case['new'], case['filename'])).model_dump(mode='json')
                    check_expectations(case, rust)
                    check_expectations(case, python)
                    assert projection(rust) == projection(python), (case['id'], projection(rust), projection(python))
                    records.append({'id': case['id'], 'old': case['old'], 'new': case['new'], 'rust': rust, 'python': python})
        finally:
            os.chdir(original)
    from intentumdiff.rust_core import _c_abi_call
    for case in json.loads((core / 'tests/fixtures/migration/utilities.json').read_text(encoding='utf-8')):
        rust = json.loads(subprocess.run([str(native)], input=json.dumps(case), text=True, encoding='utf-8', capture_output=True, check=True).stdout)
        if case['handler'] == 'infer_file_lifecycle':
            python = _c_abi_call(case['handler'], *case['args'])
        else:
            profile = _c_abi_call('schema_profiles', json.dumps({'operation': 'validate', 'document': {
                'language_id': 'test', 'match': {'filename_patterns': [case['pattern']]}, 'identity_fields': ['key']},
                'path': 'profile.json', 'raw_text': ''}))['profile']
            python = _c_abi_call('schema_profiles', json.dumps({'operation': 'match', 'profiles': [profile],
                'filename': case['filename'], 'content': '{}'})) is not None
        assert rust == python == case['expected'], (case, rust, python)
        records.append(dict(case, rust=rust, python=python))
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(records, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{len(records)} source-judged native Rust/Python cases passed')


if __name__ == '__main__':
    main()
