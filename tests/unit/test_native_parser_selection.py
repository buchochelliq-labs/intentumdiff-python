"""Real component selection and source-grounded native/public-Python comparisons."""
import json
import os
from pathlib import Path
import subprocess

import pytest

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/native_parser_selection.json').read_text(encoding='utf-8'))


def native(case, config=None, wasm_dir=None):
    probe = os.environ.get('INTENTUMDIFF_NATIVE_PROBE')
    if not probe:
        pytest.skip('native migration probe is required by the cross-language CI gate')
    root = Path(__file__).parents[2]
    return json.loads(subprocess.check_output([probe], input=json.dumps({
        **case, 'handler':'native_selection_diff', 'repo':str(root),
        'wasm':str(wasm_dir or root/'src/intentumdiff/wasm'), 'config':config or {},
    }), text=True, encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_actual_selection_and_produced_diff(case):
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    result = native(case)
    assert 'fallback' not in result and 'error' not in result, result
    actual = SemanticDiff.model_validate(result['diff']).model_dump(mode='json')
    python = SemanticDiffer().diff_strings(case['old'], case['new'], case['filename']).model_dump(mode='json')
    assert actual['language'] == 'python'
    assert actual['has_semantic_changes'] and not actual['is_style_only']
    if case['old'] and case['new']:
        assert len(actual['changes']) == 1
        change = actual['changes'][0]
        assert change['change_type'] == 'MODIFICATION'
        assert change['old_node']['label'] == '1'
        assert change['new_node']['label'] == '2'
        assert change['new_node']['node_type'] == 'integer'
        assert len(actual['change_groups']) == 1, 'one literal edit must not duplicate enclosing groups'
    else:
        assert actual['changes']
        assert all(change['change_type'] == ('DELETION' if case['old'] else 'ADDITION') for change in actual['changes'])
    for field in ('language','changes','change_groups','has_semantic_changes','is_style_only'):
        assert actual[field] == python[field], field


def test_component_fuel_failure_is_terminal_in_both_apis():
    from intentumdiff.core.models import DiffConfig
    from intentumdiff.plugins.registry import PluginRegistry
    from intentumdiff.plugins.exceptions import PluginFuelExhausted
    case = CASES[0]
    result = native(case, {'plugin_fuel':1})
    assert 'fallback' not in result and 'diff' not in result
    assert 'fuel' in result['error']
    with pytest.raises(PluginFuelExhausted):
        PluginRegistry(DiffConfig(plugin_fuel=1)).detect_parser(case['filename'],case['new'])


def test_real_probe_rejects_misleading_manifest_and_skips_missing_component(tmp_path):
    import shutil
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    root = Path(__file__).parents[2]
    case = json.loads((root/'tests/fixtures/parser_special_filename.json').read_text(encoding='utf-8'))
    for filename in ('python_parser.wasm','dockerfile_parser.wasm'):
        shutil.copyfile(root/'src/intentumdiff/wasm'/filename, tmp_path/filename)
    # The Python component is shortlisted first but must decline Dockerfile;
    # another discovery hint points to a missing component. Dockerfile is found
    # through the remaining-specific phase using actual guest metadata/probing.
    (tmp_path/'parser_manifest.json').write_text(json.dumps({
        'parsers': {
            'python':{'plugin_id':'python','wasm':'python_parser.wasm','extensions':['Dockerfile']},
            'missing':{'plugin_id':'missing','wasm':'missing.wasm','extensions':['Dockerfile']},
            'dockerfile':{'plugin_id':'dockerfile','wasm':'dockerfile_parser.wasm','extensions':[]},
        }, 'extension_index':{},
    }), encoding='utf-8')
    result = native(case, wasm_dir=tmp_path)
    assert 'diff' in result, result
    actual = SemanticDiff.model_validate(result['diff']).model_dump(mode='json')
    python = SemanticDiffer().diff_strings(case['old'], case['new'], case['filename']).model_dump(mode='json')
    assert actual['language'] == 'dockerfile'
    assert len(actual['changes']) == 1
    change = actual['changes'][0]
    assert change['change_type'] == 'MODIFICATION'
    assert change['old_node']['label'] == case['old_label']
    assert change['new_node']['label'] == case['new_label']
    assert actual['has_semantic_changes'] and not actual['is_style_only']
    for field in ('language','changes','change_groups','has_semantic_changes','is_style_only'):
        assert actual[field] == python[field], field


GROUP_CASES = json.loads((Path(__file__).parents[1] / 'fixtures/meaningful_group_ownership.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', GROUP_CASES, ids=lambda case: case['name'])
def test_custom_and_certified_routes_own_meaningful_evidence_once(case, tmp_path):
    import shutil
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    root = Path(__file__).parents[2]
    shutil.copyfile(root/'src/intentumdiff/wasm/python_parser.wasm', tmp_path/'python.wasm')
    (tmp_path/'parser_manifest.json').write_text(json.dumps({
        'parsers':{'python':{'plugin_id':'custom-python','wasm':'python.wasm','extensions':['.py']}},
        'extension_index':{'.py':'python'},
    }), encoding='utf-8')
    result = native({**case,'filename':'example.py'}, wasm_dir=tmp_path)
    assert 'diff' in result, result
    actual = SemanticDiff.model_validate(result['diff']).model_dump(mode='json')
    python = SemanticDiffer().diff_strings(case['old'],case['new'],'example.py').model_dump(mode='json')
    for diff in (actual, python):
        groups = [g for g in diff['change_groups'] if g['kind'] == 'MEANINGFUL_CHANGE']
        assert len(groups) == case['groups'], groups
        if case['groups'] == 1:
            assert 'answer' in groups[0]['old_labels']
            assert 'answer' in groups[0]['new_labels']
        if 'numeric spelling' in case['name']:
            assert diff['is_style_only'] and not diff['has_semantic_changes']
        if case['name'].startswith('equivalent literal and'):
            assert len(diff['changes']) == 1
            assert diff['changes'][0]['old_node']['label'] == '2'
            assert diff['changes'][0]['new_node']['label'] == '3'
            for side in ('old', 'new'):
                for scope in diff['metadata'].get('scope_trails', {}).get(side, []):
                    assert scope['change_index'] == 0
                    assert scope['node_id'] == diff['changes'][0][f'{side}_node']['id']
        evidence = [i for g in groups for i in g['raw_change_indices']]
        assert sorted(evidence) == list(range(len(diff['changes'])))
        # Entity context must not masquerade as additional changed-node evidence.
        for group in groups:
            for side in ('old','new'):
                changed_ids = {diff['changes'][i][f'{side}_node']['id'] for i in group['raw_change_indices']
                               if diff['changes'][i][f'{side}_node'] is not None}
                assert set(group[f'{side}_node_ids']) <= changed_ids
    for field in ('language','changes','change_groups','has_semantic_changes','is_style_only'):
        assert actual[field] == python[field], field


@pytest.mark.parametrize('reverse', [False, True], ids=['insert', 'delete'])
def test_statement_insertion_or_deletion_is_not_reorder(reverse):
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    case = json.loads((Path(__file__).parents[1] / 'fixtures/reorder_insertion.json').read_text(encoding='utf-8'))[0]
    if reverse:
        case['old'], case['new'] = case['new'], case['old']
    result = native(case)
    assert 'diff' in result, result
    actual = SemanticDiff.model_validate(result['diff']).model_dump(mode='json')
    python = SemanticDiffer().diff_strings(case['old'], case['new'], case['filename']).model_dump(mode='json')
    for diff in (actual, python):
        assert sorted(c['change_type'] for c in diff['changes']) == sorted([
            'DELETION' if reverse else 'ADDITION', 'MODIFICATION'])
        assert len(diff['change_groups']) == 1
        assert 'GREET' in diff['change_groups'][0]['old_labels']
        assert sorted(diff['change_groups'][0]['raw_change_indices']) == [0, 1]
    for field in ('language', 'changes', 'change_groups', 'has_semantic_changes', 'is_style_only'):
        assert actual[field] == python[field], field


@pytest.mark.parametrize('case', json.loads((Path(__file__).parents[1] / 'fixtures/call_layout_evidence.json').read_text(encoding='utf-8')), ids=lambda c: c['name'])
def test_call_layout_evidence_is_source_backed(case):
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    result = native({**case, 'filename': 'example.py'})
    assert 'diff' in result, result
    actual = SemanticDiff.model_validate(result['diff']).model_dump(mode='json')
    python = SemanticDiffer().diff_strings(case['old'], case['new'], 'example.py').model_dump(mode='json')
    for diff in (actual, python):
        groups = [g for g in diff['change_groups'] if g['rule_id'] == 'python.formatting.call_wrapping_equivalence']
        assert bool(groups) == case['style']
        for group in groups:
            assert group['metadata']['evidence'] == 'matched_call_source'
            assert 'send' in group['old_labels']
            assert 'foo' not in group['old_labels'] and 'use' not in group['old_labels']
        assert any(c['change_type'] == 'DELETION' for c in diff['changes'])
        assert any(c['change_type'] == 'MODIFICATION' for c in diff['changes'])
    for field in ('changes', 'change_groups', 'has_semantic_changes', 'is_style_only'):
        assert actual[field] == python[field], field


@pytest.mark.parametrize('case', json.loads((Path(__file__).parents[1] / 'fixtures/call_layout_evidence.json').read_text(encoding='utf-8')), ids=lambda c: c['name'])
def test_deleted_call_arguments_do_not_move_into_surviving_call(case):
    from intentumdiff import SemanticDiffer
    result = native({**case, 'filename': 'example.py'})
    assert 'diff' in result, result
    python = SemanticDiffer().diff_strings(case['old'], case['new'], 'example.py').model_dump(mode='json')
    for diff in (result['diff'], python):
        assert not any(c['change_type'] == 'MOVE' for c in diff['changes'])


@pytest.mark.parametrize('case', json.loads((Path(__file__).parents[1] / 'fixtures/argument_owner_matching.json').read_text(encoding='utf-8')), ids=lambda c: c['name'])
def test_argument_lists_follow_their_owning_calls(case):
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    rust = SemanticDiff.model_validate(native({**case, 'filename': 'example.py'})['diff']).model_dump(mode='json')
    python = SemanticDiffer().diff_strings(case['old'], case['new'], 'example.py').model_dump(mode='json')
    for diff in (rust, python):
        actual = [[c['change_type'], (c.get('old_node') or {}).get('node_type'), (c.get('old_node') or {}).get('label'), (c.get('new_node') or {}).get('node_type'), (c.get('new_node') or {}).get('label')] for c in diff['changes']]
        assert sorted(actual, key=str) == sorted(case['expected'], key=str)
    for field in ('changes', 'change_groups', 'has_semantic_changes', 'is_style_only'):
        assert rust[field] == python[field], field


@pytest.mark.parametrize('case', json.loads((Path(__file__).parents[1] / 'fixtures/elixir_sibling_swap.json').read_text(encoding='utf-8')), ids=lambda c: c['name'])
def test_elixir_sibling_swap_preserves_matched_definition_contents(case):
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    rust = SemanticDiff.model_validate(native(case)['diff']).model_dump(mode='json')
    python = SemanticDiffer().diff_strings(case['old'], case['new'], case['filename']).model_dump(mode='json')
    for diff in (rust, python):
        actual = [[c['change_type'], (c.get('old_node') or {}).get('node_type'), (c.get('old_node') or {}).get('label'), (c.get('new_node') or {}).get('node_type'), (c.get('new_node') or {}).get('label')] for c in diff['changes']]
        assert actual == case['expected']
    for field in ('changes', 'change_groups', 'has_semantic_changes', 'is_style_only'):
        assert rust[field] == python[field], field
