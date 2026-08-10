"""Assert every pinned GitHub Action SHA agrees with the version in its trailing comment.

WHY THIS EXISTS
---------------
Actions are pinned by full commit SHA, which is the right call: a tag is mutable and a
compromised action can be re-tagged under you. But a SHA is unreadable, so every pin carries
a trailing comment naming the version:

    uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

That comment is the ONLY human-readable part of the pin. It is what a reviewer actually reads
when approving a workflow change, and it is what Dependabot reads to decide whether a bump is
even needed. When it disagrees with the SHA, three things break at once:

  1. Reviewers approve a version they were not shown. On 2026-08-10 this repo pinned
     actions/checkout v7.0.1, upload-artifact v7.0.1 and download-artifact v8.0.1 while the
     comments claimed v4.2.2, v4.5.0 and v4.1.4 - a three-major gap in what the diff said.
  2. Dependabot proposes bumps that are already applied, because it trusts the comment. Those
     PRs then present as CONFLICTING or failing, and the wasted review time looks like a
     Dependabot problem rather than a data problem.
  3. Supply-chain review stops working. "Pin to SHA" buys nothing if nobody can tell which
     release the SHA is, and the label they use to tell is wrong.

All five mismatches were introduced the same way: a bump replaced the SHA and left the comment
untouched. Nothing in CI could see it, because a wrong comment is still valid YAML and the
workflow runs perfectly - just not the version everyone believes.

WHAT IT DOES
------------
Resolves each pinned SHA against the upstream repository's real tag list and compares it to the
comment. Trusts nothing written in the file.

Pins whose comment names a BRANCH rather than a version - `# stable` for dtolnay/rust-toolchain,
`# release/v1` for pypa/gh-action-pypi-publish - are a deliberate, different convention and are
reported separately rather than failed, since there is no version for them to disagree with.

Needs only public read access; GITHUB_TOKEN is enough, so this runs on Dependabot and fork PRs
where repository secrets are unavailable.

Usage:
    python scripts/check_action_pin_comments.py            # exits 1 on any mismatch
    python scripts/check_action_pin_comments.py --list     # report only, always exits 0
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

WORKFLOWS = Path(".github/workflows")

# `uses: owner/repo@<40 hex> # comment`. Actions pinned to a tag or branch rather than a SHA
# are not this script's business - a separate lint enforces SHA pinning.
PIN = re.compile(
    r"""uses:\s*
        (?P<action>[\w.\-]+/[\w.\-]+)   # owner/repo, ignoring any subpath
        (?:/[\w.\-/]+)?
        @(?P<sha>[0-9a-f]{40})
        [^\S\n]*
        (?:\#[^\S\n]*(?P<comment>\S+))?
    """,
    re.VERBOSE,
)

# A comment naming one of these is tracking a moving ref on purpose, not claiming a version.
BRANCH_LIKE = re.compile(r"^(stable|main|master|release/.*|v\d+$)")


def _api(path: str) -> list | dict | None:
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.environ.get("GITHUB_TOKEN") else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


_tags: dict[str, dict[str, str] | None] = {}


def tags_for(action: str) -> dict[str, str] | None:
    """Map commit SHA -> tag name for an action repo, or None if it could not be read.

    The /tags endpoint reports the COMMIT a tag points at, so annotated tags are already
    dereferenced. Fetching refs/tags directly would hand back the tag object's own SHA and
    silently match nothing.
    """
    if action in _tags:
        return _tags[action]
    out: dict[str, str] = {}
    for page in range(1, 6):  # 500 tags is far beyond any action's release count
        data = _api(f"repos/{action}/tags?per_page=100&page={page}")
        if data is None:
            _tags[action] = None
            return None
        if not data:
            break
        for tag in data:
            sha, name = (tag.get("commit") or {}).get("sha"), tag.get("name")
            if not sha or not name:
                continue
            # Several tags can share a commit (v4, v4.2, v4.2.2). Keep the most specific,
            # so a v7.0.1 pin is never "matched" by the floating v7 that shares its commit.
            if sha not in out or len(name) > len(out[sha]):
                out[sha] = name
    _tags[action] = out
    return out


def main() -> int:
    report_only = "--list" in sys.argv

    if not WORKFLOWS.is_dir():
        print(f"no {WORKFLOWS} directory - nothing to check")
        return 0

    mismatched: list[str] = []
    unresolved: list[str] = []
    branch_pins: list[str] = []
    ok = 0

    for wf in sorted(WORKFLOWS.glob("*.y*ml")):
        for m in PIN.finditer(wf.read_text(encoding="utf-8")):
            action, sha, comment = m["action"], m["sha"], (m["comment"] or "").strip()
            where = f"{wf.name}: {action}@{sha[:10]}"

            if not comment:
                mismatched.append(f"{where} has NO version comment (a bare SHA is unreviewable)")
                continue
            if BRANCH_LIKE.match(comment):
                branch_pins.append(f"{where} tracks branch '{comment}'")
                continue

            known = tags_for(action)
            if known is None:
                unresolved.append(f"{where} could not reach the GitHub API")
                continue

            real = known.get(sha)
            if real is None:
                unresolved.append(f"{where} says {comment} but that SHA is not at any tag")
            elif real.lstrip("v") == comment.lstrip("v"):
                ok += 1
            else:
                mismatched.append(f"{where} says {comment} but is really {real}")

    for line in branch_pins:
        print(f"  branch pin  {line}")
    for line in unresolved:
        print(f"  UNRESOLVED  {line}")
    for line in mismatched:
        print(f"  MISMATCH    {line}")

    print(
        f"\n{ok} pin(s) agree with their comment, {len(mismatched)} disagree, "
        f"{len(unresolved)} unresolved, {len(branch_pins)} track a branch"
    )

    if mismatched and not report_only:
        print(
            "\nA pin comment that disagrees with its SHA is worse than no comment: it looks "
            "like provenance and is misinformation.\nFix the COMMENT to match the SHA (the SHA "
            "is what actually runs), or change the SHA if the comment was the intent."
        )
        return 1
    # An unreachable API must not silently pass as 'all good', but must not fail a PR for a
    # network blip either - it is reported loudly and left to the reader.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
