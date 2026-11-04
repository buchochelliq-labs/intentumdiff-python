"""Source-judged operator controls for the pinned Rust engine (Core #229).

Parity between Rust and Python is not sufficient when both call the same oracle.
These assertions state the expected semantics independently of both runtimes.
"""

from __future__ import annotations

import pytest

from intentumdiff.differ import SemanticDiffer


@pytest.mark.parametrize(
    "filename, old, new",
    [
        ("calc.py", "def f(a, b):\n    return a + b\n", "def f(a, b):\n    return a - b\n"),
        ("main.go", "package main\nfunc f(a,b int) int { return a + b }\n", "package main\nfunc f(a,b int) int { return a - b }\n"),
        ("app.js", "function f(a,b) { return a + b; }\n", "function f(a,b) { return a - b; }\n"),
        ("app.ts", "function f(a:number,b:number):number { return a + b; }\n", "function f(a:number,b:number):number { return a - b; }\n"),
        ("lib.rs", "fn f(a: i32, b: i32) -> i32 { a + b }\n", "fn f(a: i32, b: i32) -> i32 { a - b }\n"),
        ("calc.py", "def f(a,b):\n    return a and b\n", "def f(a,b):\n    return a or b\n"),
    ],
)
def test_operator_edits_are_semantic_modifications(filename: str, old: str, new: str) -> None:
    diff = SemanticDiffer().diff_strings(old, new, filename)
    assert diff.has_semantic_changes is True, diff.model_dump_json()
    assert diff.is_style_only is False, diff.model_dump_json()
    assert any(
        change.change_type == "MODIFICATION"
        or getattr(change.change_type, "value", None) == "MODIFICATION"
        for change in diff.changes
    ), diff.model_dump_json()
    assert not any(
        getattr(group.kind, "value", group.kind) == "IGNORED_STYLE"
        for group in diff.change_groups
    ), diff.model_dump_json()


@pytest.mark.parametrize(
    "old, new",
    [
        ("def f(a, b):\n    return a + b\n", "def f(a, b):\n    # explanation\n    return a + b\n"),
        ("def f(a, b):\n    return a + b\n", "def f(a, b):\n    return a  +  b\n"),
    ],
)
def test_comment_or_spacing_only_edits_remain_style_only(old: str, new: str) -> None:
    diff = SemanticDiffer().diff_strings(old, new, "calc.py")
    assert diff.is_style_only is True, diff.model_dump_json()
    assert diff.has_semantic_changes is False, diff.model_dump_json()


@pytest.mark.parametrize(
    "filename, old, new",
    [
        # Core #237: the keyword selects the iteration protocol; dropping it changes behaviour.
        ("loop.js", "async function f(xs) {\n  for await (const x of xs) { g(x); }\n}\n",
         "async function f(xs) {\n  for (const x of xs) { g(x); }\n}\n"),
        ("loop.ts", "async function f(xs: AsyncIterable<number>) {\n  for await (const x of xs) { g(x); }\n}\n",
         "async function f(xs: AsyncIterable<number>) {\n  for (const x of xs) { g(x); }\n}\n"),
        ("keys.js", "function f(o) {\n  for (const k in o) { g(k); }\n}\n",
         "function f(o) {\n  for (const k of o) { g(k); }\n}\n"),
        ("comp.py", "async def f(xs):\n    return [x async for x in xs]\n",
         "async def f(xs):\n    return [x for x in xs]\n"),
    ],
)
def test_iteration_protocol_keyword_edits_are_semantic(filename: str, old: str, new: str) -> None:
    for before, after in ((old, new), (new, old)):
        diff = SemanticDiffer().diff_strings(before, after, filename)
        assert diff.is_style_only is False, diff.model_dump_json()
        assert diff.has_semantic_changes is True, diff.model_dump_json()
        assert diff.changes, diff.model_dump_json()
