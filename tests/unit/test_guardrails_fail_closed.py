"""A guardrail check must never report success when it could not do its job.

WHY THIS EXISTS

`intentumdiff guardrails check --policy typo.yaml` used to print

    Guardrail check passed: 0 file(s) checked; no protected semantic changes.

and exit **0**, with nothing on stderr. A typo in the policy path was indistinguishable from
a clean run, on the one feature whose documented purpose is stopping API keys changing without
review. It failed OPEN — the gate went green precisely when the protection was absent.

The cause was not a bad error path; it was that there was no error path. The policy was loaded
lazily, per changed file, so with no changed files it was never loaded at all. Nothing
validated the policy before the command declared success.

These tests assert the general property, not the specific bug: **a gate must verify it can do
its job before reporting that it did.**

Exit code 2 rather than 1 on purpose — this is "you asked for something impossible", not
"the check ran and found violations" (which is 1 under --strict). A CI author needs to tell a
broken gate from a caught violation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

VALID = "guardrails:\n  protected:\n    - language: yaml\n      path: api_key\n      severity: immutable\n"
MISSING_KEY = "guardrails:\n  protected:\n    - language: yaml\n      severity: immutable\n"
NO_RULES = "guardrails:\n  protected: []\n"
NOT_A_MAPPING = "- just\n- a\n- list\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit — the command needs refs to resolve."""
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@e.x")
    git("config", "user.name", "t")
    (tmp_path / "config.yaml").write_text("api_key: sk-000\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    return tmp_path


def _check(repo: Path, policy: str | None) -> subprocess.CompletedProcess:
    args = [sys.executable, "-m", "intentumdiff", "guardrails", "check", str(repo),
            "--old", "HEAD", "--new", "HEAD"]
    if policy is not None:
        args += ["--policy", policy]
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("missing required key", MISSING_KEY),
        ("not a mapping", NOT_A_MAPPING),
        ("parses but protects nothing", NO_RULES),
    ],
)
def test_an_unusable_policy_fails_the_check(label: str, content: str, repo: Path, tmp_path: Path) -> None:
    """Every way a policy can be unusable must be an error, not a pass."""
    policy = tmp_path / "policy.yaml"
    policy.write_text(content, encoding="utf-8")

    result = _check(repo, str(policy))

    assert result.returncode != 0, (
        f"a policy that {label} produced exit 0.\n"
        f"stdout: {result.stdout[:300]}"
    )
    assert "passed" not in result.stdout.lower(), (
        f"a policy that {label} still reported 'passed'. A gate that cannot load its rules "
        f"has not checked anything, and must not say otherwise.\nstdout: {result.stdout[:300]}"
    )
    assert result.stderr.strip(), "the failure must be explained on stderr, not merely implied by the exit code"


def test_a_policy_path_that_does_not_exist_fails_loudly(repo: Path) -> None:
    """The original defect, in its worst form: exit 0 AND zero bytes on stderr.

    An explicit --policy is the caller asserting that file exists. Auto-discovery finding
    nothing is different and stays permissive — the user never claimed a policy was there.
    """
    result = _check(repo, "definitely_not_a_real_policy.yaml")

    assert result.returncode != 0, f"a nonexistent policy path exited 0: {result.stdout[:200]}"
    assert "passed" not in result.stdout.lower()
    assert "definitely_not_a_real_policy.yaml" in result.stderr, (
        "the message must name the path it could not find, or the user cannot see the typo"
    )


def test_a_valid_policy_still_passes(repo: Path, tmp_path: Path) -> None:
    """The guard must not have been achieved by failing everything.

    Without this, making the three tests above pass is trivial and useless.
    """
    policy = tmp_path / "good.yaml"
    policy.write_text(VALID, encoding="utf-8")

    result = _check(repo, str(policy))

    assert result.returncode == 0, (
        f"a valid policy with no changed files should pass.\n"
        f"stdout: {result.stdout[:300]}\nstderr: {result.stderr[:300]}"
    )
    assert "passed" in result.stdout.lower()


def test_no_policy_at_all_is_still_permissive(repo: Path) -> None:
    """Not specifying --policy must keep working.

    Auto-discovery finding nothing is a legitimate state: guardrails are opt-in. The fix
    distinguishes "you named a file that isn't there" from "you named no file".
    """
    result = _check(repo, None)
    assert result.returncode == 0, f"omitting --policy should not fail: {result.stderr[:300]}"
