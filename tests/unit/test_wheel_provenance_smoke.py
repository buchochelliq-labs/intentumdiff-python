"""Exercise the installed-resource smoke probe without native/parser dependencies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.smoke_published_wheel import PROVENANCE_SCRIPT


@pytest.mark.parametrize('defect', ['none', 'missing', 'tampered', 'extra', 'count'])
def test_installed_provenance_probe(tmp_path: Path, defect: str) -> None:
    package = tmp_path / 'intentumdiff'
    wasm = package / 'wasm'
    wasm.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    data = b'\x00asm fixture'
    (wasm / 'fixture.wasm').write_bytes(data)
    manifest = {
        'schema_version': 1, 'artifact_count': 1,
        'artifacts': {'fixture.wasm': {
            'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
        }},
    }
    if defect == 'count':
        manifest['artifact_count'] = 2
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
