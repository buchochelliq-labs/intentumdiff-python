"""Regression guards for semantic subtree parity in the 0.0.2 release gate."""
from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_migration_parity.py"


def _projection(diff):
    return runpy.run_path(str(SCRIPT))["projection"](diff)


def _node(label: str, *, children=None, col: int = 0):
    result = {
        "node_type": "identifier",
        "label": label,
        "position": {"start_line": 0, "start_col": col, "end_line": 0, "end_col": col + len(label)},
        "id": "nonsemantic-random-id",
    }
    if children is not None:
        result["children"] = children
    return result


def _diff(node):
    return {
        "changes": [{
            "change_type": "ADDITION", "old_node": None, "new_node": node,
            "refactoring_kind": None,
        }],
        "change_groups": [],
        "has_semantic_changes": True, "is_style_only": False, "is_fallback": False,
    }


def test_corpus_parity_rejects_dropped_descendants():
    full = _diff(_node("module", children=[_node("count")]))
    truncated = _diff(_node("module"))
    assert _projection(full) != _projection(truncated)


def test_corpus_parity_rejects_corrupt_nested_spans_and_labels():
    full = _diff(_node("module", children=[_node("count", children=[_node("value", col=2)])]))
    changed_span = _diff(_node("module", children=[_node("count", children=[_node("value", col=5)])]))
    changed_label = _diff(_node("module", children=[_node("count", children=[_node("other", col=2)])]))
    assert _projection(full) != _projection(changed_span)
    assert _projection(full) != _projection(changed_label)


def test_corpus_parity_normalizes_absent_and_empty_child_lists():
    no_key = _diff(_node("value"))
    empty_key = _diff(_node("value", children=[]))
    assert _projection(no_key) == _projection(empty_key)


def test_corpus_parity_ignores_only_nonsemantic_ids():
    left = _diff(_node("value"))
    right = _diff(_node("value"))
    right["changes"][0]["new_node"]["id"] = "another-id"
    assert _projection(left) == _projection(right)


def test_parity_projection_rejects_different_rust_group_rules_and_labels():
    left = _diff(_node("value"))
    left["change_groups"] = [{
        "kind": "IGNORED_STYLE",
        "rule_id": "javascript.formatting.call_argument_wrapping_equivalence",
        "raw_change_indices": [],
        "old_labels": ["old wrapping"],
        "new_labels": ["new wrapping"],
        "old_node_ids": ["route-dependent-id"],
        "metadata": {"reason": "wrapping"},
    }]
    different_rule = json.loads(json.dumps(left))
    different_rule["change_groups"][0]["rule_id"] = "unsupported.semantic_claim"
    assert _projection(left) != _projection(different_rule)

    different_labels = json.loads(json.dumps(left))
    different_labels["change_groups"][0]["new_labels"] = ["fabricated changed text"]
    assert _projection(left) != _projection(different_labels)


def test_parity_projection_excludes_only_nonsemantic_group_metadata():
    left = _diff(_node("value"))
    left["change_groups"] = [{
        "kind": "MEANINGFUL_CHANGE", "rule_id": "rust.named_entity_edit",
        "raw_change_indices": [0], "old_labels": ["before"], "new_labels": ["after"],
        "old_node_ids": ["native-1"], "new_node_ids": ["native-2"],
        "metadata": {"backend": "native"},
    }]
    alternate = json.loads(json.dumps(left))
    alternate["change_groups"][0]["old_node_ids"] = ["python-1"]
    alternate["change_groups"][0]["new_node_ids"] = ["python-2"]
    alternate["change_groups"][0]["metadata"] = {"backend": "python"}
    assert _projection(left) == _projection(alternate)


def test_vbnet_declaration_extent_requires_closing_source_line():
    checker = runpy.run_path(str(SCRIPT))["check_vbnet_declaration_extent"]
    source = (Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
              / "vbnet" / "playground.vb").read_text(encoding="utf-8")
    case = {"id": "corpus-vbnet-addition", "new": source, "old": ""}
    declaration = _node("Multiply", col=4)
    declaration["node_type"] = "function_statement"
    declaration["position"] = {
        "start_line": 9, "start_col": 4, "end_line": 10, "end_col": 60,
    }
    with pytest.raises(AssertionError, match="VB.NET Multiply full declaration span"):
        checker(case, _diff(declaration))

    declaration["position"] = {
        "start_line": 9, "start_col": 4, "end_line": 11, "end_col": 16,
    }
    checker(case, _diff(declaration))


def test_vbnet_declaration_extent_rejects_silently_missing_declaration():
    checker = runpy.run_path(str(SCRIPT))["check_vbnet_declaration_extent"]
    source = (Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
              / "vbnet" / "playground.vb").read_text(encoding="utf-8")
    case = {"id": "corpus-vbnet-addition", "new": source, "old": ""}
    with pytest.raises(AssertionError, match="VB.NET Multiply full declaration span"):
        checker(case, _diff(_node("unrelated")))


def test_corpus_addition_rejects_equal_but_fabricated_native_python_content():
    """Two routes agreeing on invented labels must fail source-judged acceptance."""
    checker = runpy.run_path(str(SCRIPT))["check_visible_corpus_tokens"]
    case = {
        "id": "corpus-python-addition",
        "old": "",
        "new": "def greet(name): return 'Hello'",
        "required_visible_tokens": ["greet", "name", "Hello"],
    }
    fabricated = _diff(_node("NOT IN SOURCE"))
    with pytest.raises(AssertionError, match="missing source-ratcheted semantic tokens"):
        checker(case, fabricated)
    # Presence must be inside the exposed new-tree descendants, not merely
    # in the input source or the other route's result.
    good = _diff(_node("module", children=[
        _node("greet", children=[_node("name"), _node("Hello")]),
    ]))
    checker(case, good)
    dropped_child = _diff(_node("module", children=[_node("greet")]))
    with pytest.raises(AssertionError, match="missing source-ratcheted semantic tokens"):
        checker(case, dropped_child)


def test_manifest_visible_tokens_are_carried_into_every_addition_case():
    harness = runpy.run_path(str(SCRIPT))
    cases = harness["full_corpus_cases"]()
    additions = [case for case in cases if case["id"].endswith("-addition")]
    assert len(additions) == 74
    for case in additions:
        manifest = (Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
                    / case["corpus_language"] / "playground.expect.json")
        expected = json.loads(manifest.read_text(encoding="utf-8"))
        assert case["required_visible_tokens"] == expected["visible_tokens"], case["id"]


def test_corpus_spans_require_utf8_character_boundaries_and_exclude_newlines():
    """In-bounds byte offsets may still point into a code point or CRLF."""
    checker = runpy.run_path(str(SCRIPT))["check_source_spans"]
    case = {"id": "corpus-utf8-addition", "old": "", "new": "é\r\n"}
    node = _node("é", col=0)
    # The single visible character occupies exactly bytes [0, 2].
    node["position"] = {
        "start_line": 0, "start_col": 0, "end_line": 0, "end_col": 2,
    }
    checker(case, _diff(node))

    # Column 1 splits the two-byte UTF-8 encoding of 'é'.
    node["position"]["start_col"] = 1
    with pytest.raises(AssertionError, match="invalid UTF-8 byte boundary"):
        checker(case, _diff(node))

    # CRLF bytes are not part of the visible source line's column space.
    node["position"]["start_col"] = 0
    node["position"]["end_col"] = 3
    with pytest.raises(AssertionError, match="invalid UTF-8 byte boundary"):
        checker(case, _diff(node))


def test_corpus_spans_reject_unterminated_virtual_eof_row():
    """A next-line EOF position is only legal after an actual source newline."""
    checker = runpy.run_path(str(SCRIPT))["check_source_spans"]
    case = {"id": "corpus-eof-span", "old": "", "new": "é"}
    node = _node("é", col=0)
    node["position"] = {
        "start_line": 0, "start_col": 0, "end_line": 1, "end_col": 0,
    }
    with pytest.raises(AssertionError, match="non-source row"):
        checker(case, _diff(node))

    case["new"] = "é\n"
    checker(case, _diff(node))

    node["position"]["end_col"] = 1
    with pytest.raises(AssertionError, match="non-source row"):
        checker(case, _diff(node))


def test_corpus_gate_rejects_known_platform_parser_exclusion():
    """The 74-language gate cannot certify generic text in lieu of PowerShell."""
    check = runpy.run_path(str(SCRIPT))["check_available_corpus_parser"]
    case = {"id": "corpus-powershell-addition", "corpus_language": "powershell", "filename": "code.ps1"}
    with pytest.raises(AssertionError, match="semantic parser unavailable on Windows/ARM64"):
        check(case, system="Windows", machine="ARM64")
    # A platform where the component is supported must not be exempted.
    check(case, system="Linux", machine="x86_64")


def test_all_corpus_semantic_mutations_reject_false_style_reduction():
    """The 74-language gate must reject suppressed edits, not just additions."""
    harness = runpy.run_path(str(SCRIPT))
    cases = harness["full_corpus_cases"]()
    mutations = [case for case in cases if not case["id"].endswith("-addition")]
    assert mutations, "no semantic content mutations in the release corpus"
    assert all(case.get("forbid_ignored_style") is True for case in cases)

    # Both API routes could agree that a content edit is partly 'ignored
    # style' and still report a semantic change. That must not certify parity.
    case = mutations[0]
    fabricated = _diff(_node("updated_content"))
    fabricated["metadata"] = {"ignored_style_changes": 1}
    with pytest.raises(AssertionError):
        harness["check_expectations"](case, fabricated)
    fabricated["metadata"]["ignored_style_changes"] = 0
    harness["check_expectations"](case, fabricated)


def test_corpus_fallback_is_rejected_even_when_both_routes_have_changes():
    """Parser availability and route equality cannot certify a generic fallback."""
    checker = runpy.run_path(str(SCRIPT))["check_expectations"]
    case = {"id": "corpus-python-bump_numeric_literal", "corpus_language": "python"}
    real_semantic_diff = _diff(_node("updated_number"))
    checker(case, real_semantic_diff)

    fabricated_fallback = _diff(_node("updated_number"))
    fabricated_fallback["is_fallback"] = True
    with pytest.raises(AssertionError, match="semantic parser fell back"):
        checker(case, fabricated_fallback)


@pytest.mark.parametrize("response", [
    None,
    [],
    "not JSON",
    "{}",
    {"fallback": "missing parser"},
    {"diff": []},
    {},
])
def test_direct_c_abi_source_review_fails_closed_on_bad_envelopes(
    monkeypatch, response
):
    """A missing/invalid native source result cannot become Python success."""
    from intentumdiff import rust_core

    monkeypatch.setattr(rust_core, "_c_abi_call", lambda *_args: response)
    check = runpy.run_path(str(SCRIPT))["run_direct_c_abi_source_review"]
    case = {
        "id": "corpus-tsx-addition", "corpus_language": "tsx",
        "filename": "component.tsx", "old": "", "new": "const value = 1;\\n",
    }
    with pytest.raises(AssertionError):
        check(case, Path("/candidate"), Path("/candidate/wasm"))


def test_direct_c_abi_source_review_preserves_exact_request_and_result(monkeypatch):
    """Use pinned staging paths and the same explicit parser hint as native."""
    from intentumdiff import rust_core

    calls = []
    expected = _diff(_node("value"))
    def fake_abi(*args):
        calls.append(args)
        return {"diff": expected}
    monkeypatch.setattr(rust_core, "_c_abi_call", fake_abi)
    check = runpy.run_path(str(SCRIPT))["run_direct_c_abi_source_review"]
    case = {
        "id": "corpus-tsx-addition", "corpus_language": "tsx",
        "filename": "component.tsx", "old": "", "new": "const value = 1;\\n",
    }
    # The transport must pass through the exact path values supplied by the
    # host. Windows Path("/candidate") uses backslashes, so a POSIX literal
    # would reject a correct, unchanged C ABI request on both Windows runners.
    repo_path = Path("/candidate")
    wasm_path = repo_path / "wasm"
    assert check(case, repo_path, wasm_path) == expected
    assert len(calls) == 1
    handler, repo, filename, old, new, config, wasm = calls[0]
    assert handler == "live_diff_contents"
    assert (repo, filename, old, new, wasm) == (
        str(repo_path), "component.tsx", "", "const value = 1;\\n", str(wasm_path),
    )
    assert json.loads(config) == {"language_hint": "tsx", "strict_plugins": True}


def test_tsx_literal_exact_span_rejects_false_style_and_off_by_one():
    """Source-judge the retained TSX literal across native, direct ABI and Python."""
    import copy

    check = runpy.run_path(str(SCRIPT))["check_tsx_literal_exact"]
    case = {"id": 'corpus-tsx-edit_"_string'}
    old = _node("Alice", col=22)
    new = _node("AliceX", col=22)
    for node in (old, new):
        node["position"]["start_line"] = 6
        node["position"]["end_line"] = 6
    valid = _diff(new)
    valid["changes"][0].update({
        "change_type": "MODIFICATION",
        "old_node": old,
    })
    check(case, valid)

    corrupt = copy.deepcopy(valid)
    corrupt["changes"][0]["new_node"]["position"]["end_col"] += 1
    with pytest.raises(AssertionError, match="incorrect exact UTF-8 source span"):
        check(case, corrupt)

    false_style = copy.deepcopy(valid)
    false_style["change_groups"] = [{"kind": "IGNORED_STYLE"}]
    with pytest.raises(AssertionError, match="false formatting group"):
        check(case, false_style)

    suppressed = copy.deepcopy(valid)
    suppressed["metadata"] = {"ignored_style_changes": [{"rule_id": "unjustified"}]}
    with pytest.raises(AssertionError, match="falsely classified as formatting"):
        check(case, suppressed)
