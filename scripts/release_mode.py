#!/usr/bin/env python3
"""Decide which kind of release a publish run is, before it touches any bytes.

A release reaches this workflow in one of two ways, and both stay supported:

* repo         A maintainer pushes a ``v*`` tag here. Pins come from this repository's
               ``INTENTUMDIFF_*`` variables (or inputs, as before). Unchanged behaviour.
* orchestrated ``buchochelliq-labs/intentumdiff-release`` creates an *annotated* tag whose
               message carries a ``Release-Orchestrator: <run URL>`` line, then dispatches
               this workflow on that tag with every pin as an input. Variables are never
               read in this mode.

Everything else is derived from those two:

* deferred              The tag push that an orchestrated tag also fires. Nothing runs, so
                        one tag cannot publish twice (once from stale variables).
* orchestrated-dry-run  The orchestrator rehearsing: verify the pins, publish nothing.
* rehearsal             A manual dispatch (dry run or TestPyPI), as before.
* none                  Any other event (for example a pull request).

The mode is written to ``$GITHUB_OUTPUT`` as ``mode`` and explained in the job summary.
Standard library only; the GitHub API is read with the workflow's own token.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

ORCHESTRATOR = "buchochelliq-labs/intentumdiff-release"
ORCHESTRATOR_RUN = re.compile(
    r"^https://github\.com/" + re.escape(ORCHESTRATOR) + r"/actions/runs/[0-9]+(?:/attempts/[0-9]+)?$"
)
TRAILER = re.compile(r"^Release-Orchestrator:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
TAG_REF = re.compile(r"^refs/tags/v[0-9]")

MODES = ("repo", "orchestrated", "orchestrated-dry-run", "deferred", "rehearsal", "none")
PUBLISHING = {"repo", "orchestrated"}


class ModeError(Exception):
    pass


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def github_get(path: str) -> dict:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(
        f"{api}/{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
        return json.load(response)


def tag_orchestrator(repository: str, tag: str, get=github_get) -> str | None:
    """The ``Release-Orchestrator`` run URL of an annotated tag, else ``None``.

    A lightweight tag, or an annotated one without the line, is a repo release.
    """
    ref = get(f"repos/{repository}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")
    target = ref.get("object") or {}
    if target.get("type") != "tag":
        return None
    message = get(f"repos/{repository}/git/tags/{target['sha']}").get("message", "")
    found = TRAILER.findall(message)
    if not found:
        return None
    if len(found) != 1 or not ORCHESTRATOR_RUN.match(found[0]):
        raise ModeError(
            f"tag {tag} names a release orchestrator, but not exactly one run of {ORCHESTRATOR}: "
            f"{found!r}"
        )
    return found[0]


def decide(env: dict[str, str], get=github_get) -> tuple[str, list[str]]:
    """Return the mode and the lines that explain it."""
    event = env.get("EVENT_NAME", "")
    ref = env.get("REF", "")
    tag = env.get("REF_NAME", "")
    repository = env.get("REPOSITORY", "")
    requested = (env.get("ORCHESTRATOR_RUN") or "").strip()
    dry_run = truthy(env.get("DRY_RUN"))
    on_tag = bool(TAG_REF.match(ref))
    required = [name for name in (env.get("REQUIRED_INPUTS") or "").split(",") if name.strip()]

    if event == "push":
        if not on_tag:
            return "none", [f"push to {ref} is not a release tag"]
        orchestrator = tag_orchestrator(repository, tag, get)
        if orchestrator:
            return "deferred", [
                f"{tag} was created by the release orchestrator ({orchestrator}).",
                "It publishes from its own dispatch of this workflow; this tag push does nothing.",
            ]
        return "repo", [f"{tag} was pushed to this repository: a repo release, pinned by its variables."]

    if event != "workflow_dispatch":
        return "none", [f"{event or 'unknown event'} never publishes"]

    if requested:
        if not ORCHESTRATOR_RUN.match(requested):
            raise ModeError(f"orchestrator_run must be a run of {ORCHESTRATOR}, got {requested!r}")
        missing = [name for name in required if not (env.get(f"INPUT_{name.upper()}") or "").strip()]
        if missing:
            raise ModeError(
                "an orchestrated run takes every pin as an input and never falls back to "
                f"repository variables; missing: {', '.join(missing)}"
            )
        if dry_run:
            return "orchestrated-dry-run", [f"Rehearsal requested by {requested}: verify only."]
        if not on_tag:
            raise ModeError(f"an orchestrated release must run on its tag, not {ref}")
        created_by = tag_orchestrator(repository, tag, get)
        if not created_by:
            raise ModeError(
                f"{tag} was not created by the release orchestrator (no Release-Orchestrator line); "
                "a repo release publishes from its tag push instead"
            )
        return "orchestrated", [
            f"Orchestrated release of {tag}, dispatched by {requested}.",
            f"Tag created by {created_by}. Pins come from the dispatch inputs only.",
        ]

    if truthy(env.get("DISPATCH_PUBLISHES_FROM_TAG")) and on_tag and not dry_run:
        return "repo", [f"Manual dispatch on {tag} with dry_run=false: a repo release, as before."]
    return "rehearsal", ["Manual dispatch: verify (and optionally upload to a test index) only."]


def main() -> int:
    try:
        mode, reasons = decide(dict(os.environ))
    except ModeError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    assert mode in MODES
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"mode={mode}\npublishes={'true' if mode in PUBLISHING else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Release mode: `{mode}`\n\n" + "".join(f"- {r}\n" for r in reasons))
    print(f"release mode: {mode}")
    for reason in reasons:
        print(f"  {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
