"""scripts/release_mode.py: a repo release and an orchestrated release stay distinguishable.

A maintainer's own ``v*`` tag push is a repo release and keeps working exactly as before.
The release orchestrator (buchochelliq-labs/intentumdiff-release) creates an annotated tag
with a ``Release-Orchestrator`` line and dispatches publish on it with every pin as an input.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "release_mode", Path(__file__).resolve().parents[2] / "scripts" / "release_mode.py"
)
mode = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mode)

RUN = "https://github.com/buchochelliq-labs/intentumdiff-release/actions/runs/123"
REPO = "buchochelliq-labs/intentumdiff-python"
PINS = "candidate_run_id,candidate_source_sha,candidate_core_sha,candidate_wheels_json"


def _api(tags: dict[str, str | None]):
    """tags: name -> annotated message, or None for a lightweight tag."""
    def get(path: str) -> dict:
        if "/git/ref/tags/" in path:
            name = path.rsplit("/", 1)[1]
            message = tags[name]
            if message is None:
                return {"object": {"type": "commit", "sha": "c" * 40}}
            return {"object": {"type": "tag", "sha": f"tagobj-{name}"}}
        if "/git/tags/tagobj-" in path:
            return {"message": tags[path.rsplit("tagobj-", 1)[1]]}
        raise AssertionError(path)
    return get


def _env(**overrides) -> dict[str, str]:
    env = {"REPOSITORY": REPO, "REF": "refs/tags/v0.0.3b2", "REF_NAME": "v0.0.3b2",
           "REQUIRED_INPUTS": PINS}
    env.update(overrides)
    return env


def _pins() -> dict[str, str]:
    return {f"INPUT_{n.upper()}": "x" for n in PINS.split(",")}


ORCHESTRATED_TAG = f"intentumdiff-python 0.0.3b2\n\nRelease-Orchestrator: {RUN}\n"


def test_a_maintainers_own_tag_push_is_a_repo_release():
    for message in (None, "v0.0.3b2\n"):  # lightweight, or annotated by hand
        got, _ = mode.decide(_env(EVENT_NAME="push"), _api({"v0.0.3b2": message}))
        assert got == "repo"


def test_the_tag_push_of_an_orchestrated_tag_defers_to_the_orchestrated_dispatch():
    got, why = mode.decide(_env(EVENT_NAME="push"), _api({"v0.0.3b2": ORCHESTRATED_TAG}))
    assert got == "deferred"
    assert RUN in why[0]


def test_an_orchestrated_dispatch_on_its_tag_publishes_from_inputs():
    env = _env(EVENT_NAME="workflow_dispatch", ORCHESTRATOR_RUN=RUN, DRY_RUN="false", **_pins())
    got, _ = mode.decide(env, _api({"v0.0.3b2": ORCHESTRATED_TAG}))
    assert got == "orchestrated"
    assert got in mode.PUBLISHING


def test_an_orchestrated_dispatch_never_falls_back_to_repository_variables():
    pins = _pins()
    pins.pop("INPUT_CANDIDATE_WHEELS_JSON")
    env = _env(EVENT_NAME="workflow_dispatch", ORCHESTRATOR_RUN=RUN, DRY_RUN="false", **pins)
    with pytest.raises(mode.ModeError, match="candidate_wheels_json"):
        mode.decide(env, _api({"v0.0.3b2": ORCHESTRATED_TAG}))


def test_the_orchestrator_cannot_publish_a_tag_it_did_not_create():
    env = _env(EVENT_NAME="workflow_dispatch", ORCHESTRATOR_RUN=RUN, DRY_RUN="false", **_pins())
    with pytest.raises(mode.ModeError, match="not created by the release orchestrator"):
        mode.decide(env, _api({"v0.0.3b2": None}))


def test_an_orchestrated_release_must_run_on_a_tag():
    env = _env(EVENT_NAME="workflow_dispatch", ORCHESTRATOR_RUN=RUN, DRY_RUN="false",
               REF="refs/heads/main", REF_NAME="main", **_pins())
    with pytest.raises(mode.ModeError, match="must run on its tag"):
        mode.decide(env, _api({}))


def test_an_orchestrated_rehearsal_verifies_without_a_tag():
    env = _env(EVENT_NAME="workflow_dispatch", ORCHESTRATOR_RUN=RUN, DRY_RUN="true",
               REF="refs/heads/main", REF_NAME="main", **_pins())
    got, _ = mode.decide(env, _api({}))
    assert got == "orchestrated-dry-run"
    assert got not in mode.PUBLISHING


@pytest.mark.parametrize("run", [
    "https://github.com/someone/else/actions/runs/1",
    "https://github.com/buchochelliq-labs/intentumdiff-release/pulls/1",
    "buchochelliq-labs/intentumdiff-release#1",
])
def test_only_a_run_of_the_release_repository_can_orchestrate(run):
    env = _env(EVENT_NAME="workflow_dispatch", ORCHESTRATOR_RUN=run, DRY_RUN="false", **_pins())
    with pytest.raises(mode.ModeError, match="must be a run of"):
        mode.decide(env, _api({"v0.0.3b2": ORCHESTRATED_TAG}))


def test_a_tag_naming_a_foreign_orchestrator_is_refused():
    message = "Release-Orchestrator: https://github.com/someone/else/actions/runs/1\n"
    with pytest.raises(mode.ModeError, match="not exactly one run"):
        mode.decide(_env(EVENT_NAME="push"), _api({"v0.0.3b2": message}))


def test_a_manual_dispatch_is_still_a_rehearsal_here():
    for dry_run in ("true", "false"):
        got, _ = mode.decide(_env(EVENT_NAME="workflow_dispatch", DRY_RUN=dry_run), _api({}))
        assert got == "rehearsal"


def test_a_manual_dispatch_on_a_tag_publishes_only_where_that_was_already_supported():
    env = _env(EVENT_NAME="workflow_dispatch", DRY_RUN="false", DISPATCH_PUBLISHES_FROM_TAG="true")
    assert mode.decide(env, _api({}))[0] == "repo"
    env["DRY_RUN"] = "true"
    assert mode.decide(env, _api({}))[0] == "rehearsal"


def test_other_events_never_publish():
    assert mode.decide(_env(EVENT_NAME="pull_request"), _api({}))[0] == "none"
    assert mode.decide(_env(EVENT_NAME="push", REF="refs/heads/main"), _api({}))[0] == "none"
