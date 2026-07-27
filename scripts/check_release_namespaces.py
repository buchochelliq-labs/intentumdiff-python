#!/usr/bin/env python3
"""
Check public namespace/listing availability before the IntentDiff release.

This helper performs unauthenticated public checks only. It cannot reserve a
name, prove account ownership, or replace the manual Marketplace/Open VSX/PyPI
publisher setup steps documented in docs/VS_CODE_EXTENSION_RELEASE.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Iterable


DEFAULT_PRODUCT_NAME = "intentdiff"
DEFAULT_OWNER_NAMESPACE = "buchochelliq-labs"
DEFAULT_PLUGIN_REPO = "intentdiff-registry"
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class NamespaceProbe:
    id: str
    label: str
    url: str
    visible_statuses: tuple[int, ...] = ()
    available_statuses: tuple[int, ...] = (404,)
    taken_statuses: tuple[int, ...] = (200,)
    note: str = ""


@dataclass(frozen=True)
class NamespaceResult:
    id: str
    label: str
    status: str
    detail: str
    url: str
    note: str


HttpGet = Callable[[str, float], int]


def default_probes(
    name: str = DEFAULT_PRODUCT_NAME,
    *,
    owner: str = DEFAULT_OWNER_NAMESPACE,
    plugin_repo: str = DEFAULT_PLUGIN_REPO,
) -> list[NamespaceProbe]:
    github_repo_url = f"https://github.com/{owner}/{name}"
    github_plugin_repo_url = f"https://github.com/{owner}/{plugin_repo}"
    return [
        NamespaceProbe(
            id="github-owner",
            label=f"GitHub owner namespace {owner}",
            url=f"https://github.com/{owner}",
            visible_statuses=(200,),
            taken_statuses=(),
            note="Visible is expected if this is the owned Buchochelliq Labs namespace.",
        ),
        NamespaceProbe(
            id="pypi-project",
            label=f"PyPI project {name}",
            url=f"https://pypi.org/pypi/{name}/json",
            note="404 usually means the project name is not publicly registered.",
        ),
        NamespaceProbe(
            id="github-repo",
            label=f"GitHub repository {owner}/{name}",
            url=github_repo_url,
            visible_statuses=(200,),
            taken_statuses=(),
            note=(
                "A visible repository needs ownership confirmation before release. "
                "Private owned repositories may return 404 to this public check; "
                "verify with git ls-remote or an authenticated browser."
            ),
        ),
        NamespaceProbe(
            id="github-plugin-repo",
            label=f"GitHub plugin repository {owner}/{plugin_repo}",
            url=github_plugin_repo_url,
            visible_statuses=(200,),
            taken_statuses=(),
            note="Used for first-party plugin packages once public plugin publishing starts.",
        ),
        NamespaceProbe(
            id="vs-marketplace-extension",
            label=f"VS Marketplace extension {owner}.{name}",
            url=f"https://marketplace.visualstudio.com/items?itemName={owner}.{name}",
            note="This checks the extension listing, not publisher-account ownership.",
        ),
        NamespaceProbe(
            id="open-vsx-extension",
            label=f"Open VSX extension {owner}/{name}",
            url=f"https://open-vsx.org/api/{owner}/{name}",
            note="This checks the extension listing; namespace ownership still needs account confirmation.",
        ),
    ]


def http_status(url: str, timeout: float) -> int:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError(f"namespace probes must use https URLs: {url}")
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={"User-Agent": "IntentDiff release namespace check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def collect_results(
    probes: Iterable[NamespaceProbe],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    get_status: HttpGet = http_status,
) -> list[NamespaceResult]:
    results: list[NamespaceResult] = []
    for probe in probes:
        try:
            code = get_status(probe.url, timeout)
        except OSError as exc:
            results.append(NamespaceResult(
                id=probe.id,
                label=probe.label,
                status="unknown",
                detail=f"Network check failed: {exc}",
                url=probe.url,
                note=probe.note,
            ))
            continue

        if code in probe.visible_statuses:
            status = "visible"
            detail = f"HTTP {code}: namespace is publicly visible; confirm ownership manually."
        elif code in probe.taken_statuses:
            status = "taken"
            detail = f"HTTP {code}: public listing or namespace is already visible."
        elif code in probe.available_statuses:
            status = "available"
            detail = f"HTTP {code}: no public listing found."
        else:
            status = "unknown"
            detail = f"HTTP {code}: result needs manual review."

        results.append(NamespaceResult(
            id=probe.id,
            label=probe.label,
            status=status,
            detail=detail,
            url=probe.url,
            note=probe.note,
        ))
    return results


def print_results(results: Iterable[NamespaceResult]) -> None:
    for result in results:
        print(f"[{result.status.upper()}] {result.label}")
        print(f"  {result.detail}")
        print(f"  URL: {result.url}")
        if result.note:
            print(f"  Note: {result.note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_PRODUCT_NAME, help="Primary product/package name.")
    parser.add_argument(
        "--owner",
        default=DEFAULT_OWNER_NAMESPACE,
        help="Publisher/GitHub owner namespace.",
    )
    parser.add_argument(
        "--plugin-repo",
        default=DEFAULT_PLUGIN_REPO,
        help="First-party plugin repository name under the owner namespace.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Network timeout per check in seconds.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    results = collect_results(
        default_probes(args.name, owner=args.owner, plugin_repo=args.plugin_repo),
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True))
    else:
        print_results(results)

    return 1 if any(result.status in {"taken", "unknown"} for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
