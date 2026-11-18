"""Offline acceptance-tool checks: no semantic engine replacement."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/check_external_binding.py'


def tool():
    spec = importlib.util.spec_from_file_location('external_binding', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review():
    return dict(language='python', changes=[dict(change_type='MODIFICATION',
        old_node={'label': '1'}, new_node={'label': '2'})],
        change_groups=[], has_semantic_changes=True, is_style_only=False, is_fallback=False)


def test_independent_expectations_require_labels_on_same_change():
    module = tool()
    expected = dict(has_semantic_changes=True, is_style_only=False,
        required_changes=[dict(change_type='MODIFICATION', old_label='1', new_label='2')],
        forbidden_change_types=['DELETION'])
    assert module.expectation_errors(expected, review()) == []
    wrong = review()
    wrong['changes'][0]['new_node']['label'] = '3'
    wrong['changes'].append(dict(change_type='MODIFICATION', old_node={'label': '0'}, new_node={'label': '2'}))
    assert module.expectation_errors(expected, wrong)


def test_missing_boolean_is_not_false_default():
    module = tool()
    output = review()
    del output['is_style_only']
    with pytest.raises((ValueError, KeyError)):
        module.expectation_errors(dict(has_semantic_changes=True, is_style_only=False,
            required_changes=[], forbidden_change_types=[]), output)


def test_rejects_path_traversal_and_tampered_bytes(tmp_path):
    module = tool()
    (tmp_path / 'source').write_bytes(b'hello')
    with pytest.raises(ValueError):
        module.checked_bytes(tmp_path, '../source', '0' * 64)
    with pytest.raises(ValueError):
        module.checked_bytes(tmp_path, 'source', '0' * 64)


def test_checkout_import_and_checkout_cwd_are_rejected(tmp_path):
    module = tool()
    checkout = SCRIPT.parents[1]
    with pytest.raises(ValueError):
        module.verify_installed(checkout / 'src/intentumdiff/__init__.py', tmp_path)
    with pytest.raises(ValueError):
        module.verify_installed(tmp_path / 'site-packages/intentumdiff/__init__.py', checkout)


def test_missing_driver_inputs_fail_before_engine_import(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), '--core-report', str(tmp_path / 'missing'),
        '--fixtures', str(tmp_path), '--parsers', str(tmp_path), '--report', str(tmp_path / 'result.json')],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 2
    assert 'infrastructure' in result.stderr.lower()
    assert not (tmp_path / 'result.json').exists()


def inputs(tmp_path):
    import hashlib
    import json
    fixture = tmp_path / 'fixtures'
    parsers = tmp_path / 'parsers'
    fixture.mkdir()
    parsers.mkdir()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    (fixture / 'old').write_bytes(b'1')
    (fixture / 'new').write_bytes(b'2')
    (parsers / 'python_parser.wasm').write_bytes(b'component')
    case = dict(id='repair', expected={}, required=False, old_path='old', new_path='new', old_sha256=sha(b'1'), new_sha256=sha(b'2'))
    manifest = dict(schema_version=1, cases=[case])
    languages = [dict(language_id=f'language-{i}') for i in range(74)]
    ledger = dict(schema_version=1, languages=languages)
    staged = dict(schema_version=1, components=[dict(component='python_parser.wasm', sha256=sha(b'component'))])
    encode = lambda value: json.dumps(value).encode()
    (fixture / 'manifest.json').write_bytes(encode(manifest))
    (fixture / 'ledger.json').write_bytes(encode(ledger))
    (parsers / 'parser_manifest.json').write_bytes(encode(staged))
    report = dict(schema_version=1, fixture_manifest_sha256=sha(encode(manifest)),
        ledger_sha256=sha(encode(ledger)), parser_manifest=staged, declared_cases=1,
        executed_cases=1, cases=[dict(id='repair', case=case, status='passed', review={}, infrastructure_errors=[])], languages=languages)
    report_path = tmp_path / 'core.json'
    report_path.write_bytes(encode(report))
    return report_path, fixture, parsers, report


def test_exact_report_and_byte_identity(tmp_path):
    module = tool()
    report, fixtures, parsers, _ = inputs(tmp_path)
    assert len(module.load_inputs(report, fixtures, parsers)[1]) == 1
    (parsers / 'python_parser.wasm').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='SHA256'):
        module.load_inputs(report, fixtures, parsers)


@pytest.mark.parametrize('fault', ['duplicate', 'missing', 'definition', 'manifest'])
def test_rejects_incomplete_or_different_rust_evidence(tmp_path, fault):
    import json
    module = tool()
    path, fixtures, parsers, report = inputs(tmp_path)
    if fault == 'duplicate':
        report['cases'].append(report['cases'][0])
    elif fault == 'missing':
        report['cases'] = []
    elif fault == 'definition':
        report['cases'][0]['case']['old_sha256'] = '0' * 64
    else:
        report['fixture_manifest_sha256'] = '0' * 64
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        module.load_inputs(path, fixtures, parsers)


def test_installed_routing_ignores_only_benchmark_metadata(tmp_path):
    import hashlib
    import json
    module = tool()
    raw = b'wasm bytes'
    sha = hashlib.sha256(raw).hexdigest()
    (tmp_path / 'python_parser.wasm').write_bytes(raw)
    installed = dict(schema_version=1, parsers={'python': {'wasm': 'python_parser.wasm'}}, extension_index={'.py': ['python']})
    staged = dict(installed, components=[dict(component='python_parser.wasm', sha256=sha)])
    (tmp_path / 'parser_manifest.json').write_text(json.dumps(installed))
    provenance = dict(schema_version=1, artifacts={'python_parser.wasm': dict(sha256=sha, size_bytes=len(raw))})
    (tmp_path / 'wasm_provenance.json').write_text(json.dumps(provenance))
    assert module.verify_installed_components(tmp_path, staged)['artifacts']['python_parser.wasm']['sha256'] == sha
    installed['extension_index'] = {}
    (tmp_path / 'parser_manifest.json').write_text(json.dumps(installed))
    with pytest.raises(ValueError, match='routing'):
        module.verify_installed_components(tmp_path, staged)


def test_installed_component_provenance_must_agree_with_actual_bytes(tmp_path):
    import hashlib
    import json
    module = tool()
    raw = b'wasm bytes'
    (tmp_path / 'python_parser.wasm').write_bytes(raw)
    installed = dict(schema_version=1, parsers={}, extension_index={})
    staged = dict(installed, components=[dict(component='python_parser.wasm', sha256=hashlib.sha256(raw).hexdigest())])
    (tmp_path / 'parser_manifest.json').write_text(json.dumps(installed))
    (tmp_path / 'wasm_provenance.json').write_text(json.dumps(dict(artifacts={'python_parser.wasm': dict(sha256='0' * 64, size_bytes=len(raw))})))
    with pytest.raises(ValueError, match='provenance'):
        module.verify_installed_components(tmp_path, staged)


@pytest.mark.parametrize('status', ['runtime_error', 'invalid_result', 'unknown_status'])
def test_unadmitted_infrastructure_failure_cannot_be_filtered_out(tmp_path, status):
    import json
    module = tool()
    path, fixtures, parsers, report = inputs(tmp_path)
    manifest_path = fixtures / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['cases'][0]['expected'] = None
    manifest_path.write_text(json.dumps(manifest))
    report['fixture_manifest_sha256'] = module.digest(manifest_path.read_bytes())
    report['cases'][0].update(case=manifest['cases'][0], status=status, error='fuel exhausted')
    report['failed'] = True
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='Rust infrastructure failure'):
        module.load_inputs(path, fixtures, parsers)


@pytest.mark.parametrize('fault', ['missing_review', 'infrastructure_errors'])
def test_even_success_status_cannot_hide_infrastructure_failure(tmp_path, fault):
    import json
    module = tool()
    path, fixtures, parsers, report = inputs(tmp_path)
    if fault == 'missing_review':
        del report['cases'][0]['review']
    else:
        report['cases'][0]['infrastructure_errors'] = ['invalid source span']
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='Rust infrastructure failure'):
        module.load_inputs(path, fixtures, parsers)


def test_optional_accuracy_failure_is_not_infrastructure_failure(tmp_path):
    import json
    module = tool()
    path, fixtures, parsers, report = inputs(tmp_path)
    report['cases'][0]['status'] = 'accuracy_failure'
    report['cases'][0]['accuracy_errors'] = ['source expectation mismatch']
    report['failed'] = True  # Native runner may have used --strict.
    path.write_text(json.dumps(report))
    assert len(module.load_inputs(path, fixtures, parsers)[1]) == 1


def test_identity_can_leave_only_style_flag_unasserted():
    module = tool()
    expected = dict(has_semantic_changes=False, is_style_only=None,
                    required_changes=[], forbidden_change_types=['MODIFICATION'])
    output = review()
    output.update(changes=[], has_semantic_changes=False)
    identity = dict(old_sha256='1' * 64, new_sha256='1' * 64)
    assert module.expectation_errors(expected, output, identity) == []
    output['has_semantic_changes'] = True
    assert module.expectation_errors(expected, output, identity)


@pytest.mark.parametrize('case,semantic', [
    (dict(old_sha256='1' * 64, new_sha256='2' * 64), False),
    (dict(old_sha256='1' * 64, new_sha256='1' * 64), True),
    (dict(old_sha256='bad', new_sha256='bad'), False),
])
def test_null_style_flag_cannot_waive_nonidentity_or_semantic_expectations(case, semantic):
    module = tool()
    expected = dict(has_semantic_changes=semantic, is_style_only=None,
                    required_changes=[], forbidden_change_types=[])
    with pytest.raises(ValueError, match='identity'):
        module.expectation_errors(expected, review(), case)


def test_core_library_hash_must_match_installed_binary(tmp_path):
    import hashlib
    module = tool()
    path = tmp_path / 'lib.so'
    path.write_bytes(b'candidate')
    sha = hashlib.sha256(b'candidate').hexdigest()
    assert module.verify_core_library(path, {'cdylib_sha256': sha}) == sha
    with pytest.raises(ValueError, match='C ABI'):
        module.verify_core_library(path, {'cdylib_sha256': '0' * 64})
    with pytest.raises((KeyError, ValueError)):
        module.verify_core_library(path, {})


def test_descendant_evidence_requires_positions_within_one_change():
    module = tool()
    position = dict(start_line=0, start_col=4, end_line=0, end_col=7)
    def parent(label):
        return dict(label='function', position=dict(position, start_col=0),
                    children=[dict(label=label, position=position, children=[])])
    expected = dict(has_semantic_changes=True, is_style_only=False,
        required_changes=[dict(change_type='MODIFICATION', match='descendant_evidence',
            old_label="'a'", new_label="'b'", old_position=position, new_position=position)],
        forbidden_change_types=[])
    output = review()
    output['changes'] = [dict(change_type='MODIFICATION', old_node=parent("'a'"), new_node=parent("'b'"))]
    assert module.expectation_errors(expected, output) == []
    output['changes'][0]['new_node']['children'][0]['position'] = dict(position, start_col=3)
    assert module.expectation_errors(expected, output)
    output['changes'][0]['new_node'] = parent("'c'")
    output['changes'].append(dict(change_type='MODIFICATION', old_node=parent("'c'"), new_node=parent("'b'")))
    assert module.expectation_errors(expected, output)
    expected['required_changes'][0].pop('match')
    assert module.expectation_errors(expected, output)  # Default still matches roots only.


@pytest.mark.parametrize('change_type', ['MOVE', 'REORDER', 'STYLE_ONLY'])
def test_exact_identity_rejects_any_changes_even_with_false_semantic_flag(change_type):
    module = tool()
    expected = dict(has_semantic_changes=False, is_style_only=None,
                    required_changes=[], forbidden_change_types=[])
    output = review()
    output.update(has_semantic_changes=False, changes=[dict(change_type=change_type)])
    identity = dict(old_sha256='1' * 64, new_sha256='1' * 64)
    assert module.expectation_errors(expected, output, identity)
