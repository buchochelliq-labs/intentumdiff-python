"""Assert every URL we ship in source actually resolves.

WHY THIS EXISTS
---------------
0.0.1 shipped an error message pointing at `docs.intentumdiff.dev`, a domain that was never
registered. Nothing caught it, because nothing followed a documented URL.

`smoke_published_wheel.py` closed part of that: it extracts URLs from the CLI's stderr and
checks them. But it can only see URLs that a *successful run happens to print*. The plugin
metadata error fires only for a third-party plugin missing a metadata field, so its URL never
reached stderr and was never checked - and it rotted. On 2026-08-10 it pointed at
`.../blob/main/docs/PLUGIN_GUIDE.md`, a file that does not exist on `main`, in a public repo,
so it 404'd for every user who followed it.

A link in an error message is a promise. This checks the promises we cannot trigger on demand.

Placeholders are skipped rather than failed - `https://github.com/OWNER/REPO/pull/NUMBER` is
documentation of a shape, not a link, and there is no sensible way to resolve it.

Usage:
    python scripts/check_source_urls.py            # exits 1 on any dead URL
    python scripts/check_source_urls.py --list     # report only, always exits 0
"""

from __future__ import annotations

import concurrent.futures
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path("src")

URL = re.compile(r"https?://[^\s\"'`<>)\]}]+")

# Trailing punctuation that belongs to the prose, not the URL.
TRAILING = ".,;:`…"

# A URL containing any of these is a template or a local address, not a live endpoint.
PLACEHOLDER = (
    "OWNER", "REPO", "NUMBER", "GIST_ID", "myorg", "{", "}", "…",
    "HOST", "PORT", "localhost", "127.0.0.1", "0.0.0.0",
)

# Endpoints that exist but must never be contacted by a lint. api.osv.dev is a live query API,
# not a page; hammering it from CI is rude and tells us nothing about link rot.
NO_CONTACT = ("api.osv.dev",)

# Bases that are only ever f-string'd into a real URL. The prefix alone 404s and that means
# nothing - what matters is the composed URL, which this checker cannot assemble. Verified by
# hand instead, with the date, so a future reader does not "fix" a working endpoint.
ASSEMBLED_ONLY = {
    # {BASE}/dbt_cloud-latest.json -> HTTP 200 (checked 2026-08-10)
    "https://raw.githubusercontent.com/dbt-labs/dbt-jsonschema/main/schemas/latest",
}

# Genuinely dead, genuinely ours to fix, and NOT fixable by editing a string. These are
# fetched at runtime for schema-aware analysis, so the right replacement is a behavioural
# decision rather than a typo correction - `kubernetes-definitions.json` resolves but is a
# different schema, and no working azure-pipelines URL was found.
#
# Listed here so the gate stays useful for the other 20 URLs instead of being switched off,
# and every entry MUST carry a tracking issue. An entry without one is how a known-broken
# list becomes a place bugs go to be forgotten.
KNOWN_BROKEN = {
    "https://json.schemastore.org/kubernetes.json": "intentumdiff-python#38",
    "https://json.schemastore.org/azure-pipelines.json": "intentumdiff-python#38",
}


def collect() -> dict[str, list[str]]:
    """url -> ['file:line', ...]"""
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for raw in URL.findall(line):
                url = raw.rstrip(TRAILING)
                if not url:
                    continue
                found.setdefault(url, []).append(f"{path.as_posix()}:{lineno}")
    return found


def check(url: str) -> tuple[str, str]:
    """(status, detail). status is 'ok', 'dead' or 'unknown'."""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(
            url,
            method=method,
            # Some hosts (GitHub Pages among them) answer a bare urllib UA with 403.
            headers={"User-Agent": "Mozilla/5.0 (compatible; intentumdiff-link-check)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return ("ok", str(resp.status))
        except urllib.error.HTTPError as exc:
            # 404/410 is rot. 403/405/429 usually means the host dislikes the request, not
            # that the page is missing - retry with GET, then report as unknown rather than
            # failing a build on someone else's bot policy.
            if exc.code in (404, 410):
                return ("dead", f"HTTP {exc.code}")
            if method == "GET":
                return ("unknown", f"HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001 - network, DNS, TLS all mean "cannot verify"
            if method == "GET":
                return ("unknown", type(exc).__name__)
    return ("unknown", "no response")


def main() -> int:
    report_only = "--list" in sys.argv

    if not SRC.is_dir():
        print(f"no {SRC}/ directory - run from the repository root")
        return 1

    found = collect()
    skipped = {
        u: w
        for u, w in found.items()
        if any(p in u for p in PLACEHOLDER)
        or any(n in u for n in NO_CONTACT)
        or u in ASSEMBLED_ONLY
    }
    live = {u: w for u, w in found.items() if u not in skipped}

    print(f"{len(found)} distinct URL(s) in {SRC}/: checking {len(live)}, skipping {len(skipped)} placeholder/no-contact\n")

    dead: list[str] = []
    unknown: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for url, (status, detail) in zip(live, pool.map(check, live)):
            where = ", ".join(live[url][:2])
            if status == "dead":
                dead.append(f"{url}\n      {detail} - referenced at {where}")
            elif status == "unknown":
                unknown.append(f"{url}  ({detail}) - {where}")

    # A known-broken URL that started working is worth knowing about: it means the entry, and
    # probably its issue, can be closed. Reported, never failed.
    revived = [u for u in KNOWN_BROKEN if u in live and all(u not in d for d in dead)]

    for line in unknown:
        print(f"  UNVERIFIED  {line}")
    for url in revived:
        print(f"  REVIVED     {url} now resolves - remove it from KNOWN_BROKEN ({KNOWN_BROKEN[url]})")
    for line in dead:
        print(f"  {'KNOWN-BROKEN' if any(k in line for k in KNOWN_BROKEN) else 'DEAD        '}  {line}")

    unexpected = [d for d in dead if not any(k in d for k in KNOWN_BROKEN)]
    print(
        f"\n{len(live) - len(dead) - len(unknown)} resolved, {len(unexpected)} dead, "
        f"{len(dead) - len(unexpected)} known-broken, {len(unknown)} unverified"
    )

    dead = unexpected
    if dead and not report_only:
        print(
            "\nA URL in a shipped message is a promise to the user. A dead one is worse than "
            "no link:\nit implies help exists and wastes the reader's time proving it does not."
        )
        return 1
    # Unverified never fails the build - a bot policy or a network blip is not link rot, and a
    # lint that fails on someone else's rate limiter gets disabled within a week.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
