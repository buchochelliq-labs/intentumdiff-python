"""Public-wrapper evidence for semantics retired from active Python processing."""
import pytest

from intentumdiff.core.models import DiffConfig, NodePosition, SemanticNode
from intentumdiff.differ import SemanticDiffer
from intentumdiff.rust_core import enrich_literal_labels, review_trees_equivalent


def literal(kind, label, start=0, end=0):
    return SemanticNode(id="0", node_type=kind, label=label, structural_hash="test",
                        position=NodePosition(start_line=0, start_col=start, end_line=0, end_col=end))


@pytest.mark.parametrize("kind", ["string", "string_literal", "character_literal", "char_literal"])
@pytest.mark.parametrize("old,new", [("a b", "a  b"), (" ", "\t"), ("x", " x ")])
def test_literal_whitespace_is_data(kind, old, new):
    assert not review_trees_equivalent(literal(kind, old), literal(kind, new))


@pytest.mark.parametrize("prefix", ['é="x"; b=', 'a="\u2028"; b=', 'b='])
def test_enrichment_uses_parser_byte_columns_and_physical_lines(prefix):
    source = prefix + '" target "\r\n'
    start = len(prefix.encode("utf-8"))
    result = enrich_literal_labels(literal("string", "string", start, start + 10), source)
    assert result.label == " target "


@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("prefix", ['é="x"; b=', 'a="\u2028"; b='])
def test_real_parser_literal_labels_after_unicode(diagnostics, prefix):
    differ = SemanticDiffer(DiffConfig(diagnostics=diagnostics))
    try:
        result = differ.diff_strings(prefix + '" target "\n', prefix + '"target"\n', "example.py")
    finally:
        differ.close()
    assert any(c.change_type == "MODIFICATION" and c.old_node and c.new_node
               and (c.old_node.label, c.new_node.label) in {
                   (" target ", "target"), ('" target "', '"target"')}
               for c in result.changes)
