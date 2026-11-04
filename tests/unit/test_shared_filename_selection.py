"""Rust action traces: source expectations, ABI and native parity."""
import json
import os
from pathlib import Path
import subprocess
import pytest
from intentumdiff.rust_core import _required_engine_json

CASES = json.loads((Path(__file__).parents[1] / 'fixtures/filename_selection.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_source_expected_selection_trace(case):
    payload = json.dumps({'request':case['request'], 'events':case['events']})
    if 'error' in case:
        with pytest.raises(RuntimeError, match=case['error']):
            _required_engine_json('filename_selection_next', payload, result_type=dict)
    else:
        assert _required_engine_json('filename_selection_next', payload, result_type=dict) == case['expected']
    if probe := os.environ.get('INTENTUMDIFF_NATIVE_PROBE'):
        native = json.loads(subprocess.check_output([probe], input=json.dumps({'handler':'filename_selection','request':case['request'],'events':case['events']}), text=True, encoding='utf-8'))
        if 'error' in case:
            assert case['error'] in native['error']
        else:
            assert native['result'] == case['expected']
