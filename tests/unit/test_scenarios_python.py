"""Standard scenarios for Python (code, the reference language on the certified Rust batch path)."""

from __future__ import annotations

from tests.unit.scenario_suite import Case, CodeScenarioSuite

_PY = "def greet(name):\n    print('Hi ' + name)\n\ndef add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n"


class TestPython(CodeScenarioSuite):
    language = "python"
    filename = "m.py"
    CASES = {
        "add_fn_at_end": Case(_PY, _PY + "\ndef mul(x, y):\n    return x * y\n", additions=("mul",), exact_total=1),
        "add_fn_in_middle": Case(_PY, "def greet(name):\n    print('Hi ' + name)\n\ndef mul(x, y):\n    return x * y\n\ndef add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n", additions=("mul",), exact_total=1),
        "delete_fn_at_end": Case(_PY, "def greet(name):\n    print('Hi ' + name)\n\ndef add(a, b):\n    return a + b\n", deletions=("sub",), exact_total=1),
        "delete_fn_in_middle": Case(_PY, "def greet(name):\n    print('Hi ' + name)\n\ndef sub(a, b):\n    return a - b\n", deletions=("add",), exact_total=1),
        # Fixed 2026-07-05 (issue #13): suppress_add_delete_drafts_covered_by_pairings removes the
        # ADDITION/DELETION drafts that duplicate a paired change's endpoints.
        "modify_fn_body": Case(_PY, _PY.replace("print('Hi ' + name)", "print('Hello ' + name)"), modifications=(("'Hi '", "'Hello '"),), exact_total=1),
        # Fixed 2026-07-05 (issue #10): promote_same_id_named_renames_from_add_delete_drafts now
        # emits REFACTORING RENAME_SYMBOL instead of MOVE for a same-id relabeled entity.
        "rename_fn": Case(_PY, _PY.replace("def greet(name):", "def greeting(name):"), renames=(("greet", "greeting"),), exact_total=1),
        # Fixed 2026-07-06 (issue #11, via the #33 matched-parent promotion): param/operator
        # add-delete pairs now promote to modifications, letting the rename detection fire.
        "rename_param": Case(_PY, _PY.replace("def add(a, b):\n    return a + b", "def add(x, y):\n    return x + y"), renames=(("a", "x"), ("b", "y")), forbid_node_types=("parameters", "binary_operator")),
        # Fixed 2026-07-06 (issue #12): the refine-pass container-noise suppressor no longer
        # blanket-drops same-identity entity REORDERs, so they reach the LIS discrimination in
        # finalize — insertion shifts stay suppressed, a genuine swap surfaces as ONE MOVE
        # (the LIS keeps the first entity as stationary, so `add` is the reported mover).
        "move_fn": Case("def greet(name):\n    print('Hi ' + name)\n\ndef add(a, b):\n    return a + b\n", "def add(a, b):\n    return a + b\n\ndef greet(name):\n    print('Hi ' + name)\n", not_style_only=True, moves=("add",), exact_total=1),
        "add_param": Case(_PY, _PY.replace("def add(a, b):", "def add(a, b, c):"), additions=("c",), exact_total=1),
        # Issue #30: async-ness must survive into the tree — the toggle is a semantic change
        # (call site gets a coroutine), never style-only.
        "async_toggle": Case("def f():\n    return 1\n", "async def f():\n    return 1\n", not_style_only=True),
        # Fixed 2026-07-06 (issue #31): label-match parent anchoring + entity-aware leaf-update
        # gating + no containment-swallowing of entity deletions — removed code always surfaces.
        "delete_and_add_unrelated": Case(
            "def old_one():\n    return 1\n\ndef keep():\n    return 0\n",
            "def keep():\n    return 0\n\ndef new_one():\n    return 2\n",
            deletions=("old_one",), additions=("new_one",), exact_total=2),
        # Fixed 2026-07-06 (issue #32): decorated_definition is transparent to matching, the
        # line-move promoter uses LIS insertion-shift discrimination, and identifier-rename
        # sweeps no longer swallow named entities — one wrapper ADDITION, no false moves.
        "add_decorator": Case(
            "def calc(x):\n    return x * 2\n\nclass Box:\n    def get(self):\n        return self.v\n",
            "@cached\ndef calc(x):\n    return x * 2\n\nclass Box:\n    def get(self):\n        return self.v\n",
            exact_total=1),
        # Fixed 2026-07-06 (issue #33): editing a return expression is ONE statement-level
        # MODIFICATION — decomposition is shape-gated (no positional leaf pairing across
        # different leaf counts) and both halves of a covered rewrite suppress their
        # duplicate inner add/delete drafts.
        "add_import_and_use": Case(
            "def f(p):\n    return p\n",
            "import os\n\ndef f(p):\n    return os.path.basename(p)\n",
            additions=("import_statement",), exact_total=2),
        # Issue #41: a trivial body replaced by a real body is MEANINGFUL, never style-only.
        # (pass was pruned from the semantic tree, so pass -> print() had no deletion side and
        # vanished into a false style-only. Current honest shape: statement MODIFICATION +
        # call ADDITION; compacting to one MODIFICATION may tighten this pin later.)
        "trivial_body_to_real_body": Case(
            'def aaa():\n    pass\n',
            'def aaa():\n    print("Hello, World!")\n',
            exact_total=2),
        "style_only": Case(_PY, _PY.replace("    return a + b", "    return a  +  b"), style_only=True),
    }
