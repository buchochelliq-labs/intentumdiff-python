"""Rust-owned incompleteness must be forwarded by the Python live transport.

These tests intentionally mock the Rust ABI *response*, not any semantic
classifier. They reject Python recovery after an explicitly typed Rust decline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from intentumdiff.core.models import SemanticDiff
from intentumdiff.live_server import LiveServer, _ParsedRequest, _RustLiveDecline
from intentumdiff.sources.live_buffer_source import LiveBufferSource


def _server(tmp_path):
    differ = MagicMock()
    differ._config.diagnostics = False
    differ._config.plugin_fuel = 100_000_000
    differ._config.model_dump_json.return_value = "{}"
    differ._config.model_copy.return_value = differ._config
    return LiveServer(differ, repo_path=tmp_path, debounce=0), differ


def _error(seq, code, message, op):
    return {"op": op, "seq": seq, "ok": False, "error": {"code": code, "message": message}}


@pytest.mark.parametrize("name,content", [
    ("edit.py", "def g("),
    ("edit.js", "function g("),
])
@pytest.mark.parametrize("stream", [False, True])
def test_typed_rust_direct_diff_decline_never_falls_back_to_python(
    tmp_path, name, content, stream,
):
    server, differ = _server(tmp_path)
    request = _ParsedRequest(
        op="diff", seq=17, path=name, content=content, ref="HEAD",
        edit_deltas=None, stream=stream,
    )
    source = LiveBufferSource(tmp_path, name, content, ref="HEAD")
    sent = []
    rust_decline = {
        "fallback_code": "native_parse_incomplete",
        "fallback": f"{name}: Incomplete or invalid syntax",
    }
    with patch("intentumdiff.rust_core.live_handle_diff", return_value=rust_decline), (
        patch("intentumdiff.rust_core.live_error_response", side_effect=_error)
    ):
        server._process_request_with_source(source, request, sent.append)

    assert sent == [_error(17, rust_decline["fallback_code"],
                           rust_decline["fallback"], "diff")]
    differ.diff.assert_not_called()


def test_valid_rust_diff_remains_successful_without_python_recomputation(tmp_path):
    server, differ = _server(tmp_path)
    request = _ParsedRequest(
        op="diff", seq=18, path="edit.py", content="def g():\n    return 2\n",
        ref="HEAD", edit_deltas=None, stream=False,
    )
    source = LiveBufferSource(tmp_path, "edit.py", request.content, ref="HEAD")
    valid = SemanticDiff(
        old_filename="edit.py", new_filename="edit.py", language="python",
        is_fallback=False, parse_errors=[],
    ).model_dump(mode="json")
    sent = []
    with patch("intentumdiff.rust_core.live_handle_diff", return_value={"diff": valid}):
        server._process_request_with_source(source, request, sent.append)

    assert len(sent) == 1 and sent[0]["op"] == "diff" and sent[0]["ok"] is True
    assert sent[0]["diff"]["is_fallback"] is False
    assert sent[0]["diff"]["parse_errors"] == []
    differ.diff.assert_not_called()


def test_valid_streaming_emits_only_rust_certified_final_changes(tmp_path):
    from intentumdiff.core.models import Change, ChangeType

    server, differ = _server(tmp_path)
    request = _ParsedRequest(
        op="diff", seq=20, path="edit.py", content="def g():\n    return 2\n",
        ref="HEAD", edit_deltas=None, stream=True,
    )
    source = LiveBufferSource(tmp_path, "edit.py", request.content, ref="HEAD")
    valid = SemanticDiff(
        old_filename="edit.py", new_filename="edit.py", language="python",
        is_fallback=False, parse_errors=[], has_semantic_changes=True,
        changes=[Change(change_type=ChangeType.MODIFICATION, description="real edit")],
    ).model_dump(mode="json")
    sent = []
    with patch("intentumdiff.rust_core.live_handle_diff", return_value={"diff": valid}):
        server._process_request_with_source(source, request, sent.append)

    assert len(sent) == 2
    assert sent[0]["op"] == "diff" and sent[0]["ok"] is True
    assert sent[0]["event"]["phase"] == 3  # FINAL, not a Python synthetic stage
    assert sent[0]["event"]["change"]["change_type"] == "MODIFICATION"
    assert sent[1] == {"op": "diff", "seq": 20, "ok": True, "done": True}
    differ.diff.assert_not_called()
    differ.diff_stream_progressive.assert_not_called()


def test_valid_unchanged_stream_has_only_successful_done(tmp_path):
    server, differ = _server(tmp_path)
    request = _ParsedRequest(
        op="diff", seq=21, path="edit.py", content="x = 1\n",
        ref="HEAD", edit_deltas=None, stream=True,
    )
    source = LiveBufferSource(tmp_path, "edit.py", request.content, ref="HEAD")
    valid = SemanticDiff(
        old_filename="edit.py", new_filename="edit.py", language="python",
        is_fallback=False, parse_errors=[],
    ).model_dump(mode="json")
    sent = []
    with patch("intentumdiff.rust_core.live_handle_diff", return_value={"diff": valid}):
        server._process_request_with_source(source, request, sent.append)

    assert sent == [{"op": "diff", "seq": 21, "ok": True, "done": True}]
    differ.diff.assert_not_called()
    differ.diff_stream_progressive.assert_not_called()


def test_typed_rust_commit_decline_propagates_without_python_commit_fallback(tmp_path):
    server, _ = _server(tmp_path)
    commit_differ = MagicMock()
    decline = {
        "fallback_code": "native_parse_incomplete",
        "fallback": "broken.js: Invalid syntax in commit",
    }
    with patch("intentumdiff.rust_core.live_handle_review", return_value=decline):
        with pytest.raises(_RustLiveDecline) as exc:
            server._compute_review(server._differ._config, commit_differ, "HEAD~1", "HEAD")
    assert exc.value.code == decline["fallback_code"]
    assert str(exc.value) == decline["fallback"]
    commit_differ.diff_commit.assert_not_called()


def test_typed_rust_commit_decline_reports_typed_review_error(tmp_path):
    server, _ = _server(tmp_path)
    decline = {
        "fallback_code": "native_parse_incomplete",
        "fallback": "broken.js: Invalid syntax in commit",
    }
    sent = []
    with patch("intentumdiff.rust_core.live_parse_review_request",
               return_value={"parsed": {"old_ref": "HEAD~1", "new_ref": "HEAD",
                                         "stream": False}}), (
        patch("intentumdiff.core.commit_differ.CommitDiffer") as differ_cls
    ), (
        patch("intentumdiff.rust_core.live_handle_review", return_value=decline)
    ), (
        patch("intentumdiff.rust_core.live_error_response", side_effect=_error)
    ):
        server._process_review_request({"op": "review"}, 19, sent.append)

    assert sent == [_error(19, decline["fallback_code"], decline["fallback"], "review")]
    differ_cls.return_value.diff_commit.assert_not_called()
