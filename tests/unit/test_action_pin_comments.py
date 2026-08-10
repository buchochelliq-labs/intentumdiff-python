"""Network-free invariants on the workflow action pins.

`scripts/check_action_pin_comments.py` is the real gate: it resolves each pinned SHA against
the upstream tag list and catches a comment that names the wrong version. It needs the network,
so it runs as its own CI job rather than here - unit tests in this repo do no network.

What CAN be checked offline is internal consistency, and it is worth checking because it fails
first. Every mismatch found on 2026-08-10 (checkout, upload-artifact, download-artifact - all
claiming a version three-to-four majors below the SHA they carried) appeared in more than one
workflow file, and the update touched some occurrences and not others. That asymmetry is
visible without asking GitHub anything: the same SHA carrying two different version comments
is a contradiction on its face.

These tests are cheap and catch the common case in milliseconds. The networked gate catches the
rest.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

PIN = re.compile(
    r"uses:\s*(?P<action>[\w.\-]+/[\w.\-]+)(?:/[\w.\-/]+)?"
    r"@(?P<sha>[0-9a-f]{40})[^\S\n]*(?:\#[^\S\n]*(?P<comment>\S+))?"
)


def _pins() -> list[tuple[str, str, str, str]]:
    """(workflow, action, sha, comment) for every SHA-pinned action."""
    found = []
    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        for m in PIN.finditer(wf.read_text(encoding="utf-8")):
            found.append((wf.name, m["action"], m["sha"], (m["comment"] or "").strip()))
    return found


def test_there_are_pins_to_check():
    """Guard the guard.

    Every assertion below passes vacuously against an empty list, so a regex that quietly
    stops matching - or a workflow directory moved out from under this file - would turn the
    whole module green while checking nothing.
    """
    assert WORKFLOWS.is_dir(), f"{WORKFLOWS} does not exist"
    assert len(_pins()) >= 5, f"expected several pinned actions, found {len(_pins())}"


def test_every_pinned_sha_carries_a_version_comment():
    """A bare 40-character SHA is unreviewable.

    Pinning by SHA is the correct supply-chain posture, but it only stays reviewable because
    of the trailing comment. Without one, nobody approving the diff can tell v4 from v8.
    """
    bare = [f"{wf}: {action}@{sha[:10]}" for wf, action, sha, comment in _pins() if not comment]
    assert not bare, "pinned actions with no version comment:\n  " + "\n  ".join(bare)


def test_the_same_sha_never_carries_two_different_comments():
    """One commit is one version, so two labels for it means at least one is wrong.

    This is the exact shape of the 2026-08-10 defect: an update rewrote the SHA in every file
    but the comment in only some, leaving `actions/checkout@3d3c42e5` documented as v4.2.2 in
    one place and v7.0.1 in another.
    """
    by_sha: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _wf, action, sha, comment in _pins():
        if comment:
            by_sha[(action, sha)].add(comment)

    conflicts = {k: v for k, v in by_sha.items() if len(v) > 1}
    assert not conflicts, "the same SHA is documented as more than one version:\n  " + "\n  ".join(
        f"{action}@{sha[:10]} is labelled {sorted(labels)}" for (action, sha), labels in conflicts.items()
    )


def test_the_same_version_never_maps_to_two_different_shas():
    """The mirror image: one label pointing at two commits.

    Arises when a bump lands in one workflow and not another, so the estate silently runs two
    different builds of what the files both call the same version.
    """
    by_version: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _wf, action, sha, comment in _pins():
        if comment:
            by_version[(action, comment)].add(sha)

    conflicts = {k: v for k, v in by_version.items() if len(v) > 1}
    assert not conflicts, "one version label points at more than one SHA:\n  " + "\n  ".join(
        f"{action} {version} -> {sorted(s[:10] for s in shas)}" for (action, version), shas in conflicts.items()
    )


@pytest.mark.parametrize("workflow", sorted(p.name for p in WORKFLOWS.glob("*.y*ml")))
def test_no_action_is_pinned_to_a_mutable_ref(workflow: str):
    """A tag can be moved; a SHA cannot.

    `uses: foo/bar@v4` re-resolves on every run, so an upstream compromise reaches this repo
    without any change here. Local actions (`./...`) and reusable workflows in this org are
    exempt - they are not third-party supply chain.
    """
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    mutable = [
        line.strip()
        for line in text.splitlines()
        if (m := re.search(r"uses:\s*(\S+)", line))
        and not m.group(1).startswith((".", "./"))
        and "buchochelliq-labs/" not in m.group(1)
        and "@" in m.group(1)
        and not re.search(r"@[0-9a-f]{40}$", m.group(1))
    ]
    assert not mutable, f"{workflow} pins a mutable ref:\n  " + "\n  ".join(mutable)
