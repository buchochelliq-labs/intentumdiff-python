"""Standard scenarios for go (hardening matrix, issue #42).

Mirrors the Python reference suite (`test_scenarios_python.py`) case for case, so the two
languages are held to the same shapes. One of the fifteen scenarios is not represented:
``add_decorator`` — Go has no decorator/annotation syntax, so the scenario is genuinely
inapplicable and is omitted rather than faked (an absent case is the documented way to say
"this language does not have this construct"; see scenario_suite.py). ``async_toggle`` is
expressed the way Go expresses concurrency: ``go f()`` rather than ``async def``.
"""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite

_GO = (
    "package main\n\n"
    'func greet(name string) {\n\tprintln("Hi " + name)\n}\n\n'
    "func add(a, b int) int {\n\treturn a + b\n}\n\n"
    "func sub(a, b int) int {\n\treturn a - b\n}\n"
)

_MUL = "func mul(x, y int) int {\n\treturn x * y\n}\n"


class TestGo(CodeScenarioSuite):
    language = "go"
    filename = "a.go"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'package main\n\nfunc f() {}\n',
            'package main\n\nfunc f() {\n\tprintln("Hello")\n}\n',
            exact_total=1),
        "add_fn_at_end": Case(_GO, _GO + "\n" + _MUL, additions=("mul",), exact_total=1),
        "add_fn_in_middle": Case(
            _GO, _GO.replace("func add(", _MUL + "\nfunc add(", 1),
            additions=("mul",), exact_total=1),
        "delete_fn_at_end": Case(
            _GO, _GO.replace("\nfunc sub(a, b int) int {\n\treturn a - b\n}\n", ""),
            deletions=("sub",), exact_total=1),
        "delete_fn_in_middle": Case(
            _GO, _GO.replace("\nfunc add(a, b int) int {\n\treturn a + b\n}\n", ""),
            deletions=("add",), exact_total=1),
        "modify_fn_body": Case(
            _GO, _GO.replace('"Hi "', '"Hello "'),
            modifications=(('"Hi "', '"Hello "'),), exact_total=1),
        "rename_fn": Case(
            _GO, _GO.replace("func greet(", "func greeting("),
            renames=(("greet", "greeting"),), exact_total=1),
        # The renames themselves are right, but both parameters share one `int` type
        # identifier and rewriting the parameter list drags it into a spurious MOVE — the
        # same leakage class the Python case forbids via `parameters`/`binary_operator`.
        "rename_param": Case(
            _GO,
            _GO.replace("func add(a, b int) int {\n\treturn a + b",
                        "func add(x, y int) int {\n\treturn x + y"),
            renames=(("a", "x"), ("b", "y")),
            forbid_node_types=("type_identifier",),
            xfail="param rename drags the shared `int` type_identifier into a MOVE"),
        # As in Python: the LIS keeps the first entity stationary, so `add` is the mover.
        "move_fn": Case(
            'package main\n\nfunc greet(name string) {\n\tprintln("Hi")\n}\n\n'
            "func add(a, b int) int {\n\treturn a + b\n}\n",
            "package main\n\nfunc add(a, b int) int {\n\treturn a + b\n}\n\n"
            'func greet(name string) {\n\tprintln("Hi")\n}\n',
            not_style_only=True, moves=("add",), exact_total=1),
        "add_param": Case(
            _GO, _GO.replace("func add(a, b int) int", "func add(a, b, c int) int"),
            additions=("c",), exact_total=1),
        "delete_and_add_unrelated": Case(
            "package main\n\nfunc oldOne() int {\n\treturn 1\n}\n\n"
            "func keep() int {\n\treturn 0\n}\n",
            "package main\n\nfunc keep() int {\n\treturn 0\n}\n\n"
            "func newOne() int {\n\treturn 2\n}\n",
            deletions=("oldOne",), additions=("newOne",), exact_total=2),
        # Python gets this in 2 changes (import ADDITION + one statement MODIFICATION, the
        # #33 shape-gated decomposition). Go splits the rewritten return into a
        # DELETION/ADDITION pair instead of promoting it to a MODIFICATION, so it lands 3.
        "add_import_and_use": Case(
            "package main\n\nfunc f(p string) string {\n\treturn p\n}\n",
            'package main\n\nimport "path/filepath"\n\n'
            "func f(p string) string {\n\treturn filepath.Base(p)\n}\n",
            additions=("import_declaration",), exact_total=2,
            xfail="the rewritten return splits into DELETION+ADDITION instead of one "
                  "MODIFICATION, so the total is 3 (Python's #33 promotion does not fire here)"),
        # Go's concurrency toggle, standing in for `async def`: wrapping a call in `go`
        # changes what the program does, so it can never be style-only (#30 class).
        "async_toggle": Case(
            "package main\n\nfunc run() {\n\twork()\n}\n",
            "package main\n\nfunc run() {\n\tgo work()\n}\n",
            not_style_only=True),
        "style_only": Case(
            _GO, _GO.replace("\treturn a + b", "\treturn a  +  b"), style_only=True),
    }
