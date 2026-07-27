"""
tests/unit/test_models.py — unit tests for core Pydantic models.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intentdiff.core.models import (
    Change,
    ChangeGroup,
    ChangeGroupKind,
    ChangeType,
    DiffConfig,
    GuardrailCheckResult,
    GuardrailSeverity,
    GuardrailViolation,
    NodePosition,
    ReferenceKind,
    ReferenceUsage,
    SemanticDiff,
    SymbolDefinition,
)


class TestNodePosition:
    def test_valid(self):
        p = NodePosition(start_line=0, start_col=0, end_line=1, end_col=5)
        assert p.start_line == 0

    def test_same_line_valid(self):
        NodePosition(start_line=5, start_col=0, end_line=5, end_col=10)

    def test_end_before_start_raises(self):
        with pytest.raises(ValidationError):
            NodePosition(start_line=5, start_col=0, end_line=4, end_col=0)


class TestSemanticNode:
    def test_leaf_node(self, make_node):
        n = make_node("identifier", "foo", id="1")
        assert n.is_leaf()
        assert n.height() == 0

    def test_internal_node_height(self, make_node):
        child = make_node(id="1")
        parent = make_node("module", id="0", children=[child])
        assert not parent.is_leaf()
        assert parent.height() == 1

    def test_descendants(self, make_node):
        grandchild = make_node(id="2")
        child = make_node(id="1", children=[grandchild])
        root = make_node(id="0", children=[child])
        descs = root.descendants()
        assert len(descs) == 2
        assert descs[0].id == "1"
        assert descs[1].id == "2"


class TestChange:
    def test_addition_no_old_node(self, make_node):
        n = make_node(id="1")
        c = Change(change_type=ChangeType.ADDITION, new_node=n)
        assert c.old_node is None

    def test_deletion_no_new_node(self, make_node):
        n = make_node(id="1")
        c = Change(change_type=ChangeType.DELETION, old_node=n)
        assert c.new_node is None

    def test_addition_with_old_node_raises(self, make_node):
        n = make_node(id="1")
        with pytest.raises(ValidationError):
            Change(change_type=ChangeType.ADDITION, old_node=n, new_node=n)

    def test_refactoring_without_kind_raises(self, make_node):
        n = make_node(id="1")
        with pytest.raises(ValidationError):
            Change(change_type=ChangeType.REFACTORING, new_node=n)


class TestSemanticDiff:
    def test_style_only_constructor(self):
        d = SemanticDiff.style_only("a.py", "b.py", "python")
        assert d.is_style_only
        assert not d.has_semantic_changes
        assert d.changes == []
        assert d.change_groups == []

    def test_style_only_constructor_accepts_evidence(self):
        group = ChangeGroup(
            kind=ChangeGroupKind.IGNORED_STYLE,
            rule_id="test.style",
            metadata={"reason": "ignored"},
        )
        d = SemanticDiff.style_only(
            "a.py",
            "b.py",
            "python",
            change_groups=[group],
            metadata={"ignored_style_changes": [{"rule_id": "test.style"}]},
        )

        assert d.is_style_only
        assert d.change_groups == [group]
        assert d.metadata["ignored_style_changes"][0]["rule_id"] == "test.style"

    def test_style_only_metadata_is_immutable_copy_and_json_safe(self):
        metadata = {"ignored_style_changes": [{"rule_id": "test.style"}]}
        d = SemanticDiff.style_only(
            "a.py",
            "b.py",
            "python",
            metadata=metadata,
        )

        metadata["ignored_style_changes"] = []

        assert d.metadata["ignored_style_changes"][0]["rule_id"] == "test.style"
        with pytest.raises(TypeError):
            d.metadata["extra"] = "blocked"
        assert d.model_dump(mode="json")["metadata"] == {
            "ignored_style_changes": [{"rule_id": "test.style"}],
        }

    def test_style_only_with_semantic_changes_raises(self):
        with pytest.raises(ValidationError):
            SemanticDiff(
                old_filename="a.py",
                new_filename="b.py",
                language="python",
                is_style_only=True,
                has_semantic_changes=True,
            )

    def test_model_json_round_trip(self):
        d = SemanticDiff.style_only("a.py", "b.py", "python")
        restored = SemanticDiff.model_validate_json(d.model_dump_json())
        assert restored == d

    def test_change_groups_round_trip(self, make_node):
        old = make_node("function_definition", "helper", id="old")
        new = make_node("function_definition", "helper", id="new")
        group = ChangeGroup(
            kind=ChangeGroupKind.MOVED_CODE,
            raw_change_indices=[0],
            old_labels=["helper"],
            new_labels=["helper"],
            old_node_ids=[old.id],
            new_node_ids=[new.id],
            confidence=0.9,
            rule_id="test.move",
            metadata={"note": "evidence"},
        )
        diff = SemanticDiff(
            old_filename="a.py",
            new_filename="b.py",
            language="python",
            changes=[Change(change_type=ChangeType.MOVE, old_node=old, new_node=new)],
            change_groups=[group],
            has_semantic_changes=True,
        )

        restored = SemanticDiff.model_validate_json(diff.model_dump_json())

        assert restored.change_groups == [group]
        assert restored.change_groups[0].kind == ChangeGroupKind.MOVED_CODE
        assert restored.change_groups[0].metadata["note"] == "evidence"

    def test_metadata_is_immutable_copy_and_json_safe(self):
        metadata = {"diagnostics": {"version": 2}}
        diff = SemanticDiff(
            old_filename="a.py",
            new_filename="b.py",
            language="python",
            metadata=metadata,
        )

        metadata["diagnostics"] = {"version": 99}

        assert diff.metadata["diagnostics"]["version"] == 2
        with pytest.raises(TypeError):
            diff.metadata["extra"] = "blocked"
        assert diff.model_dump(mode="json")["metadata"] == {
            "diagnostics": {"version": 2},
        }

    def test_change_group_metadata_is_immutable_copy_and_json_safe(self):
        metadata = {"reason": "ignored"}
        group = ChangeGroup(
            kind=ChangeGroupKind.IGNORED_STYLE,
            rule_id="test.style",
            metadata=metadata,
        )

        metadata["reason"] = "changed"

        assert group.metadata["reason"] == "ignored"
        with pytest.raises(TypeError):
            group.metadata["extra"] = "blocked"
        assert group.model_dump(mode="json")["metadata"] == {"reason": "ignored"}

    def test_guardrail_violations_round_trip(self):
        violation = GuardrailViolation(
            rule_id="prod-host",
            severity=GuardrailSeverity.IMMUTABLE,
            file="config.yaml",
            language="yaml",
            semantic_path="server.host",
            old_value="localhost",
            new_value="prod.example.com",
            position=NodePosition(
                start_line=4,
                start_col=2,
                end_line=4,
                end_col=18,
            ),
            message="Production host changed",
        )
        diff = SemanticDiff(
            old_filename="config.yaml",
            new_filename="config.yaml",
            language="yaml",
            guardrail_violations=[violation],
        )

        restored = SemanticDiff.model_validate_json(diff.model_dump_json())

        assert restored.guardrail_violations == [violation]
        assert restored.guardrail_violations[0].severity == GuardrailSeverity.IMMUTABLE
        assert restored.guardrail_violations[0].position == violation.position

    def test_guardrail_check_result_serializes_counts_and_metadata(self):
        violation = GuardrailViolation(
            rule_id="prod-host",
            severity=GuardrailSeverity.IMMUTABLE,
            file="config.yaml",
            language="yaml",
            semantic_path="server.host",
        )
        metadata = {"policy": "intentdiff.yaml"}
        result = GuardrailCheckResult(
            violations=[violation],
            violation_count=1,
            immutable_count=1,
            checked_files=1,
            strict=True,
            passed=False,
            metadata=metadata,
        )

        metadata["policy"] = "changed"

        assert result.metadata["policy"] == "intentdiff.yaml"
        assert result.model_dump(mode="json")["metadata"] == {"policy": "intentdiff.yaml"}

    def test_guardrail_check_result_derives_counts_from_violations(self):
        immutable = GuardrailViolation(
            rule_id="prod-host",
            severity=GuardrailSeverity.IMMUTABLE,
            file="config.yaml",
            language="yaml",
            semantic_path="server.host",
        )
        important = immutable.model_copy(
            update={
                "rule_id": "entrypoint",
                "severity": GuardrailSeverity.IMPORTANT,
                "semantic_path": "main",
            }
        )

        result = GuardrailCheckResult(
            violations=[immutable, important],
            checked_files=1,
            strict=True,
        )

        assert result.violation_count == 2
        assert result.immutable_count == 1
        assert result.important_count == 1
        assert result.passed is False


class TestDiffConfig:
    def test_defaults(self):
        cfg = DiffConfig()
        assert cfg.min_similarity == 0.5
        assert cfg.plugin_fuel == 100_000_000
        assert cfg.max_plugin_output_bytes == 16 * 1024 * 1024
        assert cfg.detect_refactorings is True

    def test_mutation_allowed(self):
        cfg = DiffConfig()
        cfg.min_similarity = 0.8
        assert cfg.min_similarity == 0.8

    def test_fuel_must_be_positive(self):
        with pytest.raises(ValidationError):
            DiffConfig(plugin_fuel=0)

    def test_fuel_unlimited_accepted(self):
        from intentdiff.core.models import FUEL_UNLIMITED
        cfg = DiffConfig(plugin_fuel=FUEL_UNLIMITED)
        assert cfg.plugin_fuel == -1

    def test_fuel_negative_non_sentinel_rejected(self):
        with pytest.raises(ValidationError):
            DiffConfig(plugin_fuel=-2)

    def test_resolve_references_default_false(self):
        cfg = DiffConfig()
        assert cfg.resolve_references is False

    def test_guardrails_default_enabled(self):
        cfg = DiffConfig()
        assert cfg.guardrails_enabled is True
        assert cfg.guardrails_strict is False

    def test_resolve_references_settable(self):
        cfg = DiffConfig(resolve_references=True)
        assert cfg.resolve_references is True
        cfg.resolve_references = False
        assert cfg.resolve_references is False


def _make_position(**kwargs) -> NodePosition:
    defaults = {"start_line": 0, "start_col": 0, "end_line": 1, "end_col": 0}
    defaults.update(kwargs)
    return NodePosition(**defaults)


def _make_symbol(name: str = "foo", file: str = "a.py") -> SymbolDefinition:
    return SymbolDefinition(
        qualified_name=name,
        file=file,
        node_type="function_definition",
        node_id="1",
        start_line=0,
        start_col=0,
        end_line=5,
        end_col=0,
        language="python",
    )


class TestReferenceKind:
    def test_values(self):
        assert ReferenceKind.CALL == "CALL"
        assert ReferenceKind.IMPORT == "IMPORT"
        assert ReferenceKind.TYPE_USAGE == "TYPE_USAGE"


class TestReferenceUsage:
    def test_minimal_fields(self):
        ref = ReferenceUsage(
            qualified_name="do_work",
            file="a.py",
            node_id="5",
            reference_kind=ReferenceKind.CALL,
            position=_make_position(),
        )
        assert ref.qualified_name == "do_work"
        assert ref.reference_kind == ReferenceKind.CALL
        assert ref.enclosing_scope is None
        assert ref.resolved_definition is None
        assert ref.language == ""

    def test_with_enclosing_scope(self):
        ref = ReferenceUsage(
            qualified_name="helper",
            file="a.py",
            node_id="7",
            reference_kind=ReferenceKind.CALL,
            position=_make_position(),
            enclosing_scope="MyClass.method",
        )
        assert ref.enclosing_scope == "MyClass.method"

    def test_with_resolved_definition(self):
        sym = _make_symbol("helper")
        ref = ReferenceUsage(
            qualified_name="helper",
            file="a.py",
            node_id="7",
            reference_kind=ReferenceKind.CALL,
            position=_make_position(),
            resolved_definition=sym,
        )
        assert ref.resolved_definition is sym
        assert ref.resolved_definition.qualified_name == "helper"

    def test_frozen(self):
        ref = ReferenceUsage(
            qualified_name="foo",
            file="a.py",
            node_id="1",
            reference_kind=ReferenceKind.IMPORT,
            position=_make_position(),
        )
        with pytest.raises(Exception):  # frozen model raises on attribute set
            ref.qualified_name = "bar"  # type: ignore[misc]

    def test_model_copy_update_resolved(self):
        """model_copy(update=...) works on frozen ReferenceUsage (used in resolve path)."""
        ref = ReferenceUsage(
            qualified_name="foo",
            file="a.py",
            node_id="1",
            reference_kind=ReferenceKind.CALL,
            position=_make_position(),
        )
        sym = _make_symbol("foo")
        resolved = ref.model_copy(update={"resolved_definition": sym})
        assert resolved.resolved_definition is sym
        assert ref.resolved_definition is None  # original unchanged

    def test_qualified_name_empty_raises(self):
        with pytest.raises(ValidationError):
            ReferenceUsage(
                qualified_name="",
                file="a.py",
                node_id="1",
                reference_kind=ReferenceKind.CALL,
                position=_make_position(),
            )

    def test_json_round_trip(self):
        ref = ReferenceUsage(
            qualified_name="do_work",
            file="a.py",
            node_id="5",
            reference_kind=ReferenceKind.CALL,
            position=_make_position(),
            enclosing_scope="my_func",
        )
        restored = ReferenceUsage.model_validate_json(ref.model_dump_json())
        assert restored == ref
