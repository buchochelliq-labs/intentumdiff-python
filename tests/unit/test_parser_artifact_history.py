"""Pinned builds remain discoverable after unrelated workflow runs accumulate."""
import json
import runpy
from pathlib import Path


def test_pinned_component_survives_more_than_one_page_of_other_workflows(monkeypatch):
    stage = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/provision_build_inputs.py'))['_successful_artifact_runs']
    seen = []
    def get(url):
        seen.append(url)
        assert 'head_sha=pinned' in url
        assert 'status=success' in url
        # Simulate the repository's history: a full page of scheduled jobs,
        # then the successful build of the same pinned commit.
        if '&page=2' in url:
            return json.dumps({'workflow_runs': [{'id': 101, 'head_sha': 'pinned'}]}).encode()
        return json.dumps({'workflow_runs': [{'id': i, 'head_sha': 'pinned'} for i in range(100)]}).encode()
    assert any(run['id'] == 101 for run in stage(get, 'https://api.github.com', 'buchochelliq-labs', 'intentumdiff-python-parser', 'pinned'))
    assert len(seen) == 2


def test_history_lookup_preserves_pin_and_skips_github_internal_workflows():
    stage = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/provision_build_inputs.py'))['_successful_artifact_runs']
    payload = {'workflow_runs': [
        {'id': 1, 'head_sha': 'other', 'path': '.github/workflows/ci.yml'},
        {'id': 2, 'head_sha': 'pinned', 'path': 'dynamic/dependabot/dependabot-updates'},
        {'id': 3, 'head_sha': 'pinned', 'path': '.github/workflows/ci.yml'},
    ]}
    get = lambda _: json.dumps(payload).encode()
    assert stage(get, 'https://api.github.com', 'org', 'repo', 'pinned') == [payload['workflow_runs'][2]]
