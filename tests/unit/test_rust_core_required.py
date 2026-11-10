"""The Rust core is mandatory: no environment value or config field can switch it off (#338 S2).

Before slice S2, ``INTENTUMDIFF_RUST_CORE`` values outside a fixed "on" set (``0``, a typo,
anything) silently set ``experimental_rust_core=False`` and every file got a Python-chosen
source fallback. A disable request or an unrecognised value is now a clear error.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from intentumdiff import DiffConfig, SemanticDiffer
from intentumdiff import rust_core
from intentumdiff.core.models import RustCoreRequiredError

_ENV_VARS = ("INTENTUMDIFF_RUST_CORE", "INTENTUMDIFF_EXPERIMENTAL_RUST_CORE")
_ACCEPTED = [None, "", "  ", "1", "true", "TRUE", "yes", "on", "auto", "native"]
_REJECTED = ["0", "false", "no", "off", "OFF", "disabled", "python", "2", "ture"]

OLD = "def f():\n    return 1\n"
NEW = "def f():\n    return 2\n"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", _ENV_VARS)
@pytest.mark.parametrize("value", _ACCEPTED)
def test_on_or_unset_values_use_the_core(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv(name, value)

    def no_python_fallback(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Python chose a source fallback")

    monkeypatch.setattr(rust_core, "source_fallback_diff", no_python_fallback)
    assert DiffConfig().experimental_rust_core is True
    diff = SemanticDiffer().diff_strings(OLD, NEW, "example.py")
    assert diff.is_fallback is False
    assert "fallback_reason" not in diff.metadata
    assert diff.has_semantic_changes


@pytest.mark.parametrize("name", _ENV_VARS)
@pytest.mark.parametrize("value", _REJECTED)
def test_disable_or_unknown_values_raise(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RustCoreRequiredError, match=rf"{name}=.*Rust core is mandatory"):
        DiffConfig()
    with pytest.raises(RustCoreRequiredError):
        SemanticDiffer()


def test_rejected_value_in_either_variable_raises_even_when_the_other_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTENTUMDIFF_RUST_CORE", "1")
    monkeypatch.setenv("INTENTUMDIFF_EXPERIMENTAL_RUST_CORE", "0")
    with pytest.raises(RustCoreRequiredError, match="INTENTUMDIFF_EXPERIMENTAL_RUST_CORE"):
        DiffConfig()


def test_config_field_cannot_disable_the_core() -> None:
    with pytest.raises(ValidationError, match="Rust core is mandatory"):
        DiffConfig(experimental_rust_core=False)
    assert DiffConfig(experimental_rust_core=True).experimental_rust_core is True


