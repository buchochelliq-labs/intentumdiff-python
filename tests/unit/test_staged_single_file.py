import json
import subprocess
import sys
import pytest

def git(root, *args):
    subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)

@pytest.mark.parametrize('case', ['modified', 'unstaged_only', 'added', 'deleted', 'protected'])
def test_cli_single_file_reads_index(tmp_path, case):
    git(tmp_path, 'init'); git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'config', 'user.name', 'Test')
    path = tmp_path / 'config.json'
    path.write_text('{"version":1}')
    if case == 'protected':
        (tmp_path / 'intentumdiff.yaml').write_text('guardrails:\n  protected:\n    - id: immutable.version\n      language: json\n      path: version\n      severity: immutable\n      files: [config.json]\n')
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-m', 'base')
    if case == 'added':
        path = tmp_path / 'added.json'
    path.write_text('{"version":2}')
    if case != 'unstaged_only':
        if case == 'deleted':
            path.unlink()
        git(tmp_path, 'add', '.')
        path.write_text('{"version":1}')
    command = [sys.executable, '-m', 'intentumdiff', 'git', str(tmp_path), path.name, '--staged', '--json']
    if case == 'protected':
        command.append('--guardrails-strict')
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == (2 if case == 'protected' else 0), result.stderr
    data = json.loads(result.stdout)
    assert data['has_semantic_changes'] == (case != 'unstaged_only')
    if case == 'protected':
        assert {v['rule_id'] for v in data['guardrail_violations']} == {'immutable.version'}

@pytest.mark.parametrize('payload', [b'\x00binary', b'\xffinvalid'])
def test_staged_nontext_never_returns_clean(tmp_path, payload):
    from intentumdiff import SemanticDiffer
    from intentumdiff.sources.git_source import StagedSource
    git(tmp_path, 'init'); git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'config', 'user.name', 'Test')
    path = tmp_path / 'config.json'
    path.write_text('{"version":1}')
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-m', 'base')
    path.write_bytes(payload); git(tmp_path, 'add', '.')
    with pytest.raises((ValueError, UnicodeError, RuntimeError)):
        SemanticDiffer().diff(StagedSource(tmp_path, 'config.json'))
