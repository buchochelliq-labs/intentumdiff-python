"""Intent-facts sufficiency (issue #71, Layer 1 — deterministic, no network).

Proves the engine derives a "good enough" set of privacy-safe NodeFacts for the intent
explainer, ACROSS LANGUAGES (issue #70), so an LLM can describe a change accurately without the
code. Each case declares the facts the engine MUST derive for a changed entity; the test asserts
``required_facts`` is a subset of what the engine actually emits.

This is the acceptance gate for the fact-model work (#69): a change whose intent needs a fact we
do not derive fails here and drives the enrichment. The paired fact-sheet/claim assertions live in
the extension tests (``plugins/vscode/test``); the opt-in real-LLM accuracy grading is Layer 2
(``benchmark``-marked, not in CI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from intentumdiff.differ import SemanticDiffer


@dataclass(frozen=True)
class FactsCase:
    name: str
    language: str
    filename: str
    before: str
    after: str
    #: node_type of the added entity to inspect (e.g. "function_definition").
    entity_type: str
    #: facts the derived NodeFacts MUST contain (subset match).
    required_facts: dict[str, Any] = field(default_factory=dict)


# Seed corpus: the #68 `ccc` stub — prints (a side effect) and returns a constant integer — in
# several languages. Each must yield returns="literal" + return_kind (numeric) + side_effects=True,
# so the explainer says "returns a constant <number>; has a side effect" instead of inventing
# computation. This is the cross-language (#70) driver.
CASES: list[FactsCase] = [
    FactsCase(
        name="python-const-return-with-print",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after='x = 1\ndef ccc():\n    print("Boo!")\n    return 99\n',
        entity_type="function_definition",
        # has_computation=False is the #68 antidote: a substantive body (print + return) that
        # computes nothing, so the explainer cannot invent "performs some internal computation".
        required_facts={
            "returns": "literal",
            "return_kind": "int",
            "side_effects": True,
            "has_computation": False,
        },
    ),
    FactsCase(
        name="javascript-const-return-with-call",
        language="javascript",
        filename="m.js",
        before="const x = 1;\n",
        after='const x = 1;\nfunction ccc() {\n  console.log("Boo");\n  return 99;\n}\n',
        entity_type="function_declaration",
        # JS/TS have a single `number` node type (int/float indistinguishable at the CST).
        # Cross-language #68 antidote: derive_node_facts emits has_computation=False here too.
        required_facts={
            "returns": "literal",
            "return_kind": "number",
            "side_effects": True,
            "has_computation": False,
        },
    ),
    FactsCase(
        name="typescript-const-return-with-call",
        language="typescript",
        filename="m.ts",
        before="const x = 1;\n",
        after='const x = 1;\nfunction ccc(): number {\n  console.log("Boo");\n  return 99;\n}\n',
        entity_type="function_declaration",
        required_facts={"returns": "literal", "return_kind": "number", "side_effects": True},
    ),
    FactsCase(
        name="go-const-return-with-call",
        language="go",
        filename="m.go",
        before="package m\n\nvar x = 1\n",
        after='package m\n\nvar x = 1\n\nfunc Ccc() int {\n\tfmt.Println("Boo")\n\treturn 99\n}\n',
        entity_type="function_declaration",
        required_facts={"returns": "literal", "return_kind": "int", "side_effects": True},
    ),
    FactsCase(
        name="rust-const-return-with-macro",
        language="rust",
        filename="m.rs",
        before="fn x() {}\n",
        after='fn x() {}\nfn ccc() -> i32 {\n    println!("Boo");\n    return 99;\n}\n',
        entity_type="function_item",
        required_facts={"returns": "literal", "return_kind": "int", "side_effects": True},
    ),
    FactsCase(
        # The java parser retains return-VALUE literal nodes since #72 (tree-sitter-java's
        # kind is decimal_integer_literal, not integer_literal), so return_kind derives the
        # same way it does for python/js/go/rust.
        name="java-return-with-call",
        language="java",
        filename="M.java",
        before="class M {}\n",
        after='class M {\n  int ccc() {\n    System.out.println("Boo");\n    return 99;\n  }\n}\n',
        entity_type="method_declaration",
        required_facts={"returns": "literal", "return_kind": "int", "side_effects": True},
    ),
    # Behavior classification (#69-H): the explainer must know the body's control-flow shape.
    FactsCase(
        name="python-loop-and-branch",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after=(
            "x = 1\n"
            "def scan(items):\n"
            "    for i in items:\n"
            "        if i > 0:\n"
            "            return i\n"
            "    return None\n"
        ),
        entity_type="function_definition",
        required_facts={"control_shape": "looping", "has_loop": True, "has_conditional": True},
    ),
    FactsCase(
        name="python-error-handling",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after=(
            "x = 1\n"
            "def load():\n"
            "    try:\n"
            "        return read()\n"
            "    except IOError:\n"
            "        return None\n"
        ),
        entity_type="function_definition",
        required_facts={"has_error_handling": True},
    ),
    FactsCase(
        # Cross-language behavior parity (#70 + #69-H): JS loop+branch yields the same shape.
        name="javascript-loop-and-branch",
        language="javascript",
        filename="m.js",
        before="const x = 1;\n",
        after=(
            "const x = 1;\n"
            "function scan(a) {\n"
            "  for (const i of a) { if (i > 0) return i; }\n"
            "  return 0;\n"
            "}\n"
        ),
        entity_type="function_declaration",
        required_facts={"control_shape": "looping", "has_loop": True, "has_conditional": True},
    ),
    # behavior_category rollup (#69-H): the single "purpose" enum the explainer leads with.
    FactsCase(
        name="python-validator",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\ndef check(v):\n    if v < 0:\n        raise ValueError()\n    return v\n",
        entity_type="function_definition",
        required_facts={"behavior_category": "validator", "throws": True},
    ),
    FactsCase(
        name="python-accessor",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\ndef get(self):\n    return self.x\n",
        entity_type="function_definition",
        required_facts={"behavior_category": "accessor"},
    ),
    FactsCase(
        # The other side of the #68 antidote: a body that DOES compute (a binary operator) yields
        # has_computation=True — proving the flag is a real discriminator, not always-false.
        name="python-computation",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\ndef add(a, b):\n    return a + b\n",
        entity_type="function_definition",
        required_facts={"has_computation": True},
    ),
    FactsCase(
        # Cross-language purpose parity: JS validator classifies the same as Python's.
        name="javascript-validator",
        language="javascript",
        filename="m.js",
        before="const x = 1;\n",
        after="const x = 1;\nfunction check(v) { if (v < 0) { throw new Error(); } return v; }\n",
        entity_type="function_declaration",
        required_facts={"behavior_category": "validator", "throws": True},
    ),
    # mutator / factory rollup (#69-H): the two remaining purpose enums. A setter mutates state
    # and returns nothing; a factory builds and returns a fresh collection/object.
    FactsCase(
        name="python-mutator",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\ndef set_x(self, v):\n    self.x = v\n",
        entity_type="function_definition",
        required_facts={"behavior_category": "mutator", "mutates": True},
    ),
    FactsCase(
        name="python-factory",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after='x = 1\ndef make():\n    return {"a": 1}\n',
        entity_type="function_definition",
        required_facts={"behavior_category": "factory", "constructs": True},
    ),
    FactsCase(
        # Cross-language purpose parity (#70): a JS factory returning an object literal classifies
        # the same as Python's — proves `constructs` is derived over the language-agnostic tree.
        name="javascript-factory",
        language="javascript",
        filename="m.js",
        before="const x = 1;\n",
        after="const x = 1;\nfunction make() {\n  return { a: 1 };\n}\n",
        entity_type="function_declaration",
        required_facts={"behavior_category": "factory", "constructs": True},
    ),
    # Class facts (#69 catalog D): shape (method/field/base counts) + kind (enum/exception),
    # so the explainer can say "adds an enum with 2 members" / "adds an exception class".
    FactsCase(
        name="python-enum-class",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\nclass Color(Enum):\n    RED = 1\n    GREEN = 2\n",
        entity_type="class_definition",
        required_facts={"is_enum": True, "base_count": 1, "field_count": 2, "method_count": 0},
    ),
    FactsCase(
        name="python-exception-class",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\nclass ParseError(ValueError):\n    pass\n",
        entity_type="class_definition",
        required_facts={"is_exception": True, "base_count": 1},
    ),
    FactsCase(
        name="python-class-shape",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after=(
            "x = 1\n"
            "class Widget:\n"
            "    kind = 1\n"
            "    def a(self):\n"
            "        pass\n"
            "    def b(self):\n"
            "        pass\n"
        ),
        entity_type="class_definition",
        required_facts={"method_count": 2, "field_count": 1},
    ),
    FactsCase(
        # Cross-language class parity (#70 + #69-D): a JS class yields method_count over the
        # language-agnostic tree (derive_node_facts), not just Python's CST path.
        name="javascript-class-methods",
        language="javascript",
        filename="m.js",
        before="const x = 1;\n",
        after=(
            "const x = 1;\n"
            "class Point {\n"
            "  x() { return 1; }\n"
            "  y() { return 2; }\n"
            "}\n"
        ),
        entity_type="class_declaration",
        required_facts={"method_count": 2},
    ),
    # Decorator semantics (#69 catalog C/D): behavior flags folded in from the decorated_definition
    # wrapper, so the explainer can say "a read-only property" / "a dataclass" — never the name.
    FactsCase(
        name="python-property",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after=(
            "x = 1\n"
            "class C:\n"
            "    @property\n"
            "    def x(self):\n"
            "        return self._x\n"
        ),
        entity_type="function_definition",
        required_facts={"is_property": True, "decorator_count": 1},
    ),
    FactsCase(
        name="python-staticmethod",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after=(
            "x = 1\n"
            "class C:\n"
            "    @staticmethod\n"
            "    def f():\n"
            "        return 1\n"
        ),
        entity_type="function_definition",
        required_facts={"is_staticmethod": True},
    ),
    FactsCase(
        name="python-dataclass",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\n@dataclass\nclass Point:\n    x: int = 0\n    y: int = 0\n",
        entity_type="class_definition",
        required_facts={"is_dataclass": True},
    ),
    # Param kinds (#69 catalog C): optional / keyword-only / variadic counts, no parameter names.
    FactsCase(
        name="python-param-kinds",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after="x = 1\ndef f(a, b=1, *args, c, d=2, **kwargs):\n    return a\n",
        entity_type="function_definition",
        required_facts={
            "default_count": 2,
            "keyword_only_count": 2,
            "has_variadic": True,
            "has_kwargs": True,
        },
    ),
    # Coupling (#69-J): outbound-call fan-out + self-recursion, no callee names.
    FactsCase(
        name="python-recursive",
        language="python",
        filename="m.py",
        before="x = 1\n",
        after=(
            "x = 1\n"
            "def fact(n):\n"
            "    if n <= 1:\n"
            "        return 1\n"
            "    return n * fact(n - 1)\n"
        ),
        entity_type="function_definition",
        required_facts={"recursive": True, "call_count": 1},
    ),
    FactsCase(
        # Cross-language coupling (#70 + #69-J): a JS recursive function via derive_node_facts.
        name="javascript-recursive",
        language="javascript",
        filename="m.js",
        before="const x = 1;\n",
        after="const x = 1;\nfunction walk(n) {\n  if (n > 0) walk(n - 1);\n}\n",
        entity_type="function_declaration",
        required_facts={"recursive": True},
    ),
]


def _iter_change_nodes(diff):
    """Every node touched by a change, including descendants (a new method lives inside the
    ADDITION of its enclosing class)."""
    for change in diff.changes:
        for root in (change.old_node, change.new_node):
            if root is None:
                continue
            stack = [root]
            while stack:
                node = stack.pop()
                yield node
                stack.extend(node.children or [])


def _added_entity_facts(case: FactsCase):
    diff = SemanticDiffer().diff_strings(
        case.before, case.after, filename=case.filename, language_hint=case.language
    )
    for node in _iter_change_nodes(diff):
        if node.node_type == case.entity_type and node.facts is not None:
            return node.facts
    return None


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_intent_facts_are_sufficient(case: FactsCase) -> None:
    facts = _added_entity_facts(case)
    assert facts is not None, (
        f"{case.name}: no facts derived for the added {case.entity_type} — the intent explainer "
        f"has no structural signal for {case.language} (issue #70)."
    )
    dumped = facts.model_dump()
    for key, expected in case.required_facts.items():
        assert dumped.get(key) == expected, (
            f"{case.name}: facts[{key!r}] = {dumped.get(key)!r}, expected {expected!r}. "
            f"Full facts: {dumped}"
        )
