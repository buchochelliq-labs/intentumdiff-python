"""Intent-facts LLM accuracy (issue #71, Layer 2 — opt-in, real LLM, NOT in CI).

Layer 1 (``test_intent_facts_sufficiency``) proves the engine *derives* the required facts. Layer 2
proves those facts are *sufficient*: given ONLY the privacy-safe fact sheet (no source, identifiers,
or literal values), can a real LLM describe the change accurately — and, crucially, WITHOUT
inventing what the facts do not state (the #68 "performs some internal computation" failure)?

This is a **benchmark**: it is excluded from the default suite (``addopts = -m "not benchmark"``)
and does no network unless explicitly selected *and* a key is present. BYOK — it reads
``ANTHROPIC_API_KEY`` from the environment and SKIPS when absent; it never bundles a key and never
sends source. The fact sheet fed to the model is built only from the derived NodeFacts (counts /
enums / flags), so nothing sensitive leaves the machine even when run.

Run it deliberately::

    ANTHROPIC_API_KEY=sk-... pytest tests/unit/test_intent_facts_llm_accuracy.py -m benchmark -q

Grading is intentionally lenient (synonym groups), because it measures fact *sufficiency*, not
model phrasing — a fact the model cannot recover is a gap to fill in Layer 1, not a wording nit.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import pytest

from tests.unit.test_intent_facts_sufficiency import CASES, _added_entity_facts

pytestmark = pytest.mark.benchmark

_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
_MODEL = os.environ.get("INTENTDIFF_BENCH_MODEL", "claude-haiku-4-5-20251001")
_ENDPOINT = "https://api.anthropic.com/v1/messages"


@dataclass(frozen=True)
class Grade:
    """How to score a case's one-sentence intent (all matching is case-insensitive)."""

    case_name: str
    #: Each inner list is a synonym group; the description must contain AT LEAST ONE token per group.
    expect_any: list[list[str]] = field(default_factory=list)
    #: Tokens that must NOT appear — the anti-fabrication / privacy guard.
    forbid: list[str] = field(default_factory=list)


# A representative slice of the corpus (not all 26) — the facts whose sufficiency is most at risk.
GRADES: list[Grade] = [
    # The #68 case: from facts alone the model must say it returns a constant integer AND must NOT
    # invent computation (has_computation=False is in the sheet).
    Grade(
        case_name="python-const-return-with-print",
        expect_any=[["integer", "int", "number", "numeric"], ["constant", "fixed", "literal"]],
        forbid=["comput", "calculat", "algorithm"],
    ),
    Grade(case_name="python-recursive", expect_any=[["recursi", "itself"]]),
    Grade(case_name="python-validator", expect_any=[["validat", "check", "raise", "guard", "reject"]]),
    Grade(case_name="python-accessor", expect_any=[["return", "read", "get", "access", "value"]]),
    Grade(case_name="python-enum-class", expect_any=[["enum", "enumeration"]]),
    Grade(case_name="python-exception-class", expect_any=[["exception", "error"]]),
    Grade(case_name="python-dataclass", expect_any=[["dataclass", "data class", "data-class"]]),
    Grade(case_name="python-property", expect_any=[["property", "getter", "read-only", "read only"]]),
]

_CASE_BY_NAME = {c.name: c for c in CASES}


def _fact_sheet(facts: dict) -> str:
    """A privacy-safe, human-readable fact sheet from the derived NodeFacts. Only the facts that
    are set (counts / enums / flags) — never a name or literal value (they are not in NodeFacts)."""
    lines = [f"- {key}: {json.dumps(value)}" for key, value in sorted(facts.items()) if value is not None]
    return "\n".join(lines)


def _ask_llm(sheet: str) -> str:
    prompt = (
        "You are given a STRUCTURED SUMMARY of a code change (NOT the source code). It lists only "
        "privacy-safe structural facts (counts, enums, flags). In ONE sentence, describe what the "
        "change adds or does, using ONLY the facts below. Do NOT invent computation, purpose, or "
        "behavior that the facts do not state; if the facts say a body performs no computation, do "
        "not claim it computes anything.\n\nFacts:\n" + sheet
    )
    body = json.dumps(
        {"model": _MODEL, "max_tokens": 120, "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    req = urllib.request.Request(
        _ENDPOINT,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": _API_KEY or "",
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed, trusted endpoint)
        payload = json.loads(resp.read())
    blocks = payload.get("content") or []
    return " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


@pytest.mark.skipif(not _API_KEY, reason="BYOK: set ANTHROPIC_API_KEY to run the Layer-2 LLM grade")
@pytest.mark.parametrize("grade", GRADES, ids=lambda g: g.case_name)
def test_llm_builds_accurate_intent_from_facts(grade: Grade) -> None:
    case = _CASE_BY_NAME[grade.case_name]
    facts = _added_entity_facts(case)
    assert facts is not None, f"{grade.case_name}: no facts derived (Layer 1 gap)"

    sheet = _fact_sheet(facts.model_dump())
    try:
        description = _ask_llm(sheet).lower()
    except urllib.error.URLError as exc:  # network/key problem — a benchmark, so skip not fail.
        pytest.skip(f"LLM call failed ({exc}); benchmark needs a working key + network")

    assert description, f"{grade.case_name}: empty LLM response"
    for group in grade.expect_any:
        assert any(token in description for token in group), (
            f"{grade.case_name}: description misses all of {group}. "
            f"The facts were insufficient for the model to state it.\nFacts:\n{sheet}\n"
            f"Description: {description!r}"
        )
    for banned in grade.forbid:
        assert banned not in description, (
            f"{grade.case_name}: description contains forbidden {banned!r} (fabrication/leak).\n"
            f"Facts:\n{sheet}\nDescription: {description!r}"
        )
