"""Replay admitted external fixtures through an installed wheel, without engine patches.

Run with the wheel's Python interpreter, from a directory outside this checkout.
The Rust report is evidence of execution, never the source-expectation oracle.
Exit 2: infrastructure failure; exit 1: required accuracy/parity failure.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
import sys
import tempfile


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked_bytes(root: Path, relative: str, wanted: str) -> bytes:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f'path escapes artifact root: {relative}')
    raw = path.read_bytes()
    if digest(raw) != wanted:
        raise ValueError(f'SHA256 mismatch: {path}')
    return raw


def verify_installed(origin: Path, cwd: Path) -> None:
    checkout = Path(__file__).resolve().parents[1]
    if origin.resolve().is_relative_to(checkout) or cwd.resolve().is_relative_to(checkout):
        raise ValueError('acceptance requires installed wheel imports and cwd outside source checkout')
    if 'site-packages' not in origin.resolve().parts:
        raise ValueError(f'package does not resolve to installed site-packages: {origin}')


def verify_installed_components(directory: Path, staged: dict) -> dict:
    installed = json.loads((directory / 'parser_manifest.json').read_bytes())
    routing_fields = ('schema_version', 'parsers', 'extension_index')
    if any(installed[key] != staged[key] for key in routing_fields):
        raise ValueError('installed parser routing differs from Rust report')
    provenance = json.loads((directory / 'wasm_provenance.json').read_bytes())
    for component in staged['components']:
        name, sha = component['component'], component['sha256']
        raw = checked_bytes(directory, name, sha)
        entry = provenance['artifacts'][name]
        if entry['sha256'] != sha or entry['size_bytes'] != len(raw):
            raise ValueError(f'installed component provenance differs: {name}')
    return provenance


def verify_core_library(path: Path, core: dict) -> str:
    sha = digest(path.read_bytes())
    if core['cdylib_sha256'] != sha:
        raise ValueError('installed C ABI binary differs from Rust report')
    return sha


def validate_style_expectation(expected: dict, case: dict | None) -> None:
    if expected.get('is_style_only') is None:
        if (case is None or not isinstance(case.get('old_sha256'), str)
                or re.fullmatch('[0-9a-f]{64}', case['old_sha256']) is None
                or case['old_sha256'] != case.get('new_sha256')
                or expected.get('has_semantic_changes') is not False):
            raise ValueError('unasserted style flag is allowed only for exact-byte identity with semantic=false')


def expectation_errors(expected: dict, review: dict, case: dict | None = None) -> list[str]:
    """Match fixture assertions only; never infer semantic behavior in Python."""
    validate_style_expectation(expected, case)
    errors = []
    if case is not None and case.get('old_sha256') == case.get('new_sha256') and review['changes']:
        errors.append('exact-byte identity must have no changes')
    for key in ('has_semantic_changes', 'is_style_only'):
        if type(review[key]) is not bool:
            raise ValueError(f'{key} must be an explicit boolean')
        if key == 'is_style_only' and expected[key] is None:
            continue
        if type(expected[key]) is not bool:
            raise ValueError(f'{key} expectation must be an explicit boolean')
        if review[key] != expected[key]:
            errors.append(f'{key}: expected {expected[key]}, got {review[key]}')
    for wanted in expected['required_changes']:
        def matches(change):
            if change['change_type'] != wanted['change_type']:
                return False
            mode = wanted.get('match', 'root')
            if mode not in ('root', 'descendant_evidence'):
                raise ValueError(f'unknown required-change matcher: {mode}')
            if mode == 'root':
                return all(change.get(side + '_node') is not None
                    and change[side + '_node']['label'] == wanted[side + '_label']
                    for side in ('old', 'new') if side + '_label' in wanted)
            for side in ('old', 'new'):
                label, position = wanted[side + '_label'], wanted[side + '_position']
                if set(position) != {'start_line', 'start_col', 'end_line', 'end_col'} or any(
                        type(value) is not int or value < 0 for value in position.values()):
                    raise ValueError('descendant evidence needs exact nonnegative UTF-8 positions')
                stack = [change.get(side + '_node')]
                found = False
                while stack:
                    node = stack.pop()
                    if node is None:
                        continue
                    if node['label'] == label and node['position'] == position:
                        found = True
                        break
                    stack.extend(node.get('children') or [])
                if not found:
                    return False
            return True
        if not any(matches(change) for change in review['changes']):
            errors.append(f'missing required change: {wanted}')
    for change in review['changes']:
        if change['change_type'] in expected['forbidden_change_types']:
            errors.append(f"forbidden change: {change['change_type']}")
    return errors


def load_inputs(core_report: Path, fixtures: Path, parsers: Path):
    report = json.loads(core_report.read_bytes())
    manifest_raw = (fixtures / 'manifest.json').read_bytes()
    ledger_raw = (fixtures / 'ledger.json').read_bytes()
    manifest, ledger = json.loads(manifest_raw), json.loads(ledger_raw)
    staged = json.loads((parsers / 'parser_manifest.json').read_bytes())
    if any(doc['schema_version'] != 1 for doc in (report, manifest, ledger, staged)):
        raise ValueError('unsupported schema_version')
    if report['fixture_manifest_sha256'] != digest(manifest_raw) or report['ledger_sha256'] != digest(ledger_raw):
        raise ValueError('fixture/ledger identity differs from Rust report')
    if staged != report['parser_manifest']:
        raise ValueError('parser manifest differs from Rust report')
    cases = manifest['cases']
    by_id = {case['id']: case for case in cases}
    rows = {row['id']: row for row in report['cases']}
    if len(by_id) != len(cases) or len(rows) != len(report['cases']) or rows.keys() != by_id.keys():
        raise ValueError('duplicate, missing, or unexpected benchmark cases')
    if report['declared_cases'] != len(cases) or report['executed_cases'] != len(cases):
        raise ValueError('Rust report did not execute the complete fixture inventory')
    languages = {entry['language_id'] for entry in ledger['languages']}
    if len(languages) != 74 or {entry['language_id'] for entry in report['languages']} != languages:
        raise ValueError('incomplete 74-language report inventory')
    for case in cases:
        if rows[case['id']]['case'] != case:
            raise ValueError(f"fixture definition differs: {case['id']}")
        if case['expected'] is not None and 'is_style_only' in case['expected']:
            validate_style_expectation(case['expected'], case)
        row = rows[case['id']]
        allowed = ('unadmitted',) if case['expected'] is None else ('passed', 'accuracy_failure')
        if (row.get('status') not in allowed or not isinstance(row.get('review'), dict)
                or row.get('infrastructure_errors') != []):
            raise ValueError(f"Rust infrastructure failure: {case['id']}: {row.get('status')}: "
                             f"{row.get('error') or row.get('infrastructure_errors')}")
        for side in ('old', 'new'):
            checked_bytes(fixtures, case[side + '_path'], case[side + '_sha256']).decode('utf-8')
    for component in staged['components']:
        checked_bytes(parsers, component['component'], component['sha256'])
    return report, cases, rows, staged


def run(args) -> dict:
    report, cases, rows, staged = load_inputs(args.core_report, args.fixtures, args.parsers)
    # Import only after validating the external report. Never prepend checkout/src.
    import intentumdiff
    from intentumdiff import SemanticDiffer
    from intentumdiff.core.models import DiffConfig
    from intentumdiff.plugins.registry import builtin_wasm_dir
    from intentumdiff.rust_core import _c_abi_call, _extension_path, _load_backend
    from check_migration_parity import projection, check_source_spans

    origin = Path(intentumdiff.__file__).resolve()
    verify_installed(origin, Path.cwd())
    distribution = importlib.metadata.distribution('intentumdiff-python')
    direct_url = distribution.read_text('direct_url.json')
    if direct_url and json.loads(direct_url).get('dir_info', {}).get('editable'):
        raise ValueError('editable package installation is not wheel acceptance')
    if type(_load_backend()).__name__ != '_CtypesBackend':
        raise ValueError('installed package is not using the C ABI backend')
    installed_parsers = builtin_wasm_dir().resolve()
    if not installed_parsers.is_relative_to(origin.parent):
        raise ValueError('public Python parser directory is outside installed package')
    provenance = verify_installed_components(installed_parsers, staged)
    core_abi_sha256 = verify_core_library(Path(_extension_path()), report['core'])
    options = report['options']
    # This gate must not silently discard engine configuration it cannot transport.
    supported = set(DiffConfig.model_fields)
    if not isinstance(options, dict) or set(options) - supported:
        raise ValueError(f'unsupported Rust review options: {options}')
    config = DiffConfig(**options)
    output = dict(schema_version=1, interface='installed SemanticDiffer.diff_strings + live_diff_contents',
        package_version=distribution.version, package_path=str(origin),
        core_abi_path=_extension_path(), core_abi_sha256=core_abi_sha256,
        rust_core=report['core'], options=options, parser_manifest=staged,
        installed_wasm_provenance_sha256=digest((installed_parsers / 'wasm_provenance.json').read_bytes()),
        installed_wasm_provenance=provenance,
        fixture_manifest_sha256=report['fixture_manifest_sha256'], ledger_sha256=report['ledger_sha256'],
        core_report_sha256=digest(args.core_report.read_bytes()), languages=report['languages'], cases=[], failed=False)
    with tempfile.TemporaryDirectory(prefix='intentumdiff-binding-') as directory:
        with closing(SemanticDiffer(config)) as differ:
            for case in cases:
                if case['expected'] is None:
                    continue
                row = rows[case['id']]
                source = {side: checked_bytes(args.fixtures, case[side + '_path'], case[side + '_sha256']).decode('utf-8')
                          for side in ('old', 'new')}
                abi_result = _c_abi_call('live_diff_contents', directory, case['filename'], source['old'], source['new'],
                    json.dumps(options), str(args.parsers.resolve()))
                if not isinstance(abi_result, dict) or 'fallback' in abi_result or not isinstance(abi_result.get('diff'), dict):
                    raise ValueError(f"C ABI missing review: {case['id']}: {abi_result}")
                python = differ.diff_strings(source['old'], source['new'], case['filename']).model_dump(mode='json')
                routes = {'rust': row['review'], 'c_abi': abi_result['diff'], 'python': python}
                errors = {}
                for name, review in routes.items():
                    for key in ('has_semantic_changes', 'is_style_only', 'is_fallback'):
                        if type(review[key]) is not bool:
                            raise ValueError(f'{name}: missing boolean {key}')
                    if review['is_fallback'] or review['language'] != case['language_id']:
                        raise ValueError(f"{case['id']}: {name} fallback or incorrect language")
                    check_source_spans(dict(case, **source), review)
                    errors[name] = expectation_errors(case['expected'], review, case)
                parity = projection(routes['rust']) == projection(routes['c_abi']) == projection(routes['python'])
                failed = not parity or (case['required'] and any(errors.values()))
                output['failed'] |= failed
                output['cases'].append(dict(id=case['id'], required=case['required'],
                    status='failed' if failed else ('accuracy_failure' if any(errors.values()) else 'passed'),
                    accuracy_errors=errors, parity=parity, reviews=routes,
                    old_sha256=case['old_sha256'], new_sha256=case['new_sha256']))
    output['executed_admitted_cases'] = len(output['cases'])
    if not output['cases']:
        raise ValueError('no admitted cases executed')
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('core-report', 'fixtures', 'parsers', 'report'):
        parser.add_argument('--' + argument, required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    except (OSError, ValueError, KeyError, TypeError, AssertionError, RuntimeError, ImportError) as error:
        print(f'external binding infrastructure: {error}', file=sys.stderr)
        return 2
    print(f"{result['executed_admitted_cases']} installed-wheel fixture replays; failed={result['failed']}")
    return int(result['failed'])


if __name__ == '__main__':
    raise SystemExit(main())
