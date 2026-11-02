import subprocess
import pytest
from intentumdiff import SemanticDiffer, CommitDiffer, DiffConfig
from intentumdiff.vcs.git_backend import GitVcsBackend

POLICY = 'guardrails:\n  protected:\n    - id: immutable.version\n      language: json\n      path: version\n      severity: immutable\n      files: [config.json]\n'

def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)

def repository(root):
    git(root, 'init'); git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', 'Test')
    (root / 'config.json').write_text('{"version":1}')
    (root / 'intentumdiff.yaml').write_text(POLICY)
    (root / 'nested').mkdir()
    git(root, 'add', '.'); git(root, 'commit', '-m', 'base')
    (root / 'config.json').write_text('{"version":2}')
    git(root, 'add', '.'); git(root, 'commit', '-m', 'change')

def review(root, route):
    if route.startswith('semantic'):
        return SemanticDiffer(DiffConfig(parallel=route.endswith('parallel'))).diff_commit(root, 'HEAD~1', 'HEAD')
    if route == 'stream':
        items = list(CommitDiffer().iter_file_diffs(root, 'HEAD~1', 'HEAD'))
        assert all(hasattr(item, 'file_diff') for item in items), items
        return [item.file_diff for item in items]
    kwargs = {'backend': GitVcsBackend(root)} if route == 'backend' else {}
    return CommitDiffer().diff_commit(root, 'HEAD~1', 'HEAD', **kwargs).file_diffs

@pytest.mark.parametrize('route', ['semantic', 'semantic_parallel', 'commit', 'stream', 'backend'])
def test_commit_policy_uses_selected_repository(tmp_path, route):
    repository(tmp_path)
    diffs = review(tmp_path / 'nested', route)
    assert len(diffs) == 1
    assert diffs[0].old_filename == 'config.json'
    assert {v.rule_id for v in diffs[0].guardrail_violations} == {'immutable.version'}

@pytest.mark.parametrize('route', ['semantic', 'semantic_parallel', 'commit', 'stream', 'backend'])
def test_commit_broken_policy_fails(tmp_path, route):
    repository(tmp_path)
    (tmp_path / 'intentumdiff.yaml').write_text('guardrails: [broken')
    with pytest.raises((RuntimeError, ValueError, AssertionError)):
        review(tmp_path / 'nested', route)

@pytest.mark.parametrize('route', ['semantic', 'commit', 'stream', 'backend'])
def test_historical_policy_does_not_follow_live_leaf_symlink(tmp_path, route):
    repository(tmp_path)
    (tmp_path / 'config.json').unlink()
    (tmp_path / 'config.json').symlink_to(tmp_path.parent / 'external.json')
    diffs = review(tmp_path, route)
    assert {v.rule_id for v in diffs[0].guardrail_violations} == {'immutable.version'}
