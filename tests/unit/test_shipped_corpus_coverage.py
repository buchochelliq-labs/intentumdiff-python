"""Keep the 0.0.2 source-judged corpus aligned with every shipped parser language.

A packaging smoke count alone misses an absent corpus fixture (gitignore was
missing even though its Wasm parser was shipped). This checks the actual pinned
parser manifest, rather than a separately maintained language list.
"""

from __future__ import annotations

import json
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_CORPUS = _ROOT / "tests" / "fixtures" / "corpus"
_MANIFEST = _ROOT / "src" / "intentumdiff" / "wasm" / "parser_manifest.json"


def test_each_shipped_parser_language_has_a_source_corpus_fixture() -> None:
    parser_manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    shipped = set(parser_manifest["parsers"])
    fixtures = {
        path.name
        for path in _CORPUS.iterdir()
        if path.is_dir() and (path / "playground.expect.json").is_file()
    }
    # 0.0.2's explicitly accepted language surface is 74, including gitignore.
    assert len(shipped) == 74, f"0.0.2 manifest language count drifted: {len(shipped)}"
    assert fixtures == shipped, (
        f"missing source corpus: {sorted(shipped - fixtures)}; "
        f"unshipped corpus: {sorted(fixtures - shipped)}"
    )
    for language in sorted(shipped):
        fixture = _CORPUS / language
        content = json.loads((fixture / "playground.expect.json").read_text(encoding="utf-8"))
        assert content["language"] == language, language
        assert content["filename"], language
        matrix_path = fixture / "edit_matrix.expect.json"
        assert matrix_path.is_file(), f"{language}: construct-edit matrix missing"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        expected_verbs = {
            "delete_entity", "rename_entity", "duplicate_entity",
            "modify_literal", "swap_siblings",
        }
        assert set(matrix) == expected_verbs, (
            f"{language}: unratcheted construct-edit verbs: "
            f"missing={sorted(expected_verbs - set(matrix))}; "
            f"unexpected={sorted(set(matrix) - expected_verbs)}"
        )
        assert any(
            path.is_file()
            for path in fixture.glob("playground.*")
            if path.name != "playground.expect.json"
        ), f"{language}: source snippet missing"
