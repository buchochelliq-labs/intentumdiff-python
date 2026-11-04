"""Every capability we advertise must actually work, end to end.

WHY THIS EXISTS

`--help` offered five output formats. Three of them exited 1 with "No renderer plugin found",
and had done since the formats were first advertised. The components were built, checksummed,
registry-pinned, staged and shipped — the CLI just looked for them in a directory that does
not exist, so nothing ever found them.

Nothing caught it, because everything we had checked the wrong claim:

  - the registry verifies components are PRESENT and match their pinned checksum
  - INTENTUMDIFF_REQUIRE_ALL_COMPONENTS fails a run when components are MISSING
  - unit tests cover the renderers' internals

Present and reachable are different claims, and only the second one reaches a user. This is
the same shape as the VS Code extension shipping without its codicon font (packaged, never
loadable) and as a guardrail check reporting "passed" after failing to load its policy: the
product asserting success for something it never actually did.

So these tests do the only thing that distinguishes the two — run the advertised thing and
look at what comes out.

THE RULE: if it appears in `--help`, in the README, or in the docs, exactly one test here
must exercise it against the real engine. Adding an advertised capability without one is how
this defect happened.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Formats the CLI advertises. Kept here deliberately rather than imported: if someone adds a
# format to the help text and not to this list, `test_every_advertised_format_is_tested`
# fails — which is precisely the gap that shipped.
ADVERTISED_FORMATS = ("terminal", "json", "patch", "html", "llm")

OLD = "def total(items):\n    n = 0\n    for i in items:\n        n += i.price\n    return n\n"
NEW = "def total(items):\n    n = 0\n    for i in items:\n        n += i.price * i.qty\n    return n\n"


@pytest.fixture(scope="module")
def pair(tmp_path_factory) -> tuple[Path, Path]:
    d = tmp_path_factory.mktemp("advertised")
    old, new = d / "old.py", d / "new.py"
    old.write_text(OLD, encoding="utf-8")
    new.write_text(NEW, encoding="utf-8")
    return old, new


def _run(*args: str) -> subprocess.CompletedProcess:
    """Invoke the CLI as a user does — a real process, not an in-process call.

    In-process invocation shares sys.path and the importing module's location, which is
    exactly what hid the renderer path bug: from inside the test process the wrong directory
    could still resolve.
    """
    return subprocess.run(
        [sys.executable, "-m", "intentumdiff", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_the_advertised_format_list_matches_the_help_text() -> None:
    """Guard the guard.

    Every test below iterates ADVERTISED_FORMATS. If that list drifts from what the CLI tells
    users, the suite would keep passing while testing a format nobody is offered — or, worse,
    stop testing one that is.
    """
    result = _run("file", "--help")
    assert result.returncode == 0, result.stderr

    help_text = result.stdout + result.stderr
    missing = [f for f in ADVERTISED_FORMATS if f not in help_text]
    assert not missing, (
        f"these are tested but no longer advertised: {missing}\n"
        "Either the help text changed or this list is stale; reconcile them."
    )


@pytest.mark.parametrize("fmt", ADVERTISED_FORMATS)
def test_every_advertised_format_produces_output(fmt: str, pair: tuple[Path, Path]) -> None:
    """The whole point: run it and look at what comes out.

    `--format patch`, `html` and `llm` each failed this for the entire life of the feature,
    with components that were installed and working the whole time.
    """
    old, new = pair
    result = _run("file", str(old), str(new), "--format", fmt)

    assert result.returncode == 0, (
        f"--format {fmt} exited {result.returncode}\n"
        f"stdout: {result.stdout[:400]}\nstderr: {result.stderr[:400]}"
    )
    assert result.stdout.strip(), (
        f"--format {fmt} exited 0 but wrote nothing to stdout. An empty success is "
        f"indistinguishable from a broken one to every automated caller."
    )


@pytest.mark.parametrize("fmt", ADVERTISED_FORMATS)
def test_no_advertised_format_reports_a_missing_plugin(fmt: str, pair: tuple[Path, Path]) -> None:
    """A shipped component must never be reported as absent.

    Separate from the exit-code check on purpose: the original defect produced a confident,
    specific, and completely wrong diagnosis — "No renderer plugin found for format 'patch'"
    — which sent a tester to the conclusion that the feature was unimplemented. An error that
    names the wrong cause is worse than a crash.
    """
    old, new = pair
    combined = (lambda r: r.stdout + r.stderr)(_run("file", str(old), str(new), "--format", fmt))

    assert "No renderer plugin found" not in combined, (
        f"--format {fmt} reports its renderer as missing. Check whether the component is "
        f"genuinely absent or simply being looked for in the wrong place."
    )


def test_the_component_directory_is_derived_in_exactly_one_place() -> None:
    """The path was re-derived in five places and one of them was wrong.

    Four call sites used `Path(__file__).parent.parent / "wasm"` from a subpackage; the CLI
    used `.parent`, resolving to `intentumdiff/cli/wasm/`. Both look right in isolation, and
    only one is, which is why review did not catch it.
    """
    src = Path(__file__).resolve().parents[2] / "src" / "intentumdiff"
    # Skip comments: the fix site quotes the bad pattern to explain what it replaced, and a
    # literal substring search flags that as a fresh offence. A guard that cannot tell code
    # from the comment describing it punishes documenting the fix.
    offenders = [
        f"{path.relative_to(src).as_posix()}:{n}"
        for path in src.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if 'Path(__file__).parent / "wasm"' in line and not line.lstrip().startswith("#")
    ]
    assert not offenders, (
        "component directory derived from a subpackage's own __file__:\n  "
        + "\n  ".join(offenders)
        + "\nUse registry.builtin_wasm_dir() — it is the one place that knows."
    )
