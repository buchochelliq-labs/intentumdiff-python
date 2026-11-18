"""Exercise the installed-resource smoke probe without native/parser dependencies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.smoke_published_wheel import PROVENANCE_SCRIPT, Smoke


@pytest.mark.parametrize('defect', ['none', 'missing', 'tampered', 'extra', 'count', 'source', 'registry'])
def test_installed_provenance_probe(tmp_path: Path, defect: str) -> None:
    package = tmp_path / 'intentumdiff'
    wasm = package / 'wasm'
    wasm.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    data = b'\x00asm fixture'
    (wasm / 'fixture.wasm').write_bytes(data)
    manifest = {
        'schema_version': 1, 'registry_commit': 'c' * 40, 'artifact_count': 1,
        'artifacts': {'fixture.wasm': {
            'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'source': {
                'repository': 'https://github.com/buchochelliq-labs/intentumdiff-fixture-parser',
                'commit': 'a' * 40,
                'artifact': 'parser-wasm',
                'artifact_id': 123,
                'artifact_archive_sha256': 'b' * 64,
                'abi_target': '1.0.0',
            },
        }},
    }
    if defect == 'count':
        manifest['artifact_count'] = 2
    if defect == 'source':
        manifest['artifacts']['fixture.wasm'].pop('source')
    if defect == 'registry':
        manifest['registry_commit'] = 'main'
    if defect != 'missing':
        (wasm / 'wasm_provenance.json').write_text(json.dumps(manifest), encoding='utf-8')
    if defect == 'tampered':
        (wasm / 'fixture.wasm').write_bytes(b'\x00asm changed')
    if defect == 'extra':
        (wasm / 'extra.wasm').write_bytes(data)
    result = subprocess.run(
        [sys.executable, '-c', PROVENANCE_SCRIPT], cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH': str(tmp_path)}, capture_output=True, text=True,
    )
    assert (result.returncode == 0) == (defect == 'none'), result.stderr
    if defect == 'none':
        assert 'VERIFIED 1 installed Wasm components' in result.stdout
    else:
        assert result.stderr


def test_clean_stderr_covers_every_command(tmp_path: Path) -> None:
    """Noise from any command fails the gate, not only from the last one run."""
    smoke = Smoke(tmp_path)
    smoke.exec([sys.executable, '-c', 'import sys; sys.stderr.write("Failed to catalog x\\n")'],
               label='first')
    smoke.exec([sys.executable, '-c', 'print("ok")'], label='last')
    assert smoke.unexpected_stderr() == [('first', 'Failed to catalog x')]


def test_expected_stderr_allows_only_its_pattern(tmp_path: Path) -> None:
    smoke = Smoke(tmp_path)
    smoke.exec([sys.executable, '-c', 'import sys; sys.stderr.write("Parse errors detected\\n")'],
               label='broken input', expected_stderr=r'Parse errors detected')
    assert smoke.unexpected_stderr() == []
    smoke.exec([sys.executable, '-c',
                'import sys; sys.stderr.write("Parse errors detected\\nTraceback\\n")'],
               label='broken input 2', expected_stderr=r'Parse errors detected')
    assert smoke.unexpected_stderr() == [('broken input 2', 'Traceback')]
