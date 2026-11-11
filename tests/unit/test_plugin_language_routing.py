"""Every parser language is finalized by core; Python keeps no language allowlist (#338 S1).

Before slice S1, ``_differ_gate.RUST_FINALIZE_LANGUAGES`` decided which languages reached
core's ``finalize_review``. Any other parser language (dbt, a third-party plugin) got a
Python-chosen source fallback labelled ``rust_core_disabled``. These tests drive a parser
plugin that reports a language id no first-party list knows and assert the review is core's.
"""

from __future__ import annotations

from typing import Any

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff import rust_core

_UNKNOWN_LANGUAGE = "acme-thirdparty-lang"

OLD = "def greet(name):\n    return 'hi ' + name\n"
NEW = "def greet(name):\n    return 'hello ' + name\n\n\ndef added():\n    return 1\n"


class _ThirdPartyParser:
    """A plugin that claims an unknown language id; its trees come from a real parser."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.grammar_id = "acme-thirdparty"
        self.plugin_id = "acme-thirdparty"

    def process(self, source: str, language: str, filename: str, *, fuel: int) -> Any:
        assert language == _UNKNOWN_LANGUAGE
        return self._inner.process(source, "python", filename, fuel=fuel)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@pytest.fixture
def third_party_differ(monkeypatch: pytest.MonkeyPatch) -> SemanticDiffer:
    differ = SemanticDiffer()
    real_parser, _language = differ._registry.detect_parser("example.py", OLD, "python")
    fake = _ThirdPartyParser(real_parser)
    monkeypatch.setattr(
        differ._registry,
        "detect_parser",
        lambda *args, **kwargs: (fake, _UNKNOWN_LANGUAGE),
    )
    return differ


def test_unknown_plugin_language_is_finalized_by_core(
    third_party_differ: SemanticDiffer, monkeypatch: pytest.MonkeyPatch
) -> None:
    finalize_languages: list[str] = []
    original = rust_core.try_rust_finalize_review

    def recording_finalize(**kwargs: Any) -> Any:
        finalize_languages.append(kwargs["language"])
        return original(**kwargs)

    monkeypatch.setattr(rust_core, "try_rust_finalize_review", recording_finalize)

    def no_python_fallback(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Python chose a source fallback for a parsed language")

    monkeypatch.setattr(rust_core, "source_fallback_diff", no_python_fallback)

    diff = third_party_differ.diff_strings(OLD, NEW, "example.acme")

    assert finalize_languages == [_UNKNOWN_LANGUAGE]
    assert diff.language == _UNKNOWN_LANGUAGE
    assert diff.is_fallback is False
    assert "fallback_reason" not in diff.metadata
    assert diff.has_semantic_changes
    assert diff.changes


