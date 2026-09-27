"""Source expectations and identical-input native/C ABI review comparisons."""
import json
import os
from pathlib import Path
import subprocess

from intentumdiff.rust_core import _c_abi_call


def test_routed_review_source_corpus():
    cases = json.loads((Path(__file__).parents[1] / "fixtures/routed_review.json").read_text(encoding="utf-8"))
    for case in cases:
        result = _c_abi_call("complete_routed_review", json.dumps(case["request"]))
        assert result["has_semantic_changes"] == case["semantic"]
        assert result["is_style_only"] == case["style"]
        assert len(result["changes"]) == case["changes"]
        if "meaningful_indices" in case:
            group = next(g for g in result["change_groups"] if g["kind"] == "MEANINGFUL_CHANGE")
            assert group["raw_change_indices"] == case["meaningful_indices"]
            assert group["old_labels"] == ["2"]
            assert group["new_labels"] == ["3"]
        if case.get("no_style_evidence"):
            assert "ignored_style_changes" not in result["metadata"]
        if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
            native = json.loads(subprocess.run([probe], input=json.dumps({"handler":"complete_routed_review", "request":case["request"]}), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
            assert result == native, case["name"]
