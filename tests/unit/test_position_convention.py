"""Position-convention conformance over the corpus (issue #52).

Every parser must emit tree-sitter's 0-based rows. Thirteen wasm crates emitted
1-based rows: the eight tree-sitter crates the issue counted (clojure, asm,
assemblyscript, dockerfile, delphi, dart, r, squirrel — `row as u32 + 1`) plus
five hand-rolled scanners this test caught after the first flip (kotlin, plsql,
swift, tsql — `SourceLine.number = i + 1` — and wat's tokenizer starting at
`line = 1`; generic had the same pattern but its positions are served by the
native Rust text path). The construct-edit
matrix compensated with a runtime line-base heuristic and position-proximity
code compared across conventions. Issue #52 flipped them all to 0-based; this
test keeps the convention uniform by running the (retained) detection heuristic
as an ASSERTION instead of an adapter.

Languages whose parsers emit no positions at all (spans invalidated to -1 by the
harness) or whose labels are not line-locatable produce no detection hits and
are skipped — the pin is meaningful exactly where labels anchor to lines, which
includes all eight flipped crates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentdiff import SemanticDiffer

from .construct_edit_matrix import detected_line_base, find_entities

_CORPUS_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"

# The crates issue #52 flipped from 1-based: detection MUST land hits at
# base 0 for these (a vacuous pass would mean the pin quietly stopped pinning).
_FLIPPED = {
    "clojure",
    "asm",
    "assemblyscript",
    "dockerfile",
    "delphi",
    "dart",
    "r",
    "squirrel",
    "kotlin",
    "plsql",
    "swift",
    "tsql",
    "wat",
    "wast",
}


def _cases() -> list[tuple[str, str, Path]]:
    cases = []
    if not _CORPUS_ROOT.is_dir():
        return cases
    for manifest_path in sorted(_CORPUS_ROOT.glob("*/playground.expect.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_stem = manifest_path.name[: -len(".expect.json")]
        sources = [
            p
            for p in manifest_path.parent.glob(f"{source_stem}.*")
            if not p.name.endswith(".expect.json")
        ]
        if len(sources) != 1:
            continue
        cases.append((manifest["language"], manifest["filename"], sources[0]))
    return cases


_CASES = _cases()
_IDS = [language for language, _, _ in _CASES]


@pytest.mark.parametrize(("language", "filename", "source_path"), _CASES, ids=_IDS)
def test_rows_are_zero_based(language: str, filename: str, source_path: Path) -> None:
    source = source_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    constructs, _ = find_entities(SemanticDiffer(), language, filename, source)
    locatable = [c for c in constructs if c.start_line >= 0]
    if not locatable:
        pytest.skip(f"{language}: no position-bearing entities to anchor")
    base = detected_line_base(locatable, lines)
    assert base == 0, (
        f"{language} entity labels anchor at base {base}, not 0 — a parser has "
        f"drifted off the 0-based row convention (issue #52)"
    )
    if language in _FLIPPED:
        # Non-vacuous check: at least one label must actually sit on its
        # reported 0-based line.
        hits = [
            c
            for c in locatable[:5]
            if 0 <= c.start_line < len(lines)
            and (name := c.label.split("(")[0].strip().strip("#").strip())
            and name.split()[-1] in lines[c.start_line]
        ]
        assert hits, f"{language}: no label found on its reported line — pin is vacuous"
