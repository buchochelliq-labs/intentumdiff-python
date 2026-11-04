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


def test_empty_filtered_lookup_recovers_exact_pin_from_history():
    stage = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/provision_build_inputs.py'))['_successful_artifact_runs']
    seen = []
    pinned = {'id': 7, 'head_sha': 'pinned', 'path': '.github/workflows/ci.yml'}
    def get(url):
        seen.append(url)
        if 'head_sha=' in url:
            return json.dumps({'workflow_runs': []}).encode()
        return json.dumps({'workflow_runs': [
            {'id': 8, 'head_sha': 'newer', 'path': '.github/workflows/ci.yml'}, pinned,
        ]}).encode()
    assert stage(get, 'https://api.github.com', 'org', 'repo', 'pinned') == [pinned]
    assert len(seen) == 2
    assert all('status=success' in url for url in seen)


def test_empty_filtered_lookup_never_substitutes_a_different_commit(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda _: None)
    stage = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/provision_build_inputs.py'))['_successful_artifact_runs']
    seen = []
    def get(url):
        seen.append(url)
        return json.dumps({'workflow_runs': [
            {'id': 8, 'head_sha': 'other', 'path': '.github/workflows/ci.yml'},
        ]}).encode()
    assert stage(get, 'https://api.github.com', 'org', 'repo', 'pinned') == []
    assert len(seen) == 6


def test_transient_empty_history_is_retried_without_changing_pin(monkeypatch):
    import time
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    stage = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/provision_build_inputs.py'))['_successful_artifact_runs']
    seen = []
    pinned = {'id': 7, 'head_sha': 'pinned', 'path': '.github/workflows/ci.yml'}
    def get(url):
        seen.append(url)
        return json.dumps({'workflow_runs': [] if len(seen) <= 2 else [pinned]}).encode()
    assert stage(get, 'https://api.github.com', 'org', 'repo', 'pinned') == [pinned]
    assert len(seen) == 3
    assert 'head_sha=pinned' in seen[-1]
