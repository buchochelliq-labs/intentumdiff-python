"""Configuration transport regression: unsupported options must reach Rust."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intentumdiff import DiffConfig
from intentumdiff import rust_core


class FinalizerConfigTransportTests(unittest.TestCase):
    def test_unsupported_overrides_reach_rust_and_its_error_propagates(self):
        for field, value in [
            ("ignore_style", False), ("detect_refactorings", False),
            ("approx_move_threshold", 0.8), ("leaf_text_diff", False),
            ("extra_trivia_types", ["identifier"]),
        ]:
            with self.subTest(field=field):
                captured = {}
                error = ValueError("unsupported configuration override")

                def finalize(*args):
                    captured.update(json.loads(args[-1]))
                    raise error

                tree = SimpleNamespace(model_dump_json=lambda: "{}")
                with patch.object(rust_core, "_load_backend",
                                  return_value=SimpleNamespace(finalize_review_json=finalize)):
                    with self.assertRaises(RuntimeError) as raised:
                        rust_core.try_rust_finalize_review(
                            old_tree=tree, new_tree=tree, old_source="a", new_source="b",
                            language="python", config=DiffConfig(**{field: value}),
                        )
                self.assertEqual(captured.get(field), value)
                self.assertIs(raised.exception.__cause__, error)


class NativeFinalizerConfigTests(unittest.TestCase):
    def test_real_rust_rejects_each_unsupported_override(self):
        tree_json = json.dumps({
            "id": "m", "node_type": "module", "label": "m",
            "position": {"start_line": 0, "start_col": 0, "end_line": 0, "end_col": 1},
            "structural_hash": "h", "children": [],
        })
        tree = SimpleNamespace(model_dump_json=lambda: tree_json)
        for field, value in [
            ("ignore_style", False), ("detect_refactorings", False),
            ("approx_move_threshold", 0.8), ("leaf_text_diff", False),
            ("extra_trivia_types", ["identifier"]),
        ]:
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, field):
                    rust_core.try_rust_finalize_review(
                        old_tree=tree, new_tree=tree, old_source="a", new_source="b",
                        language="python", config=DiffConfig(**{field: value}),
                    )
