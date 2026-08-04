"""#94 item 1 — the plugin contract must be independently extractable.

Every Wasm crate reads the WIT contract via ``wit_bindgen::generate!{ path: ... }``. Today
those paths reach CROSS-TREE into the Python package (``../../../src/intentdiff/plugins/wit/
plugin.wit``), which becomes a broken cross-*repo* dependency the moment the crates or the
Python binding move (#82). The DoD is **zero** such references (each crate vendors the contract
or consumes it via wit-deps). Until the mass migration lands (see
``docs/WIT_CONTRACT_MIGRATION.md``), this is a RATCHET: the count may only shrink, never grow —
so a new parser generated from the template cannot silently add more debt.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "crates" / "parsers").exists(),
    reason="monorepo crates tree not present (#82 split python repo)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Cross-tree = a `path:` that walks up (`../`) into `src/intentdiff/plugins/wit/`.
_CROSS_TREE_WIT_RE = re.compile(
    r'path:\s*"(?:\.\./)+src/intentdiff/plugins/wit/plugin\.wit"'
)

# The migration landed (#94 item 1): every crate now reads a LOCAL `wit/plugin.wit` synced by
# scripts/sync_wit.py, so the cross-tree count is ZERO and must stay there — a new parser
# generated from the template inherits the local convention, never the cross-tree path.
_CROSS_TREE_BASELINE = 0


def _rust_sources() -> list[Path]:
    roots = [REPO_ROOT / "crates", REPO_ROOT / "plugins"]
    files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.rs"):
            if "target" in path.parts:
                continue
            files.append(path)
    return files


def _cross_tree_files() -> list[Path]:
    return [
        path
        for path in _rust_sources()
        if _CROSS_TREE_WIT_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    ]


def test_no_cross_tree_wit_paths() -> None:
    offenders = _cross_tree_files()
    assert len(offenders) == _CROSS_TREE_BASELINE, (
        f"cross-tree WIT contract references must be {_CROSS_TREE_BASELINE}, found "
        f"{len(offenders)}. A Wasm crate must NOT reach into src/intentdiff/plugins/wit/ — "
        'declare `wit_bindgen::generate!{ path: "wit/plugin.wit" }` and let '
        "scripts/sync_wit.py vendor the contract (see docs/WIT_CONTRACT_MIGRATION.md). "
        "Offenders:\n  "
        + "\n  ".join(sorted(p.relative_to(REPO_ROOT).as_posix() for p in offenders))
    )


def test_canonical_wit_is_the_single_source() -> None:
    canonical = REPO_ROOT / "src" / "intentumdiff" / "plugins" / "wit" / "plugin.wit"
    assert canonical.is_file(), "the canonical plugin contract WIT is missing"
    # Its package version is the ABI contract the host gates on (#94 items 2/3).
    text = canonical.read_text(encoding="utf-8")
    assert re.search(r"package\s+intentdiff:plugin@[0-9]+\.[0-9]+\.[0-9]+", text), (
        "the canonical WIT must declare an explicit, versioned package"
    )


def test_parser_template_uses_the_local_wit_convention() -> None:
    # The lightweight-full-parse template propagates its `path:` into every generated parser,
    # so it is the growth lever: it must use the LOCAL convention, never the cross-tree path,
    # or new parsers would reintroduce the debt.
    template = REPO_ROOT / "crates" / "parsers" / "lightweight-full-parse-template.rs"
    if not template.is_file():
        return  # template relocated/renamed — nothing to assert
    text = template.read_text(encoding="utf-8")
    assert not _CROSS_TREE_WIT_RE.search(text), "the parser template must not reach cross-tree"
    assert 'path: "wit/plugin.wit"' in text, "the parser template must use path: \"wit/plugin.wit\""


def test_sync_wit_produces_matching_local_copies() -> None:
    # scripts/sync_wit.py must vendor the canonical contract into every consuming crate; after
    # a sync, its own drift check passes. The copies are gitignored build inputs, so this test
    # runs the sync (idempotent) then verifies — it does not assume they pre-exist.
    import importlib.util

    module_path = REPO_ROOT / "scripts" / "sync_wit.py"
    spec = importlib.util.spec_from_file_location("sync_wit", module_path)
    assert spec and spec.loader
    sync_wit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync_wit)

    assert sync_wit.sync(check=False) == 0
    assert sync_wit.sync(check=True) == 0  # every consumer's copy now matches the canonical
