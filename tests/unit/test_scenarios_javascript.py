"""Standard scenarios for javascript (hardening matrix, issue #42).

Mirrors the Python reference suite (`test_scenarios_python.py`) case for case, so the two
languages are held to the same shapes.

``async_toggle`` pins issue #30: `async function f()` vs `function f()` must never read
STYLE-ONLY. It used to diff to ZERO changes (tree-sitter parses `async` as an anonymous
child, named-children iteration dropped it, and the two trees came out byte-identical).
The js-ts parser now mints `async_*` node types the way the Python parser mints
`async_function_def`, so the case is a passing pin.
"""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite

_JS = (
    'function greet(name) {\n  console.log("Hi " + name);\n}\n\n'
    "function add(a, b) {\n  return a + b;\n}\n\n"
    "function sub(a, b) {\n  return a - b;\n}\n"
)

_MUL = "function mul(x, y) {\n  return x * y;\n}\n"


class TestJavascript(CodeScenarioSuite):
    language = "javascript"
    filename = "a.js"
    CASES = {
        # Issue #42: a trivial/empty body replaced by a real body is MEANINGFUL,
        # never style-only (the #41 class).
        "trivial_body_to_real_body": Case(
            'function f() {}\n',
            "function f() {\n  console.log('Hello')\n}\n",
            exact_total=1),
        "add_fn_at_end": Case(_JS, _JS + "\n" + _MUL, additions=("mul",), exact_total=1),
        "add_fn_in_middle": Case(
            _JS, _JS.replace("function add(", _MUL + "\nfunction add(", 1),
            additions=("mul",), exact_total=1),
        "delete_fn_at_end": Case(
            _JS, _JS.replace("\nfunction sub(a, b) {\n  return a - b;\n}\n", ""),
            deletions=("sub",), exact_total=1),
        "delete_fn_in_middle": Case(
            _JS, _JS.replace("\nfunction add(a, b) {\n  return a + b;\n}\n", ""),
            deletions=("add",), exact_total=1),
        "modify_fn_body": Case(
            _JS, _JS.replace('"Hi "', '"Hello "'),
            modifications=(("Hi ", "Hello "),), exact_total=1),
        "rename_fn": Case(
            _JS, _JS.replace("function greet(", "function greeting("),
            renames=(("greet", "greeting"),), exact_total=1),
        "rename_param": Case(
            _JS,
            _JS.replace("function add(a, b) {\n  return a + b;",
                        "function add(x, y) {\n  return x + y;"),
            renames=(("a", "x"), ("b", "y")),
            forbid_node_types=("formal_parameters", "binary_expression")),
        # As in Python: the LIS keeps the first entity stationary, so `add` is the mover.
        "move_fn": Case(
            "function greet(n) {\n  console.log(n);\n}\n\n"
            "function add(a, b) {\n  return a + b;\n}\n",
            "function add(a, b) {\n  return a + b;\n}\n\n"
            "function greet(n) {\n  console.log(n);\n}\n",
            not_style_only=True, moves=("add",), exact_total=1),
        # Python lands this in ONE addition. Here the rewritten parameter list also surfaces
        # as a MODIFICATION alongside the ADDITION of `c`, duplicating the same edit.
        "add_param": Case(
            _JS, _JS.replace("function add(a, b)", "function add(a, b, c)"),
            additions=("c",), exact_total=1,
            xfail="the parameter-list MODIFICATION duplicates the `c` ADDITION, so the "
                  "total is 2"),
        "delete_and_add_unrelated": Case(
            "function oldOne() {\n  return 1;\n}\n\nfunction keep() {\n  return 0;\n}\n",
            "function keep() {\n  return 0;\n}\n\nfunction newOne() {\n  return 2;\n}\n",
            deletions=("oldOne",), additions=("newOne",), exact_total=2),
        "add_decorator": Case(
            "class Box {\n  get() {\n    return this.v;\n  }\n}\n",
            "class Box {\n  @cached\n  get() {\n    return this.v;\n  }\n}\n",
            exact_total=1),
        # Python gets this in 2 (import ADDITION + one statement MODIFICATION, the #33
        # shape-gated decomposition); here the rewritten return also emits a call ADDITION.
        "add_import_and_use": Case(
            "function f(p) {\n  return p;\n}\n",
            'import path from "path";\n\nfunction f(p) {\n  return path.basename(p);\n}\n',
            exact_total=2,
            xfail="the rewritten return emits an extra call_expression ADDITION, so the "
                  "total is 3"),
        # Issue #30: async-ness must survive into the tree — the toggle is a semantic change
        # (every call site now gets a Promise), so it can never be style-only. tree-sitter
        # parses `async function` as a plain `function_declaration` whose `async` keyword is
        # an ANONYMOUS child, and named-children iteration dropped it, so both sides used to
        # serialise identically. The js-ts parser now mints `async_function_declaration`
        # (and the arrow/method/function-expression variants), mirroring the python parser's
        # `async_function_def` — so this is a passing pin, not an xfail.
        "async_toggle": Case(
            "function f() {\n  return 1;\n}\n",
            "async function f() {\n  return 1;\n}\n",
            not_style_only=True),
        "style_only": Case(
            _JS, _JS.replace("  return a + b;", "  return a  +  b;"), style_only=True),
    }
