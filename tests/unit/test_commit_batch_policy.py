from pathlib import Path
import subprocess
import pytest
from intentumdiff import SemanticDiffer, DiffConfig

def git(root, *args):
    subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)

@pytest.mark.parametrize('route', ['public'])
@pytest.mark.parametrize('mode', ['', ':staged', 'HEAD'])
@pytest.mark.parametrize('policy', ['guardrails: [bad', None])
def test_batch_validates_discovered_policy(tmp_path, route, mode, policy):
    git(tmp_path, 'init'); git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'config', 'user.name', 'Test')
    nested = tmp_path / 'nested'; nested.mkdir()
    source = nested / 'x.py'; source.write_text('x = 1\n')
    git(tmp_path, 'add', '.'); git(tmp_path, 'commit', '-m', 'before')
    source.write_text('x = 2\n')
    if mode: git(tmp_path, 'add', '.')
    old = 'HEAD'
    if mode == 'HEAD':
        git(tmp_path, 'commit', '-m', 'after'); old = 'HEAD~1'
    (tmp_path / '.git/info/exclude').write_text('intentumdiff.yaml\n')
    if policy is not None: (nested / 'intentumdiff.yaml').write_text(policy)
    differ = SemanticDiffer()
    method = differ.diff_commit if route == 'public' else differ._diff_commit_certified_json
    if policy is not None:
        with pytest.raises((ValueError, RuntimeError), match='guardrail|policy|YAML|yaml'):
            method(tmp_path, old, mode)
    else:
        result = method(tmp_path, old, mode)
        if route == 'public':
            assert len(result) == 1 and result[0].has_semantic_changes
        else:
            assert result.used
