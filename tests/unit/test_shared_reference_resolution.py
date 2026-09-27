"""Reference-resolution decisions are shared Rust semantics."""
import json
import os
from pathlib import Path
import subprocess
import pytest
from intentumdiff.core.index import SemanticIndex
from intentumdiff.core.models import ReferenceUsage


def test_source_judged_reference_corpus():
    cases = json.loads((Path(__file__).parents[1] / "fixtures/reference_resolution.json").read_text(encoding="utf-8"))
    for case in cases:
        index = SemanticIndex()
        index.load_symbol_table_json(json.dumps({"f":case["definitions"]}))
        index.load_reference_table_json(json.dumps({"f":case["references"]}))
        raw = [r.model_dump(mode="json") for r in index.find_references("f")]
        result = index.find_references("f", resolve=True)
        assert len(result) == len(case["references"])
        assert (result[0].resolved_definition.file if result[0].resolved_definition else None) == case["expected_file"]
        assert [r.model_dump(mode="json") for r in index.find_references("f")] == raw
        if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
            native = json.loads(subprocess.run([probe], input=json.dumps({"handler":"resolve_references", **case}), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
            assert [ReferenceUsage.model_validate(r) for r in native] == result


def test_resolution_engine_failure_is_not_unresolved_success(monkeypatch):
    from intentumdiff import rust_core
    index = SemanticIndex()
    index.load_symbol_table_json("{}")
    index.load_reference_table_json("{}")
    def broken(*args):
        raise RuntimeError("engine failed")
    monkeypatch.setattr(rust_core, "_c_abi_call", broken)
    with pytest.raises(RuntimeError, match="engine failed"):
        index.find_references("f", resolve=True)
