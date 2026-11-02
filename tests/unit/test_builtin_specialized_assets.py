"""Regression: first-party parser asset lookup must not silently downgrade.

This covers host-owned path resolution only. Full public installed-wheel parsing,
including missing/corrupt Wasm behavior, remains a separate release gate.
"""

from pathlib import Path

import pytest

from intentumdiff.plugins import builtins


_SPECIALIZED = [
    ("graphql_parser_entry", "graphql_parser.wasm"),
    ("gitignore_parser_entry", "gitignore_parser.wasm"),
    ("ocaml_parser_entry", "ocaml_parser.wasm"),
    ("reasonml_parser_entry", "reasonml_parser.wasm"),
    ("latex_parser_entry", "latex_parser.wasm"),
    ("asciidoc_parser_entry", "asciidoc_parser.wasm"),
    ("po_parser_entry", "po_parser.wasm"),
]


@pytest.mark.parametrize("entry_name, asset_name", _SPECIALIZED)
@pytest.mark.parametrize("asset_present", [False, True])
def test_specialized_parser_keeps_its_own_wasm_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    entry_name: str, asset_name: str, asset_present: bool,
) -> None:
    """An installed generic component cannot impersonate a specialized parser."""
    monkeypatch.setattr(builtins, "_WASM_DIR", tmp_path)
    generic = tmp_path / "generic_parser.wasm"
    generic.write_bytes(b"generic placeholder")

    specialized = tmp_path / asset_name
    if asset_present:
        specialized.write_bytes(b"specialized placeholder")

    resolved = Path(getattr(builtins, entry_name)())
    assert resolved == specialized
    assert resolved.exists() is asset_present
    assert resolved != generic

    # Explicit generic discovery is still permitted; only implicit substitution
    # of a requested specialized parser is forbidden.
    assert Path(builtins.generic_parser_entry()) == generic
