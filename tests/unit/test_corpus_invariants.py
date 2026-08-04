"""Oracle-free hardening invariants over the full-language corpus (issue #45).

Every corpus snippet is checked against invariants that need NO expected-diff curation:

1. **Content visibility** — every token the manifest lists must appear in the semantic
   tree's labels. The manifests are RATCHETED: generated from what the engine could see at
   generation time, so this test pins regressions without asserting unfixed gaps (those
   live in the manifest's ``invisible_tokens_at_generation`` triage field).
2. **Mutation non-equivalence** — deterministic mutations (bump a numeric literal, edit a
   string literal, append a statement) must NEVER be style-only and NEVER zero changes.
   Known-bad languages carry ``mutation_xfail`` entries in their manifest (each one is a
   real finding of the issue #41 class) and xfail until fixed.

Corpus layout: tests/fixtures/corpus/<dir>/<name>.<ext> + <name>.expect.json manifest:
{"language", "filename", "visible_tokens", "append_statement", "mutation_xfail",
 "invisible_tokens_at_generation"}.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from intentumdiff import SemanticDiffer

_CORPUS_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"


def _corpus_cases() -> list[tuple[Path, dict]]:
    cases = []
    if not _CORPUS_ROOT.is_dir():
        return cases
    for manifest_path in sorted(_CORPUS_ROOT.glob("*/*.expect.json")):
        if manifest_path.name == "edit_matrix.expect.json":
            # The construct-edit matrix keeps its ratchet beside the snippet manifest;
            # it belongs to test_construct_edit_matrix, not this harness.
            continue
        source_stem = manifest_path.name[: -len(".expect.json")]
        sources = [
            p
            for p in manifest_path.parent.glob(f"{source_stem}.*")
            if not p.name.endswith(".expect.json")
        ]
        assert len(sources) == 1, f"exactly one source for {manifest_path}"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "language" in manifest and "filename" in manifest, manifest_path
        cases.append((sources[0], manifest))
    return cases


_CASES = _corpus_cases()
_IDS = [f"{manifest['language']}-{path.stem}" for path, manifest in _CASES]


def _semantic_labels(language: str, filename: str, source: str) -> str:
    diff = SemanticDiffer().diff_strings("", source, filename=filename, language_hint=language)
    labels: list[str] = []

    def walk(node) -> None:
        if node.label:
            labels.append(node.label)
        for child in node.children:
            walk(child)

    for change in diff.changes:
        if change.new_node is not None:
            walk(change.new_node)
    return "\n".join(labels)


@pytest.mark.parametrize(("source_path", "manifest"), _CASES, ids=_IDS)
def test_content_visibility(source_path: Path, manifest: dict) -> None:
    """Every ratcheted token stays visible in the semantic tree — the kind-drift detector."""
    if not manifest["visible_tokens"]:
        pytest.skip("no visible tokens ratcheted for this snippet")
    source = source_path.read_text(encoding="utf-8")
    haystack = _semantic_labels(manifest["language"], manifest["filename"], source)
    missing = [token for token in manifest["visible_tokens"] if token not in haystack]
    assert not missing, (
        f"{manifest['language']}/{source_path.name}: previously-visible tokens vanished from "
        f"the semantic tree (kind-drift regression): {missing}\nlabels seen:\n{haystack[:2000]}"
    )


def _mutations(source: str, manifest: dict) -> list[tuple[str, str]]:
    mutations: list[tuple[str, str]] = []
    bumped = re.sub(
        r"(?<![\w.])(\d+)(?![\w.])", lambda m: str(int(m.group(1)) + 1), source, count=1
    )
    if bumped != source:
        mutations.append(("bump_numeric_literal", bumped))
    for quote in ("'", '"'):
        pattern = re.compile(rf"{quote}([^{quote}\n]{{2,}}){quote}")
        edited = pattern.sub(lambda m: f"{quote}{m.group(1)}X{quote}", source, count=1)
        if edited != source:
            mutations.append((f"edit_{quote}_string", edited))
            break
    append = manifest.get("append_statement")
    if append:
        mutations.append(("append_statement", source.rstrip("\n") + "\n" + append + "\n"))
    return mutations


@pytest.mark.parametrize(("source_path", "manifest"), _CASES, ids=_IDS)
def test_mutation_non_equivalence(source_path: Path, manifest: dict) -> None:
    """A content mutation must never be style-only and never produce zero changes."""
    language = manifest["language"]
    filename = manifest["filename"]
    known_bad: dict = manifest.get("mutation_xfail") or {}
    source = source_path.read_text(encoding="utf-8")
    differ = SemanticDiffer()
    mutations = _mutations(source, manifest)
    if not mutations:
        pytest.skip("no applicable mutations for this snippet")
    for name, mutated in mutations:
        diff = differ.diff_strings(source, mutated, filename=filename, language_hint=language)
        broken = diff.is_style_only or not diff.changes
        if name in known_bad:
            if broken:
                pytest.xfail(f"{language} [{name}]: {known_bad[name]}")
            else:
                pytest.fail(
                    f"{language} [{name}] now PASSES — remove its mutation_xfail entry "
                    f"from {source_path.stem}.expect.json (was: {known_bad[name]})"
                )
        assert not diff.is_style_only, (
            f"{language}/{source_path.name} [{name}]: content mutation reported STYLE-ONLY "
            f"(the issue #41 disease)"
        )
        assert diff.changes, (
            f"{language}/{source_path.name} [{name}]: content mutation produced ZERO changes "
            f"(suppression-to-zero, issue #41)"
        )
