"""Required Rust review results cannot become manufactured clean reviews."""
import json
from types import SimpleNamespace

import pytest

from intentumdiff import rust_core
from intentumdiff.core.models import Change, ChangeType, DiffConfig, NodePosition, SemanticNode


@pytest.fixture
def tree():
    return SemanticNode(id="root", node_type="document", label="x", structural_hash="x",
                        position=NodePosition(start_line=0, start_col=0, end_line=0, end_col=1))


CASES = {
    "invariance": ("apply_invariances_json", {"changes": [], "change_groups": [], "ignored_style_changes": []}),
    "generic": ("generic_text_review_json", {"used": True, "changes": [], "group": None}),
    "markdown": ("markdown_section_review_json", {"used": True, "moves": [], "renames": [],
        "moved_labels": [], "old_heading_lines": [], "new_heading_lines": [],
        "move_group": None, "rename_group": None}),
    "finalize": ("finalize_review_json", {"used": True, "changes": [], "change_groups": [],
        "is_style_only": True, "no_surviving_changes": True, "ignored_style_changes": []}),
}


def invoke(name, tree):
    if name == "invariance":
        return rust_core.apply_invariances(
            [Change(change_type=ChangeType.MODIFICATION, old_node=tree,
                    new_node=tree.model_copy(update={"label": "y"}))],
            old_tree=tree, new_tree=tree,
            old_source="x", new_source="y", language="generic")
    if name == "generic":
        return rust_core.try_rust_generic_text_review("x", "y", 1)
    if name == "markdown":
        return rust_core.try_rust_markdown_section_review("# x", "# y")
    return rust_core.try_rust_finalize_review(old_tree=tree, new_tree=tree,
        old_source="x", new_source="y", language="generic", config=DiffConfig())


def backend_payload(monkeypatch, name, payload):
    handler, _ = CASES[name]
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(
        **{handler: lambda *args: json.dumps(payload)}))


@pytest.mark.parametrize("name,field", [(name, key) for name, (_, payload) in CASES.items() for key in payload])
def test_missing_required_result_field_raises(monkeypatch, tree, name, field):
    payload = dict(CASES[name][1])
    del payload[field]
    backend_payload(monkeypatch, name, payload)
    with pytest.raises(RuntimeError):
        invoke(name, tree)


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("payload", [None, [], {"error": "engine failed"}])
def test_non_result_envelope_raises(monkeypatch, tree, name, payload):
    backend_payload(monkeypatch, name, payload)
    with pytest.raises(RuntimeError):
        invoke(name, tree)


@pytest.mark.parametrize("name", CASES)
def test_explicit_valid_empty_result_is_preserved(monkeypatch, tree, name):
    backend_payload(monkeypatch, name, CASES[name][1])
    result = invoke(name, tree)
    if name == "invariance":
        assert result.changes == []
        assert result.change_groups == []
    elif name == "generic":
        assert result == ([], [])
    elif name == "markdown":
        assert result["moves"] == result["renames"] == []
    else:
        assert result["changes"] == result["change_groups"] == []
        assert result["is_style_only"] is True
        assert result["no_surviving_changes"] is True


@pytest.mark.parametrize("name", ["generic", "markdown", "finalize"])
def test_explicit_decline_remains_a_routing_result(monkeypatch, tree, name):
    backend_payload(monkeypatch, name, {"used": False, "reason": "input too large"})
    assert invoke(name, tree) is None


@pytest.mark.parametrize("name,field,value", [
    ("generic", "changes", None), ("generic", "changes", {}),
    ("generic", "used", "false"), ("finalize", "is_style_only", "false"),
    ("invariance", "ignored_style_changes", [42]),
    ("finalize", "trace", [42]), ("markdown", "moved_labels", "name"),
])
def test_wrong_field_types_raise(monkeypatch, tree, name, field, value):
    payload = dict(CASES[name][1]); payload[field] = value
    backend_payload(monkeypatch, name, payload)
    with pytest.raises(RuntimeError):
        invoke(name, tree)


@pytest.mark.parametrize("operation,failure", [(operation, failure)
    for operation in ["facts", "xml", "profiles"]
    for failure in ["missing", "engine", "malformed"]
    if (operation, failure) != ("xml", "malformed")])
def test_required_enrichment_failure_propagates(monkeypatch, tree, operation, failure):
    handler = {"facts": "enrich_node_facts_json", "xml": "register_user_xml_dialects_json",
               "profiles": "enrich_profile_labels_json"}[operation]
    def fail(*args):
        if failure == "engine":
            raise RuntimeError("engine failure")
        return "{}"
    monkeypatch.setattr(rust_core, "_registered_xml_dialects_fingerprint", None)
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(
        **({} if failure == "missing" else {handler: fail})))
    with pytest.raises(RuntimeError):
        if operation == "facts": rust_core.enrich_node_facts(tree)
        elif operation == "xml": rust_core.try_register_user_xml_dialects([])
        else: rust_core.try_rust_profile_label_enrichment(tree, "x", "json")


@pytest.mark.parametrize("payload", [None, [], {}, {"scope_trails": None},
    {"scope_trails": {}}, {"scope_trails": {"old": [], "new": [42]}}])
def test_scope_trails_cannot_manufacture_empty_metadata(monkeypatch, tree, payload):
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(
        scope_trails_json=lambda *args: json.dumps(payload)))
    with pytest.raises(RuntimeError):
        rust_core.build_scope_trails(old_tree=tree, new_tree=tree, changes=[])


def test_scope_trails_preserves_explicit_empty_sides(monkeypatch, tree):
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(
        scope_trails_json=lambda *args: '{"scope_trails":{"old":[],"new":[]}}'))
    assert rust_core.build_scope_trails(old_tree=tree, new_tree=tree, changes=[]) == {"old": [], "new": []}


@pytest.mark.parametrize("mode", ["apply", "style_only", "zero_change_literal"])
def test_compatibility_invariance_helpers_require_rust(monkeypatch, tree, mode):
    from intentumdiff.analysis import invariances
    def failed_backend():
        raise RuntimeError("engine unavailable")
    monkeypatch.setattr(rust_core, "_load_backend", failed_backend)
    kwargs = dict(old_source="x", new_source="x", language="python")
    with pytest.raises(RuntimeError, match="engine unavailable"):
        if mode == "style_only":
            invariances.build_style_only_evidence(**kwargs)
        elif mode == "zero_change_literal":
            invariances.build_zero_change_literal_evidence(old_tree=tree, new_tree=tree, **kwargs)
        else:
            invariances.apply_invariances([], old_tree=tree, new_tree=tree, **kwargs)


@pytest.mark.parametrize("template,equivalent", [
    ("a { color: VALUE; }", True),
    ("a { --theme: (x; color: VALUE;); }", False),
    ("a { --theme: [x; color: VALUE;]; }", False),
    ('a { content: "VALUE"; }', False),
])
def test_compatibility_invariances_preserve_css_data(tree, template, equivalent):
    from intentumdiff.analysis.invariances import apply_invariances
    old_source, new_source = template.replace("VALUE", "red"), template.replace("VALUE", "#f00")
    old_tree = tree.model_copy(update={"node_type": "stylesheet", "label": old_source})
    new_tree = tree.model_copy(update={"node_type": "stylesheet", "label": new_source})
    changes = [Change(change_type=ChangeType.MODIFICATION, old_node=old_tree, new_node=new_tree)]
    result = apply_invariances(changes, old_tree=old_tree, new_tree=new_tree,
        old_source=old_source, new_source=new_source, language="css")
    if equivalent:
        assert result.changes == []
        assert result.change_groups
    else:
        assert result.changes == changes
        assert result.ignored_style_changes == []
