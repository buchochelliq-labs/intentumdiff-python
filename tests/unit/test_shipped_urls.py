"""Offline invariants on the URLs we ship in source.

`scripts/check_source_urls.py` is the real gate — it resolves every URL and fails on a 404.
It needs the network, so it runs as its own CI job rather than here; unit tests in this repo
do no network.

What can be asserted offline is that URLs already known to be dead never come back. That is
worth doing separately because these have each rotted at least once:

  - `docs.intentumdiff.dev` shipped in 0.0.1 in an error message. The domain was never
    registered. It was one of the four defects that got 0.0.1 pulled from three registries.
  - `.../blob/main/docs/PLUGIN_GUIDE.md` replaced it, and was dead too — the file does not
    exist on `main`. It survived because nothing followed a URL that only appears in an error
    for a third-party plugin, which no test exercises.

Both were introduced as *fixes*. A dead link is not a typo you make once; it is what happens
when a message names a resource nobody verifies.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

URL = re.compile(r"https?://[^\s\"'`<>)\]}]+")

# Each entry is a URL, or a distinctive fragment of one, that has been shipped and was dead.
KNOWN_DEAD = {
    "docs.intentumdiff.dev": "never a registered domain; shipped in 0.0.1 error messages",
    "PLUGIN_GUIDE.md": "not present on main in any public repo; 404s for every user",
}


def _shipped_urls() -> list[tuple[str, str]]:
    """(url, 'file:line') for every URL in shipped source."""
    out: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC.parent).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for url in URL.findall(line):
                out.append((url, f"{rel}:{lineno}"))
    return out


def test_there_are_urls_to_check() -> None:
    """Guard the guard.

    Every assertion below passes vacuously on an empty list, so a moved `src/` or a regex that
    stopped matching would turn this module green while checking nothing.
    """
    assert SRC.is_dir(), f"{SRC} does not exist"
    assert len(_shipped_urls()) >= 20, f"expected many URLs in source, found {len(_shipped_urls())}"


def test_no_known_dead_url_is_ever_reintroduced() -> None:
    offenders = [
        f"{where} -> {url}\n      ({reason})"
        for url, where in _shipped_urls()
        for dead, reason in KNOWN_DEAD.items()
        if dead in url
    ]
    assert not offenders, (
        "a URL known to be dead has come back:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse a URL that resolves. scripts/check_source_urls.py checks them for real."
    )


def test_the_plugin_metadata_error_names_a_documentation_url() -> None:
    """The one error most likely to be read by someone who cannot fix it themselves.

    A third-party plugin author hitting this needs somewhere to go. That has been true through
    two dead links, so it is asserted rather than assumed.
    """
    registry = (SRC / "intentumdiff" / "plugins" / "registry.py").read_text(encoding="utf-8")

    assert "IntentumDiff-Wasm-Path" in registry, "the metadata field name should appear in the error"

    urls = [u for u in URL.findall(registry) if "intentumdiff-docs" in u]
    assert urls, (
        "the plugin metadata error must point at the documentation site. "
        "Without a link the reader is told what is wrong and given no way to fix it."
    )
