"""Known patch outcomes, independently judged before native/Python comparison."""
import json
import os
from pathlib import Path
import subprocess
import warnings

import pytest
from intentumdiff.sources.patch_source import PatchSource, PatchExcerptWarning


def test_source_judged_patch_corpus():
    cases = json.loads((Path(__file__).parents[1] / "fixtures/patch_source.json").read_text(encoding="utf-8"))
    for case in cases:
        source = PatchSource(case["patch"], case.get("original"), filename=case.get("filename"), require_complete=case.get("require_complete", False))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                old, new, filename, hint = source.get_content()
                actual = {"old_content":old,"new_content":new,"filename":filename,"scope":"excerpt" if source.is_partial else "complete"}
                assert hint is None
            except ValueError as exc:
                actual = {"error":str(exc)}
        if "error" in case:
            assert case["error"] in actual["error"], case["name"]
        else:
            assert actual == case["expected"], case["name"]
            assert bool(caught) == source.is_partial
            if caught:
                assert caught[0].category is PatchExcerptWarning
                assert "excerpt" in str(caught[0].message)
        if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
            native = json.loads(subprocess.run([probe], input=json.dumps({"handler":"reconstruct_patch", **case}), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
            native.pop("warning", None)
            assert native == actual, case["name"]


def test_required_patch_engine_failure_propagates(monkeypatch):
    from intentumdiff import rust_core
    def failed(*args):
        raise RuntimeError("engine unavailable")
    monkeypatch.setattr(rust_core, "_c_abi_call", failed)
    with pytest.raises(RuntimeError, match="engine unavailable"):
        PatchSource("patch").get_content()
