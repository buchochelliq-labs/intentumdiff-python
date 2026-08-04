"""'What happens when I change x' across every language (issues #42/#45/#46).

Runs the tree-driven construct-edit matrix (see construct_edit_matrix.py) against every
corpus snippet, ratcheted by each corpus dir's ``edit_matrix.expect.json``:

- {"ok": true}      → the verb must keep passing (regression pin);
- {"xfail": reason} → a live documented gap; FAILS LOUDLY when it starts passing;
- {"skip": reason}  → construct not present / edit not derivable for this snippet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentumdiff import SemanticDiffer
from tests.unit.construct_edit_matrix import EDIT_VERBS, run_verb

_CORPUS_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"


def _matrix_cases() -> list[tuple[Path, dict, dict, str]]:
    cases = []
    if not _CORPUS_ROOT.is_dir():
        return cases
    for matrix_path in sorted(_CORPUS_ROOT.glob("*/edit_matrix.expect.json")):
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        manifest_path = matrix_path.parent / "playground.expect.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sources = [
            p
            for p in matrix_path.parent.glob("playground.*")
            if not p.name.endswith(".expect.json")
        ]
        if len(sources) != 1:
            continue
        for verb in EDIT_VERBS:
            cases.append((sources[0], manifest, matrix.get(verb, {}), verb))
    return cases


_CASES = _matrix_cases()
_IDS = [f"{manifest['language']}-{verb}" for _, manifest, _, verb in _CASES]


@pytest.mark.parametrize(("source_path", "manifest", "expectation", "verb"), _CASES, ids=_IDS)
def test_construct_edit(source_path: Path, manifest: dict, expectation: dict, verb: str) -> None:
    if "skip" in expectation:
        pytest.skip(expectation["skip"])
    language = manifest["language"]
    filename = manifest["filename"]
    source = source_path.read_text(encoding="utf-8")
    status, detail = run_verb(SemanticDiffer(), language, filename, source, verb)
    if status == "skip":
        pytest.skip(detail or "not derivable")
    if "xfail" in expectation:
        if status == "fail":
            pytest.xfail(f"{language} [{verb}]: {expectation['xfail']}")
        pytest.fail(
            f"{language} [{verb}] now PASSES — remove its xfail from edit_matrix.expect.json "
            f"(was: {expectation['xfail']})"
        )
    assert status == "ok", f"{language} [{verb}]: {detail}"
