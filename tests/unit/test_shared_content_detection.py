"""Source-expected content policy shared by Rust and the Python ABI."""
import json
import os
from pathlib import Path
import subprocess

import pytest
from intentumdiff.rust_core import _required_engine_json

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/content_detection.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_content_policy_corpus(case):
    payload = json.dumps({'request':case['request'], 'observations':case['observations']})
    if 'error' in case:
        with pytest.raises(RuntimeError, match=case['error']):
            _required_engine_json('content_detection_finish', payload, result_type=dict)
    else:
        actual = _required_engine_json('content_detection_finish', payload, result_type=dict)
        assert actual == case['expected']
        # The public API receives exactly the same descriptors and probe claims.
        from types import SimpleNamespace
        from unittest.mock import Mock
        from intentumdiff import SemanticDiffer
        from intentumdiff.core.models import DiffConfig
        from intentumdiff.plugins.registry import PluginRegistry
        from intentumdiff.plugins.exceptions import PluginNotFoundError
        request = case['request']
        claims = {item['index']: item['language'] for item in case['observations']}
        registry = PluginRegistry(DiffConfig(allowed_plugins=request.get('allowed_plugins')))
        registry._parsers = [SimpleNamespace(plugin_id=entry['plugin_id'], grammar_id=entry['grammar_id'],
            language_ids=entry['languages'], priority=entry['priority'],
            detect_language=Mock(return_value=claims.get(index))) for index, entry in enumerate(request['entries'])]
        differ = SemanticDiffer.__new__(SemanticDiffer)
        differ._registry = registry
        options = {key: request.get(key) for key in ('candidates','plugin_id','preferred_plugins')}
        if case['expected']['not_found'] is not None:
            with pytest.raises(PluginNotFoundError):
                differ.detect_all(request['content'], **options)
        else:
            assert [result.model_dump() for result in differ.detect_all(request['content'], **options)] == case['expected']['results']
    probe = os.environ.get('INTENTUMDIFF_NATIVE_PROBE')
    if probe:
        native = json.loads(subprocess.check_output([probe], input=json.dumps({'handler':'content_detection', 'request':case['request'], 'observations':case['observations']}), text=True, encoding='utf-8'))
        if 'error' in case:
            assert case['error'] in native['error']
        else:
            assert native['result'] == case['expected']


def test_actual_python_diff_remains_source_correct():
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import SemanticDiff
    root = Path(__file__).parents[2]
    case = json.loads((root / 'tests/fixtures/content_detection_source.json').read_text(encoding='utf-8'))
    actual = SemanticDiffer().diff_strings(case['old'], case['new'], case['filename']).model_dump(mode='json')
    assert actual['language'] == 'python'
    assert actual['has_semantic_changes'] and not actual['is_style_only']
    assert len(actual['changes']) == 1
    change = actual['changes'][0]
    assert change['change_type'] == 'MODIFICATION'
    assert change['old_node']['label'] == case['old_label']
    assert change['new_node']['label'] == case['new_label']
    assert change['new_node']['node_type'] == 'integer'
    assert any(group['kind'] == 'MEANINGFUL_CHANGE' and group['raw_change_indices'] == [0] for group in actual['change_groups'])
    if probe := os.environ.get('INTENTUMDIFF_NATIVE_PROBE'):
        native = json.loads(subprocess.check_output([probe], input=json.dumps({**case,'repo':str(root),'wasm':str(root/'src/intentumdiff/wasm')}), text=True, encoding='utf-8'))
        native = SemanticDiff.model_validate(native).model_dump(mode='json')
        for field in ('language','changes','change_groups','has_semantic_changes','is_style_only'):
            assert native[field] == actual[field], field
