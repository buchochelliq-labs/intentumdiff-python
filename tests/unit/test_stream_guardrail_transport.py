"""Execute host routing in isolation; native semantics are covered by integration tests."""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2] / "src/intentumdiff"


def load_function(path, name, namespace, class_name=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name).body if class_name else tree.body
    function = next(n for n in scope if isinstance(n, ast.FunctionDef) and n.name == name)
    # Retain the actual production body, postponing dependency-only annotations.
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / path), "exec"), namespace)
    return namespace[name]


class StreamGuardrailTransportTests(unittest.TestCase):
    def test_progressive_preserves_final_changes_as_add_events(self):
        change = object()
        source = object()
        calls = []
        def finalized(value):
            calls.append(value)
            return SimpleNamespace(changes=[change])
        namespace = dict(ChangeStreamPhase=SimpleNamespace(FINAL="final"), ChangeStreamEvent=SimpleNamespace)
        method = load_function("differ.py", "diff_stream_progressive", namespace, "SemanticDiffer")
        events = list(method(SimpleNamespace(diff=finalized), source))
        self.assertEqual(calls, [source])
        self.assertEqual([(e.phase, e.action, e.change) for e in events], [("final", "add", change)])

    def test_progressive_propagates_normal_diff_policy_read_failure(self):
        def protected_diff(source):
            raise OSError("source guardrail policy cannot be read")
        host = SimpleNamespace(diff=protected_diff, _run_pipeline_streaming=lambda *a: iter(()))
        source = SimpleNamespace(get_content=lambda: ("old", "new", "alias.json", "json"))
        method = load_function("differ.py", "diff_stream_progressive", {}, "SemanticDiffer")
        with self.assertRaisesRegex(OSError, "source guardrail policy"):
            list(method(host, source))

    def test_stream_cli_keeps_finalized_guardrail_failure_and_reports(self):
        for stream in (False, True):
            for passed in (False, True):
                with self.subTest(stream=stream, passed=passed):
                    change = SimpleNamespace(change_type="MODIFICATION", description="version changed", text_diff="1 -> 2")
                    diff = SimpleNamespace(changes=[change], guardrail_violations=["immutable"] if not passed else [])
                    reports = []
                    calls, diagnostics, printed = [], [], []
                    def finalized(source):
                        calls.append(source)
                        return diff
                    host = SimpleNamespace(diff=finalized, diff_stream=lambda source: iter(()),
                                           _config=SimpleNamespace(plugin_fuel=1))
                    def report(value, args):
                        reports.append(value)
                        return SimpleNamespace(passed=passed)
                    namespace = dict(sys=sys, FileSource=lambda **kw: kw, _differ=lambda *a, **kw: host,
                                     _console=SimpleNamespace(print=printed.append),
                                     _err=SimpleNamespace(print=lambda *a: None),
                                     _render_with_profile=lambda *a, **kw: None,
                                     _emit_guardrail_reports=report,
                                     _record_diagnostics_db=lambda value, args: diagnostics.append(value))
                    load_function("cli/_shared.py", "_exit_if_guardrail_result_fails", namespace)
                    command = load_function("cli/_commands.py", "_cmd_file", namespace)
                    args = SimpleNamespace(old_file="old.json", new_file="new.json", fuel=1,
                                           resolve_references=False, stream=stream, guardrails_strict=True)
                    if passed:
                        command(args)
                    else:
                        with self.assertRaises(SystemExit) as raised:
                            command(args)
                        self.assertEqual(raised.exception.code, 2)
                    self.assertEqual(reports, [diff])
                    self.assertEqual(diagnostics, [diff])
                    self.assertEqual(len(calls), 1)
                    if stream:
                        self.assertEqual(printed, ["MODIFICATION     version changed  [1 -> 2]"])


if __name__ == "__main__":
    unittest.main()
