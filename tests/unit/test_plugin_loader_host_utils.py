from __future__ import annotations

import json
import logging

import pytest

from intentdiff.plugins import loader


def test_structural_hash_valid_json_still_works() -> None:
    payload = json.dumps({
        "type": "root",
        "children": [
            {"type": "identifier", "text": "x"},
            {"type": "literal", "text": "1"},
        ],
    })

    digest = loader._structural_hash_impl(payload)

    assert len(digest) == 64


def test_strip_trivia_valid_json_still_works() -> None:
    payload = json.dumps({
        "type": "root",
        "children": [
            {"type": "comment", "text": "# ignored"},
            {"type": "identifier", "text": "x"},
        ],
    })

    stripped = json.loads(loader._strip_trivia_impl(payload, ["comment"]))

    assert [child["type"] for child in stripped["children"]] == ["identifier"]


def test_host_utils_reject_oversized_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_HOST_UTILS_MAX_JSON_BYTES", 20)

    with pytest.raises(ValueError, match="payload"):
        loader._structural_hash_impl('{"type":"root","text":"' + ("x" * 64) + '"}')


def test_host_utils_reject_over_deep_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_HOST_UTILS_MAX_JSON_DEPTH", 4)

    with pytest.raises(ValueError, match="nesting depth"):
        loader._structural_hash_impl('{"type":"a","children":[{"type":"b","children":[{"type":"c"}]}]}')


def test_host_utils_reject_excessive_node_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "_HOST_UTILS_MAX_JSON_NODES", 5)
    payload = json.dumps({
        "type": "root",
        "children": [{"type": f"child-{i}", "text": "x"} for i in range(10)],
    })

    with pytest.raises(ValueError, match="node count"):
        loader._strip_trivia_impl(payload, [])


def test_host_utils_reject_excessive_trivia_type_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader, "_HOST_UTILS_MAX_TRIVIA_TYPES", 2)
    payload = json.dumps({"type": "root", "children": []})

    with pytest.raises(ValueError, match="trivia type count"):
        loader._strip_trivia_impl(payload, ["comment", "whitespace", "newline"])


def test_host_utils_reject_excessive_trivia_type_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader, "_HOST_UTILS_MAX_TRIVIA_TYPE_BYTES", 4)
    payload = json.dumps({"type": "root", "children": []})

    with pytest.raises(ValueError, match="trivia type"):
        loader._strip_trivia_impl(payload, ["comment"])


def test_plugin_log_message_is_truncated(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(loader, "_HOST_UTILS_MAX_LOG_BYTES", 32)

    loader._log_impl("info", "x" * 1_000)

    assert "[truncated]" in caplog.text
    assert "x" * 100 not in caplog.text


def test_host_utils_reject_malformed_json() -> None:
    with pytest.raises(ValueError, match="invalid JSON"):
        loader._structural_hash_impl('{"type":')


def test_plugin_output_text_limit_rejects_oversized_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loader, "_MAX_PLUGIN_OUTPUT_BYTES", 8)

    with pytest.raises(loader.PluginOutputError, match="output"):
        loader._bounded_plugin_text("plugin.wasm", "process", "x" * 32)


def test_structural_hash_reads_semantic_node_key_spellings() -> None:
    """#49 item 2 (latent bug): FullParse SemanticNode trees carry node_type/label,
    not type/text — the old reads hashed them as all-blank SHAPE, so two trees with
    different labels collided."""
    a = loader._structural_hash_impl('{"node_type":"value","label":"42"}')
    b = loader._structural_hash_impl('{"node_type":"value","label":"43"}')
    assert a != b, "label must participate in the hash"
    # And the spellings are equivalent where they name the same content.
    cst = loader._structural_hash_impl('{"type":"value","text":"42"}')
    assert a == cst, "node_type/label and type/text must hash identically"
