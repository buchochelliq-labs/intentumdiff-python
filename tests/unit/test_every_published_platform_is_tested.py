"""No wheel is built for a platform the suite does not run on.

This is issue #9's definition of done, encoded so it cannot quietly stop being true:

    "No wheel is published for a platform on which the full suite and the artefact smoke
     have not both passed. If we cannot test a platform, we do not ship a wheel for it -
     an untested artefact is a claim we have not earned."

The gap it closes was invisible for a reason worth remembering. `publish.yml` grew a wheel
target per platform as demand appeared, and `ci.yml` stayed on windows-latest. Neither file
mentions the other, so nothing anywhere related the set of artefacts we ship to the set of
platforms we test, and three of four wheels went out having never had a test executed on the
platform they target.

Adding a wheel target is a one-line change to a file nobody reads alongside the test config.
That is exactly the kind of drift a human reviewer misses and a comparison catches.

Reads the workflow YAML directly - no network, no CI, no GitHub API.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is a declared dev dependency; a missing one is a broken env, not a reason to skip silently")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), f"{path} does not exist"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _matrix_os(job: dict) -> set[str]:
    """Every `os` a job's matrix expands to, whether declared as a list or via `include`."""
    matrix = job.get("strategy", {}).get("matrix", {})
    found: set[str] = set()

    declared = matrix.get("os")
    if isinstance(declared, str):
        found.add(declared)
    elif isinstance(declared, list):
        found.update(declared)

    for entry in matrix.get("include", []) or []:
        if isinstance(entry, dict) and "os" in entry:
            found.add(entry["os"])

    # A job with no matrix still runs somewhere.
    if not found:
        runs_on = job.get("runs-on")
        if isinstance(runs_on, str) and "${{" not in runs_on:
            found.add(runs_on)

    return found


def _jobs_producing_wheels() -> dict[str, dict]:
    """Publish jobs whose steps build a wheel."""
    publish = _load("publish.yml")
    out = {}
    for name, job in publish.get("jobs", {}).items():
        blob = str(job.get("steps", ""))
        if "maturin" in blob or "pip wheel" in blob or "build --wheel" in blob:
            out[name] = job
    return out


def test_we_can_see_both_sides() -> None:
    """Guard the guard.

    Every assertion below compares two sets. If either came back empty - a renamed job, a
    restructured matrix, a moved workflow - the comparison would pass while comparing nothing.
    """
    built = set().union(*(_matrix_os(j) for j in _jobs_producing_wheels().values()))
    tested = _matrix_os(_load("ci.yml")["jobs"]["python-suite"])

    assert built, "found no wheel-building job in publish.yml - has it been renamed?"
    assert tested, "found no platforms in ci.yml's python-suite matrix"
    assert len(built) >= 2, f"expected wheels for several platforms, found {sorted(built)}"


def test_every_platform_we_build_a_wheel_for_is_also_tested() -> None:
    built = set().union(*(_matrix_os(j) for j in _jobs_producing_wheels().values()))
    tested = _matrix_os(_load("ci.yml")["jobs"]["python-suite"])

    untested = built - tested
    assert not untested, (
        "publish.yml builds a wheel for "
        + ", ".join(sorted(untested))
        + " but ci.yml never runs the suite there.\n"
        "Either add the platform to ci.yml's python-suite matrix, or stop shipping a wheel "
        "for it. An artefact nothing was tested on is a claim we have not earned (#9)."
    )


def test_the_suite_leg_also_smokes_a_real_wheel() -> None:
    """Running the suite on a platform is not the same as exercising the artefact.

    The suite runs against an editable install, which resolves imports from the source
    checkout. Every 0.0.1 defect passed that and appeared on first contact with a real wheel,
    so the leg has to build one and install it clean.
    """
    steps = _load("ci.yml")["jobs"]["python-suite"]["steps"]
    blob = "\n".join(str(s.get("run", "")) for s in steps)

    assert "pip wheel" in blob, "the matrix leg no longer builds a wheel"
    assert "smoke_published_wheel.py" in blob, (
        "the matrix leg no longer smoke-tests the wheel it built. Testing the source tree "
        "and shipping a wheel are different claims; only the second one reaches a user."
    )
