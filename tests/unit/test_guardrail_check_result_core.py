"""The guardrail check verdict is core's ``guardrail_check_result`` (#296).

``build_guardrail_check_result`` and ``guardrail_result_from_diffs`` only marshal the
evidence to core and validate the DTO that comes back. These tests prove:

- passthrough: a canned core verdict surfaces unchanged, even one Python would never compute;
- one op: each builder issues exactly one ``guardrail_check_result`` call and nothing else;
- the real core gate: ``passed = !strict || no violations`` (strict now fails on any
  violation, not only an immutable one);
- core errors and malformed core results raise instead of becoming a pass.
"""

from __future__ import annotations

import ctypes
import json
from typing import Any

import pytest

from intentumdiff import rust_core
from intentumdiff.analysis.guardrail_reports import (
    build_guardrail_check_result,
    guardrail_result_from_diffs,
)
from intentumdiff.core.models import (
    GuardrailCheckResult,
    GuardrailSeverity,
    GuardrailViolation,
    SemanticDiff,
)


def _violation(severity: GuardrailSeverity, rule_id: str = "r") -> GuardrailViolation:
    return GuardrailViolation(
        rule_id=rule_id,
        severity=severity,
        file="config.json",
        language="json",
        semantic_path="secret",
        node_type="pair",
        old_value="1",
        new_value="2",
        message="m",
    )


IMMUTABLE = _violation(GuardrailSeverity.IMMUTABLE, "imm")
IMPORTANT = _violation(GuardrailSeverity.IMPORTANT, "imp")


def _diff(*violations: GuardrailViolation) -> SemanticDiff:
    return SemanticDiff(
        old_filename="config.json",
        new_filename="config.json",
        language="json",
        guardrail_violations=list(violations),
    )


class _CannedLib:
    """Stands in for the cdylib: records each op and its request, answers one canned envelope."""

    def __init__(self, envelope: dict[str, Any]) -> None:
        self.envelope = envelope
        self.calls: list[tuple[str, list[Any]]] = []
        self._buffers: list[Any] = []

    def intentumdiff_call(self, name: bytes, payload: bytes) -> int:
        self.calls.append((name.decode("utf-8"), json.loads(payload.decode("utf-8"))))
        buffer = ctypes.create_string_buffer(json.dumps(self.envelope).encode("utf-8"))
        self._buffers.append(buffer)
        return ctypes.addressof(buffer)

    def intentumdiff_free(self, _ptr: int) -> None:
        return None


@pytest.fixture
def canned(monkeypatch: pytest.MonkeyPatch):
    def install(envelope: dict[str, Any]) -> _CannedLib:
        lib = _CannedLib(envelope)
        monkeypatch.setattr(rust_core, "_c_abi_lib", lambda: lib)
        return lib

    return install


# ---------------------------------------------------------------------------
# passthrough over a fake backend
# ---------------------------------------------------------------------------

# A verdict no Python rule would produce: advisory, yet failed. It must survive unchanged.
_CANNED_RESULT = {
    "violations": [IMPORTANT.model_dump(mode="json")],
    "violation_count": 1,
    "immutable_count": 0,
    "important_count": 1,
    "checked_files": 7,
    "strict": False,
    "passed": False,
    "metadata": {"core": "authored"},
}


def test_build_result_passes_the_canned_core_verdict_through_unchanged(canned) -> None:
    lib = canned({"ok": True, "result": _CANNED_RESULT})

    result = build_guardrail_check_result([IMMUTABLE], checked_files=1, strict=True)

    assert result.model_dump(mode="json") == GuardrailCheckResult.model_validate(
        _CANNED_RESULT
    ).model_dump(mode="json")
    assert result.passed is False and result.strict is False and result.checked_files == 7
    assert [name for name, _ in lib.calls] == ["guardrail_check_result"]
    (request,) = lib.calls[0][1]
    assert json.loads(request) == {
        "violations": [IMMUTABLE.model_dump(mode="json")],
        "strict": True,
        "checked_files": 1,
        "metadata": None,
    }


def test_result_from_diffs_sends_each_diffs_violations_in_one_op(canned) -> None:
    lib = canned({"ok": True, "result": _CANNED_RESULT})

    result = guardrail_result_from_diffs(
        [_diff(IMMUTABLE), _diff()], strict=False, metadata={"source": "unit"}
    )

    assert result.metadata == {"core": "authored"}
    assert [name for name, _ in lib.calls] == ["guardrail_check_result"]
    (request,) = lib.calls[0][1]
    assert json.loads(request) == {
        "diffs": [
            {"guardrail_violations": [IMMUTABLE.model_dump(mode="json")]},
            {"guardrail_violations": []},
        ],
        "strict": False,
        "checked_files": None,
        "metadata": {"source": "unit"},
    }


def test_canned_core_error_raises_and_is_not_a_pass(canned) -> None:
    canned({"ok": False, "error_type": "value_error", "error": "canned guardrail failure"})
    with pytest.raises(ValueError, match="canned guardrail failure"):
        build_guardrail_check_result([], checked_files=0, strict=True)


def test_inconsistent_core_counts_are_rejected_by_the_dto(canned) -> None:
    canned({"ok": True, "result": {**_CANNED_RESULT, "immutable_count": 1}})
    with pytest.raises(ValueError, match="disagree"):
        build_guardrail_check_result([IMPORTANT], checked_files=1, strict=False)


# ---------------------------------------------------------------------------
# real core
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("violations", "strict", "passed"),
    [
        ([], True, True),
        ([IMMUTABLE], True, False),
        # Behaviour change in 0.0.3: an important-only violation fails a strict check too.
        ([IMPORTANT], True, False),
        ([IMMUTABLE, IMPORTANT], False, True),
        ([IMPORTANT], False, True),
    ],
)
def test_real_core_gate(violations, strict, passed) -> None:
    result = build_guardrail_check_result(violations, checked_files=3, strict=strict)
    assert result.passed is passed
    assert result.strict is strict
    assert result.checked_files == 3
    assert result.violation_count == len(violations)
    assert result.violations == violations


def test_real_core_counts_diffs_as_checked_files() -> None:
    result = guardrail_result_from_diffs(
        [_diff(IMPORTANT), _diff(), _diff(IMMUTABLE)], strict=True
    )
    assert result.checked_files == 3
    assert (result.immutable_count, result.important_count) == (1, 1)
    assert result.violations == [IMPORTANT, IMMUTABLE]
    assert result.passed is False
    assert result.metadata == {}


def test_real_core_single_diff_and_explicit_checked_files() -> None:
    result = guardrail_result_from_diffs(_diff(), strict=True, checked_files=9)
    assert result.checked_files == 9
    assert result.passed is True


def test_real_core_rejects_a_non_boolean_strict() -> None:
    with pytest.raises(ValueError, match="strict"):
        build_guardrail_check_result([], checked_files=0, strict=1)  # type: ignore[arg-type]
