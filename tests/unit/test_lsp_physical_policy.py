from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest
from intentumdiff.lsp_server import create_server


def invoke(root, old, new):
    server = create_server()
    handler = server.protocol.fm._features["intentumdiff/semanticDiff"]
    if isinstance(handler, list):
        handler = handler[0]
    with patch.object(type(server), "workspace", new_callable=PropertyMock,
                      return_value=SimpleNamespace(root_uri=root.as_uri())):
        return handler({"oldUri": old.as_uri(), "newUri": new.as_uri()})


def files(root):
    old = root / "before" / "config.json"
    new = root / "after" / "config.json"
    old.parent.mkdir()
    new.parent.mkdir()
    old.write_text('{"version":1}', encoding="utf-8")
    new.write_text('{"version":2}', encoding="utf-8")
    return old, new


def policy(path, rule):
    path.write_text(
        "guardrails:\n  protected:\n    - id: " + rule +
        "\n      language: json\n      path: version\n"
        "      severity: immutable\n      files: [config.json]\n", encoding="utf-8"
    )


@pytest.mark.parametrize("sides", [("before",), ("after",), ("before", "after")])
def test_two_uri_diff_enforces_physical_policies(tmp_path, sides):
    old, new = files(tmp_path)
    for side in sides:
        policy(tmp_path / side / "intentumdiff.yaml", side + ".version")
    result = invoke(tmp_path, old, new)
    assert "error" not in result, result
    assert result["has_semantic_changes"]
    assert {v["rule_id"] for v in result["guardrail_violations"]} == {
        side + ".version" for side in sides
    }


@pytest.mark.parametrize("side", ["before", "after"])
def test_two_uri_diff_reports_broken_policy(tmp_path, side):
    old, new = files(tmp_path)
    (tmp_path / side / "intentumdiff.yaml").write_text("guardrails: [broken", encoding="utf-8")
    result = invoke(tmp_path, old, new)
    assert "error" in result
    assert "has_semantic_changes" not in result


def test_two_uri_diff_without_policy_keeps_literal_edit(tmp_path):
    old, new = files(tmp_path)
    result = invoke(tmp_path, old, new)
    assert "error" not in result, result
    assert result["has_semantic_changes"] and not result["is_style_only"]
    assert result["guardrail_violations"] == []

