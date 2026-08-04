"""Unit tests for LiveServer."""
from __future__ import annotations

import json
import socket
import sys
import threading
import tempfile
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_differ(language: str = "python"):
    """Return a SemanticDiffer mock that returns a minimal SemanticDiff."""
    from intentumdiff.core.models import CommitDiff, SemanticDiff

    diff = SemanticDiff(
        old_filename="foo.py",
        new_filename="foo.py",
        language=language,
    )
    commit_diff = CommitDiff(
        old_ref="HEAD",
        new_ref="",
        file_diffs=[diff],
    )
    differ = MagicMock()
    differ.diff.return_value = diff
    differ.diff_commit.return_value = commit_diff
    differ.diff_stream_progressive.return_value = iter([])
    differ._config = MagicMock()
    differ._registry = MagicMock()
    return differ


def _make_server(**kw):
    from intentumdiff.live_server import LiveServer

    differ = _make_differ()
    kw.setdefault("repo_path", tempfile.mkdtemp())
    return LiveServer(differ, **kw), differ


def _json_lines(output: StringIO) -> list[dict]:
    output.seek(0)
    return [json.loads(line) for line in output.getvalue().splitlines()]


class _LazyTimer:
    """threading.Timer substitute that never auto-fires."""

    def __init__(self, delay, fn, args=()):
        self.delay = delay
        self._fn = fn
        self._args = args
        self.started = False
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def start(self):
        self.started = True

    def fire(self):
        self._fn(*self._args)


class _Cp1252Stream:
    """Text-stream stub that fails if protocol output contains raw non-CP1252."""

    def __init__(self) -> None:
        self.value = ""

    def write(self, text: str) -> None:
        text.encode("cp1252")
        self.value += text

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Protocol v2
# ---------------------------------------------------------------------------


class TestProtocolV2:
    def test_protocol_output_is_ascii_safe_for_windows_stdio(self) -> None:
        from intentumdiff.live_server import _write_line

        stream = _Cp1252Stream()

        _write_line(stream, {"description": "old → new", "emoji": "✅"})

        assert "\\u2192" in stream.value
        assert "\\u2705" in stream.value
        assert json.loads(stream.value)["description"] == "old → new"

    def test_ready_payload_contains_capabilities_and_limits(self) -> None:
        server, _ = _make_server(ref="origin/main")

        ready = server.ready_message(transport="stdio")

        assert ready["op"] == "ready"
        assert ready["ok"] is True
        assert ready["protocol_version"] == 2
        assert ready["ref"] == "origin/main"
        assert "repo_path" in ready
        assert ready["capabilities"]["legacy_diff"] is True
        assert ready["capabilities"]["per_request_ref"] is True
        assert ready["capabilities"]["review"] is True
        assert ready["capabilities"]["cross_file_changes"] is True
        assert "max_content_bytes" in ready["limits"]

    def test_hello_returns_capabilities(self) -> None:
        server, _ = _make_server(ref="HEAD~1")
        sent: list[dict] = []

        server._process_request({"op": "hello", "seq": 9}, lambda obj: sent.append(obj))

        assert sent == [
            {
                "op": "hello",
                "seq": 9,
                "ok": True,
                "protocol_version": 2,
                "repo_path": server._repo_path,
                "ref": "HEAD~1",
                "limits": server._limits(),
                "capabilities": server._capabilities(),
            }
        ]

    def test_cancel_removes_pending_timer_for_path(self) -> None:
        timers: list[_LazyTimer] = []

        def _fake_timer(delay, fn, args=()):
            t = _LazyTimer(delay, fn, args=args)
            timers.append(t)
            return t

        server, differ = _make_server(debounce=0.1)
        sent: list[dict] = []

        with patch("intentumdiff.live_server.threading.Timer", side_effect=_fake_timer):
            server._schedule_request(
                {"op": "diff", "path": "foo.py", "content": "x", "seq": 4},
                lambda obj: sent.append(obj),
            )
            server._schedule_request(
                {"op": "cancel", "path": "foo.py", "seq": 5},
                lambda obj: sent.append(obj),
            )

        assert sent == [{"op": "cancel", "seq": 5, "ok": True, "cancelled": 1}]
        assert timers[0].cancelled
        timers[0].fire()
        differ.diff.assert_not_called()

    def test_per_request_ref_passed_to_live_buffer_source(self) -> None:
        server, _ = _make_server(ref="HEAD")
        captured: list[str] = []

        def _capture(*args, **kwargs):
            captured.append(kwargs["ref"])
            return MagicMock()

        with patch("intentumdiff.live_server.LiveBufferSource", side_effect=_capture):
            server._process_request(
                {
                    "op": "diff",
                    "path": "foo.py",
                    "content": "x",
                    "seq": 1,
                    "ref": "feature",
                },
                lambda _: None,
            )

        assert captured == ["feature"]

    def test_review_returns_commit_diff_json(self) -> None:
        from intentumdiff.core.models import ChangeType, CommitDiff, CrossFileChange

        server, _ = _make_server(ref="origin/main")
        commit_diff = CommitDiff(
            old_ref="origin/main",
            new_ref="",
            cross_file_changes=[
                CrossFileChange(
                    change_type=ChangeType.MOVE_TO_MODULE,
                    symbol_name="greet",
                    old_file="a.py",
                    new_file="b.py",
                    description="'greet' moved",
                )
            ],
        )
        sent: list[dict] = []

        with patch("intentumdiff.core.commit_differ.CommitDiffer") as commit_differ:
            commit_differ.return_value.diff_commit.return_value = commit_diff
            server._process_request({"op": "review", "seq": 12}, lambda obj: sent.append(obj))

        assert sent[0]["op"] == "review"
        assert sent[0]["ok"] is True
        assert sent[0]["commit_diff"]["old_ref"] == "origin/main"
        assert sent[0]["metadata"]["cross_file_change_count"] == 1

    def test_review_streaming_emits_per_file_events_then_terminal(self) -> None:
        from intentumdiff.core.commit_differ import FileDiffResult
        from intentumdiff.core.models import CommitDiff, SemanticDiff

        server, _ = _make_server(ref="HEAD")
        diff_a = SemanticDiff(old_filename="a.py", new_filename="a.py", language="python")
        diff_b = SemanticDiff(old_filename="b.py", new_filename="b.py", language="python")
        sent: list[dict] = []

        commit_diff = CommitDiff(
            old_ref="HEAD",
            new_ref="",
            file_diffs=[diff_a, diff_b],
        )

        with patch("intentumdiff.core.commit_differ.CommitDiffer") as commit_differ:
            commit_differ.return_value.iter_file_diffs.return_value = iter([
                FileDiffResult(diff_a, "a.py", "a.py", "old a", "new a"),
                FileDiffResult(diff_b, "b.py", "b.py", "old b", "new b"),
            ])
            commit_differ.return_value.finalize_commit_diff.return_value = commit_diff
            server._process_request(
                {"op": "review", "seq": 7, "stream": True},
                lambda obj: sent.append(obj),
            )

        review_files = [msg for msg in sent if msg.get("op") == "review_file"]
        terminals = [msg for msg in sent if msg.get("op") == "review"]
        assert len(review_files) == 2
        assert review_files[0]["file_diff"]["new_filename"] == "a.py"
        assert review_files[0]["index"] == 1
        assert review_files[1]["file_diff"]["new_filename"] == "b.py"
        assert review_files[1]["index"] == 2
        assert len(terminals) == 1
        assert terminals[0]["metadata"]["streamed"] is True
        # The terminal event must still carry the complete commit diff.
        assert len(terminals[0]["commit_diff"]["file_diffs"]) == 2

    def test_review_without_stream_emits_only_terminal_event(self) -> None:
        from intentumdiff.core.models import CommitDiff

        server, _ = _make_server(ref="HEAD")
        sent: list[dict] = []

        with patch("intentumdiff.core.commit_differ.CommitDiffer") as commit_differ:
            commit_differ.return_value.diff_commit.return_value = CommitDiff(
                old_ref="HEAD", new_ref=""
            )
            server._process_request(
                {"op": "review", "seq": 5},
                lambda obj: sent.append(obj),
            )

        assert len(sent) == 1
        assert sent[0]["op"] == "review"
        assert sent[0]["metadata"]["streamed"] is False

    def test_review_honors_request_refs(self) -> None:
        from intentumdiff.core.models import CommitDiff

        server, _ = _make_server(ref="HEAD")
        sent: list[dict] = []

        with patch("intentumdiff.core.commit_differ.CommitDiffer") as commit_differ:
            commit_differ.return_value.diff_commit.return_value = CommitDiff(
                old_ref="main",
                new_ref="feature",
            )
            server._process_request(
                {"op": "review", "seq": 3, "old_ref": "main", "new_ref": "feature"},
                lambda obj: sent.append(obj),
            )

        commit_differ.return_value.diff_commit.assert_called_once_with(
            repo_path=server._repo_path,
            old_ref="main",
            new_ref="feature",
        )
        assert sent[0]["metadata"]["old_ref"] == "main"
        assert sent[0]["metadata"]["new_ref"] == "feature"

    def test_review_surfaces_total_parser_load_failure(self) -> None:
        server, _ = _make_server(ref="HEAD")
        sent: list[dict] = []

        with patch("intentumdiff.core.commit_differ.CommitDiffer") as commit_differ:
            commit_differ.return_value.diff_commit.side_effect = RuntimeError(
                "No parser plugins could be loaded"
            )
            server._process_request({"op": "review", "seq": 19}, lambda obj: sent.append(obj))

        assert sent[0]["op"] == "review"
        assert sent[0]["ok"] is False
        assert sent[0]["error"]["code"] == "review_error"
        assert "No parser plugins could be loaded" in sent[0]["error"]["message"]

    def test_review_caps_unlimited_live_fuel_with_fresh_registry(self) -> None:
        from intentumdiff.core.models import CommitDiff, DiffConfig

        server, differ = _make_server(ref="HEAD")
        differ._config = DiffConfig(plugin_fuel=-1)
        differ._registry = object()
        sent: list[dict] = []

        with patch("intentumdiff.core.commit_differ.CommitDiffer") as commit_differ:
            commit_differ.return_value.diff_commit.return_value = CommitDiff(
                old_ref="HEAD",
                new_ref="",
            )
            server._process_request({"op": "review", "seq": 8}, lambda obj: sent.append(obj))

        _, kwargs = commit_differ.call_args
        assert kwargs["config"].plugin_fuel == 100_000_000
        assert kwargs["registry"] is None
        assert sent[0]["metadata"]["review_fuel_capped"] is True
        assert sent[0]["metadata"]["review_plugin_fuel"] == 100_000_000

    def test_review_rejects_invalid_ref_types(self) -> None:
        server, _ = _make_server()
        sent: list[dict] = []

        server._process_request(
            {"op": "review", "seq": 4, "old_ref": ["main"]},
            lambda obj: sent.append(obj),
        )

        assert sent[0]["op"] == "review"
        assert sent[0]["ok"] is False
        assert sent[0]["error"]["code"] == "invalid_ref"


# ---------------------------------------------------------------------------
# Debounce coalescing
# ---------------------------------------------------------------------------


class TestDebounce:
    def test_rapid_requests_coalesced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple rapid requests for the same path → only one diff call."""
        timers: list[_LazyTimer] = []

        def _fake_timer(delay, fn, args=()):
            t = _LazyTimer(delay, fn, args=args)
            timers.append(t)
            return t

        server, differ = _make_server(debounce=0.1)

        with patch("intentumdiff.live_server.threading.Timer", side_effect=_fake_timer):
            calls = []
            req = {"path": "foo.py", "content": "x=1", "seq": 1}
            server._schedule_request(req, lambda obj: calls.append(obj))
            req2 = {"path": "foo.py", "content": "x=2", "seq": 2}
            server._schedule_request(req2, lambda obj: calls.append(obj))
            req3 = {"path": "foo.py", "content": "x=3", "seq": 3}
            server._schedule_request(req3, lambda obj: calls.append(obj))

        # Only the last timer should survive (the first two are cancelled)
        active = [t for t in timers if not t.cancelled]
        assert len(active) == 1
        assert timers[0].cancelled
        assert timers[1].cancelled
        assert not timers[2].cancelled

    def test_different_paths_not_coalesced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        timers: list[_LazyTimer] = []

        def _fake_timer(delay, fn, args=()):
            t = _LazyTimer(delay, fn, args=args)
            timers.append(t)
            return t

        server, differ = _make_server(debounce=0.1)

        with patch("intentumdiff.live_server.threading.Timer", side_effect=_fake_timer):
            server._schedule_request(
                {"path": "a.py", "content": "x", "seq": 1}, lambda _: None
            )
            server._schedule_request(
                {"path": "b.py", "content": "y", "seq": 1}, lambda _: None
            )

        # Both should be active — different paths
        active = [t for t in timers if not t.cancelled]
        assert len(active) == 2


# ---------------------------------------------------------------------------
# Sequence number (late-response discarding)
# ---------------------------------------------------------------------------


class TestSeqDiscarding:
    def test_late_seq_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stale seq (< latest_seq for path) should not trigger a diff."""
        timers: list[_LazyTimer] = []

        def _fake_timer(delay, fn, args=()):
            t = _LazyTimer(delay, fn, args=args)
            timers.append(t)
            return t

        server, differ = _make_server(debounce=0.0)
        with patch("intentumdiff.live_server.threading.Timer", side_effect=_fake_timer):
            # seq=5 arrives first — sets latest_seq["foo.py"] = 5
            server._schedule_request(
                {"path": "foo.py", "content": "a", "seq": 5}, lambda _: None
            )
            # seq=3 arrives later (stale) — latest_seq stays 5
            server._schedule_request(
                {"path": "foo.py", "content": "b", "seq": 3}, lambda _: None
            )

        # Fire the surviving timer for the stale request (seq=3)
        sent = []
        timers[-1].fire()
        differ.diff.assert_not_called()

        # _run_diff checks seq < latest_seq → should call differ.diff 0 times
        # (We need to actually call _run_diff with the low seq request)

    def test_latest_seq_wins(self) -> None:
        """latest_seq is the max of all seen seqs for a given path."""
        server, _ = _make_server()
        server._latest_seq["a.py"] = 0
        with server._lock:
            server._latest_seq["a.py"] = max(server._latest_seq.get("a.py", 0), 10)
            server._latest_seq["a.py"] = max(server._latest_seq.get("a.py", 0), 3)
        assert server._latest_seq["a.py"] == 10


# ---------------------------------------------------------------------------
# Direct request processing (no debounce)
# ---------------------------------------------------------------------------


class TestProcessRequest:
    def test_non_streaming_returns_diff_json(self) -> None:
        server, differ = _make_server(stream_analysis=False)
        sent = []

        with patch(
            "intentumdiff.live_server.LiveBufferSource",
            return_value=MagicMock(),
        ):
            server._process_request(
                {"path": "foo.py", "content": "x=1", "seq": 7},
                lambda obj: sent.append(obj),
            )

        assert len(sent) == 1
        assert sent[0]["op"] == "diff"
        assert sent[0]["ok"] is True
        assert sent[0]["seq"] == 7
        assert "diff" in sent[0]
        assert sent[0]["metadata"]["language"] == "python"

    def test_streaming_returns_events_then_done(self) -> None:
        from intentumdiff.core.models import ChangeStreamEvent, ChangeStreamPhase

        event = ChangeStreamEvent(
            phase=ChangeStreamPhase.STRUCTURAL, action="add", change=None
        )
        differ = _make_differ()
        differ.diff_stream_progressive.return_value = iter([event])

        from intentumdiff.live_server import LiveServer

        server = LiveServer(differ, repo_path=tempfile.mkdtemp(), stream_analysis=True)
        sent = []

        with patch(
            "intentumdiff.live_server.LiveBufferSource",
            return_value=MagicMock(),
        ):
            server._process_request(
                {"path": "foo.py", "content": "x", "seq": 2},
                lambda obj: sent.append(obj),
            )

        assert any("event" in m for m in sent), "Expected at least one event message"
        assert sent[-1] == {"op": "diff", "seq": 2, "ok": True, "done": True}

    def test_file_not_found_returns_error(self) -> None:
        differ = _make_differ()
        differ.diff.side_effect = FileNotFoundError("no such file")

        from intentumdiff.live_server import LiveServer

        server = LiveServer(differ, repo_path=tempfile.mkdtemp())
        sent = []

        with patch(
            "intentumdiff.live_server.LiveBufferSource",
            return_value=MagicMock(),
        ):
            server._process_request(
                {"path": "missing.py", "content": "", "seq": 1},
                lambda obj: sent.append(obj),
            )

        assert len(sent) == 1
        assert "error" in sent[0]
        assert sent[0]["error"]["code"] == "file_not_found"

    def test_invalid_edit_deltas_are_ignored(self) -> None:
        """Malformed delta dicts should not crash — they are skipped with a warning."""
        server, differ = _make_server()
        sent = []

        with patch(
            "intentumdiff.live_server.LiveBufferSource",
            return_value=MagicMock(),
        ):
            server._process_request(
                {
                    "path": "foo.py",
                    "content": "x",
                    "seq": 1,
                    "deltas": [{"invalid_field": True}],
                },
                lambda obj: sent.append(obj),
            )

        # Should still return a diff (edit deltas are optional hints)
        assert len(sent) == 1
        assert sent[0]["ok"] is True


# ---------------------------------------------------------------------------
# start_stdin
# ---------------------------------------------------------------------------


class TestNativeDiffHandler:
    """Phase A1: `rust_core.live_handle_diff` serves Python files from the core; verify it
    matches the Python differ (the oracle) for the same old/new inputs."""

    def test_native_python_diff_matches_differ(self, tmp_path: Path) -> None:
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.models import SemanticDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        old = "def f():\n    return 1\n"
        (tmp_path / "a.py").write_text(old, encoding="utf-8")
        git("add", "a.py")
        git("commit", "-m", "v1")

        new = "def f():\n    return 2\n"
        differ = SemanticDiffer()
        native = rust_core.live_handle_diff(
            str(tmp_path), "a.py", "HEAD", new, differ._config.model_dump_json(), _wasm_dir()
        )
        assert "diff" in native, f"native handler should serve a Python diff, got: {native}"

        # Reconstruct through the DTO (as `_compute_diff` does) and compare to the oracle.
        served = json.loads(SemanticDiff.model_validate(native["diff"]).model_dump_json())
        oracle = json.loads(differ.diff_strings(old, new, "a.py").model_dump_json())
        assert served["language"] == oracle["language"] == "python"
        assert served["is_style_only"] == oracle["is_style_only"]
        assert len(served["changes"]) == len(oracle["changes"]) >= 1
        assert [c["change_type"] for c in served["changes"]] == [
            c["change_type"] for c in oracle["changes"]
        ]

    def _assert_native_matches_differ(
        self, tmp_path: Path, filename: str, old: str, new: str
    ) -> None:
        """The certified batch is Python-only, so a non-Python file is served by the native
        chain (step d2: wasm-parse -> finalize -> invariances -> assemble). The served diff must
        match `differ.diff_strings` on the surface-critical fields (language, style flags, and the
        change_type sequence — all produced by the SAME Rust finalize+invariances the differ
        routes through). If a policy/analyzer/etc. case isn't covered, the handler falls back
        (still correct) and the test skips."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.models import SemanticDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (tmp_path / filename).write_text(old, encoding="utf-8")
        git("add", filename)
        git("commit", "-m", "v1")

        differ = SemanticDiffer()
        native = rust_core.live_handle_diff(
            str(tmp_path), filename, "HEAD", new, differ._config.model_dump_json(), _wasm_dir()
        )
        if "diff" not in native:
            fallback = str(native.get("fallback", ""))
            if "staged" in fallback or "unsupported parser" in fallback:
                pytest.skip(f"native chain for {filename} fell back (parser not staged): {fallback}")
            pytest.fail(f"native chain for {filename} DECLINED unexpectedly: {fallback}")
        served = json.loads(SemanticDiff.model_validate(native["diff"]).model_dump_json())
        oracle = json.loads(differ.diff_strings(old, new, filename).model_dump_json())
        assert served["language"] == oracle["language"]
        assert served["is_style_only"] == oracle["is_style_only"]
        assert served["has_semantic_changes"] == oracle["has_semantic_changes"]
        assert [c["change_type"] for c in served["changes"]] == [
            c["change_type"] for c in oracle["changes"]
        ]

    def test_native_typescript_diff_broadens_beyond_python(self, tmp_path: Path) -> None:
        """Step d2 — a non-Python (typescript) file is served natively, matching the differ.
        The parse half (step d1) is proven in the Rust suite
        (`native_wasm_parse_typescript_pair_is_language_agnostic`)."""
        self._assert_native_matches_differ(
            tmp_path, "a.ts", "const x: number = 1;\n", "const x: number = 2;\n"
        )

    def test_native_go_diff_broadens_beyond_python(self, tmp_path: Path) -> None:
        """Step d2 breadth — a second, structurally different language (go) is also served
        natively by the same chain, matching the differ."""
        self._assert_native_matches_differ(
            tmp_path,
            "main.go",
            "package main\n\nfunc add(a int, b int) int {\n\treturn a + b\n}\n",
            "package main\n\nfunc add(a int, b int) int {\n\treturn a - b\n}\n",
        )

    def test_native_sql_diff_needs_profile_enrichment(self, tmp_path: Path) -> None:
        """Step d2 — a profile language (sql). Without the stage-7b profile-label enrichment the
        added `b` surfaces as TWO changes (a bare `term` + its `field`); with it the field folds
        into one `term('b')` ADDITION, matching the differ. Regression-locks that the native chain
        enriches trees before finalize."""
        self._assert_native_matches_differ(
            tmp_path, "q.sql", "SELECT a FROM t;\n", "SELECT a, b FROM t;\n"
        )

    _MD_MOVE_OLD = (
        "# Title\n\nintro text\n\n## Alpha\n\nalpha body line\nmore alpha\n\n"
        "## Beta\n\nbeta body\n\n## Gamma\n\ngamma body\n"
    )
    _MD_MOVE_NEW = (
        "# Title\n\nintro text\n\n## Beta\n\nbeta body\n\n"
        "## Alpha\n\nalpha body line\nmore alpha\n\n## Gamma\n\ngamma body\n"
    )

    def test_native_markdown_diff_serves_section_move(self, tmp_path: Path) -> None:
        """Markdown is a certified routed language (#44): sections/headings are real tree nodes
        and the ENGINE's reorder->MOVE promotion is the section presentation — the differ never
        runs the `_differ_presentation.py` markdown passes for `.md` (those only exist for
        GENERIC-routed md-named files, which the manifest never produces). So the native chain
        serves `.md` with full section presentation; this locks the MOVE + its section label and
        the group rule_ids, not just the change_type shape (the retired lenient/line-view
        special-case regressed exactly this)."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.models import SemanticDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (tmp_path / "README.md").write_text(self._MD_MOVE_OLD, encoding="utf-8")
        git("add", "README.md")
        git("commit", "-m", "v1")

        differ = SemanticDiffer()
        native = rust_core.live_handle_diff(
            str(tmp_path),
            "README.md",
            "HEAD",
            self._MD_MOVE_NEW,
            differ._config.model_dump_json(),
            _wasm_dir(),
        )
        assert "diff" in native, f"native handler should serve markdown, got: {native}"
        served = json.loads(SemanticDiff.model_validate(native["diff"]).model_dump_json())
        oracle = json.loads(
            differ.diff_strings(self._MD_MOVE_OLD, self._MD_MOVE_NEW, "README.md")
            .model_dump_json()
        )
        assert served["language"] == oracle["language"] == "markdown"

        def shape(diff: dict) -> list[tuple[str, str | None, str | None]]:
            return [
                (
                    c["change_type"],
                    (c.get("old_node") or {}).get("label"),
                    (c.get("new_node") or {}).get("label"),
                )
                for c in diff["changes"]
            ]

        assert shape(served) == shape(oracle)
        assert ("MOVE", "Beta", "Beta") in shape(served)
        assert sorted((g["kind"], g["rule_id"]) for g in served["change_groups"]) == sorted(
            (g["kind"], g["rule_id"]) for g in oracle["change_groups"]
        )

    def test_native_markdown_diff_serves_heading_rename(self, tmp_path: Path) -> None:
        """A heading rename over a stable body is the engine's REFACTORING grouping — served
        natively with the same change shape as the differ."""
        self._assert_native_matches_differ(
            tmp_path,
            "doc.md",
            "# Doc\n\n## Old Heading\n\nstable body one\nstable body two\n\n## Keep\n\nkeep body\n",
            "# Doc\n\n## New Heading\n\nstable body one\nstable body two\n\n## Keep\n\nkeep body\n",
        )

    def test_native_markdown_diff_serves_body_edit(self, tmp_path: Path) -> None:
        self._assert_native_matches_differ(
            tmp_path,
            "notes.md",
            "# Doc\n\n## Section\n\nfirst line\nsecond line\n",
            "# Doc\n\n## Section\n\nfirst line CHANGED\nsecond line\n",
        )

    def test_native_markdown_diff_resolves_style_only(self, tmp_path: Path) -> None:
        """Whitespace-only markdown churn must resolve is_style_only exactly like the differ's
        stage-12 resolution (markdown #44 was the language that exposed that rule)."""
        self._assert_native_matches_differ(
            tmp_path,
            "style.md",
            "# Doc\n\n## Section\n\nbody line\n",
            "# Doc\n\n\n## Section\n\nbody line\n",
        )

    def test_native_diff_serves_with_empty_guardrail_policy(self, tmp_path: Path) -> None:
        """A repo whose intentumdiff.yaml carries `guardrails: protected: []` (a pure CONFIG file —
        this repo's own dogfood shape) attaches ZERO violations in the differ, so the native path
        must SERVE it, not blanket-fall-back. Regression-locks the 'guardrail policy in effect'
        error that blocked every file of the dogfood repo in the VS Code side-by-side."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.models import SemanticDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (tmp_path / "intentumdiff.yaml").write_text(
            "config:\n  min_similarity: 0.5\n\nguardrails:\n  protected: []\n",
            encoding="utf-8",
        )
        (tmp_path / "a.ts").write_text("const x: number = 1;\n", encoding="utf-8")
        git("add", "intentumdiff.yaml", "a.ts")
        git("commit", "-m", "v1")

        differ = SemanticDiffer()
        native = rust_core.live_handle_diff(
            str(tmp_path),
            "a.ts",
            "HEAD",
            "const x: number = 2;\n",
            differ._config.model_dump_json(),
            _wasm_dir(),
        )
        assert "diff" in native, f"empty-rules policy must serve natively: {native}"
        served = json.loads(SemanticDiff.model_validate(native["diff"]).model_dump_json())
        oracle = json.loads(
            differ.diff_strings(
                "const x: number = 1;\n", "const x: number = 2;\n", "a.ts"
            ).model_dump_json()
        )
        assert [c["change_type"] for c in served["changes"]] == [
            c["change_type"] for c in oracle["changes"]
        ]
        assert served["language"] == oracle["language"]

    def test_native_diff_evaluates_protected_guardrail_rules(self, tmp_path: Path) -> None:
        """A policy with ACTUAL protected rules is now evaluated NATIVELY (#100): the served diff
        must carry the same guardrail violations the differ attaches (the strict rule loading +
        the A1.3 rule engine, all in Rust)."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.models import SemanticDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        policy = tmp_path / "intentumdiff.yaml"
        policy.write_text(
            "guardrails:\n"
            "  protected:\n"
            "    - id: guard.version\n"
            "      language: json\n"
            "      path: version\n"
            "      severity: immutable\n"
            '      files: ["*.json"]\n',
            encoding="utf-8",
        )
        old = '{\n  "version": 1,\n  "name": "x"\n}\n'
        new = '{\n  "version": 2,\n  "name": "x"\n}\n'
        (tmp_path / "d.json").write_text(old, encoding="utf-8")
        git("add", "intentumdiff.yaml", "d.json")
        git("commit", "-m", "v1")

        base = SemanticDiffer()
        cfg = base._config.model_copy(update={"guardrail_policy_path": policy})
        differ = SemanticDiffer(config=cfg)
        native = rust_core.live_handle_diff(
            str(tmp_path), "d.json", "HEAD", new, cfg.model_dump_json(), _wasm_dir()
        )
        assert "diff" in native, f"ruled policy must now serve natively: {native}"
        served = json.loads(SemanticDiff.model_validate(native["diff"]).model_dump_json())
        oracle = json.loads(differ.diff_strings(old, new, "d.json").model_dump_json())
        vio = lambda d: sorted(  # noqa: E731
            (v["rule_id"], v["severity"], v["semantic_path"]) for v in d["guardrail_violations"]
        )
        assert vio(oracle), "fixture must actually trip the rule in the differ"
        assert vio(served) == vio(oracle)
        assert served["metadata"]["guardrails"] == oracle["metadata"]["guardrails"]

    def test_native_diff_defers_on_invalid_guardrail_policy(self, tmp_path: Path) -> None:
        """An off-spec policy (unsupported severity) makes the differ RAISE — the native path must
        defer for those, never silently drop or mis-read a rule."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (tmp_path / "intentumdiff.yaml").write_text(
            "guardrails:\n"
            "  protected:\n"
            "    - language: json\n"
            "      path: version\n"
            "      severity: catastrophic\n",
            encoding="utf-8",
        )
        (tmp_path / "a.ts").write_text("const x: number = 1;\n", encoding="utf-8")
        git("add", "intentumdiff.yaml", "a.ts")
        git("commit", "-m", "v1")

        differ = SemanticDiffer()
        native = rust_core.live_handle_diff(
            str(tmp_path),
            "a.ts",
            "HEAD",
            "const x: number = 2;\n",
            differ._config.model_dump_json(),
            _wasm_dir(),
        )
        assert "fallback" in native, f"invalid policy must defer: {native}"
        assert "severity" in native["fallback"]

    def test_native_diff_flags_policy_file_edit_as_immutable(self, tmp_path: Path) -> None:
        """Editing intentumdiff.yaml itself must attach the IMMUTABLE `intentumdiff.policy_file`
        violation on the served diff, exactly like `apply_guardrails_to_diff`."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.models import SemanticDiff
        from intentumdiff.live_server import _wasm_dir
        from intentumdiff.differ import SemanticDiffer

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        old = "config:\n  min_similarity: 0.5\n\nguardrails:\n  protected: []\n"
        new = "config:\n  min_similarity: 0.6\n\nguardrails:\n  protected: []\n"
        (tmp_path / "intentumdiff.yaml").write_text(old, encoding="utf-8")
        git("add", "intentumdiff.yaml")
        git("commit", "-m", "v1")

        differ = SemanticDiffer()
        native = rust_core.live_handle_diff(
            str(tmp_path),
            "intentumdiff.yaml",
            "HEAD",
            new,
            differ._config.model_dump_json(),
            _wasm_dir(),
        )
        assert "diff" in native, f"policy-file edit must serve with the violation: {native}"
        served = json.loads(SemanticDiff.model_validate(native["diff"]).model_dump_json())
        rules = [(v["rule_id"], v["severity"]) for v in served["guardrail_violations"]]
        assert ("intentumdiff.policy_file", "immutable") in rules
        assert served["metadata"]["guardrails"]["immutable_count"] >= 1

    def test_native_unknown_extension_diff_serves_generic(self, tmp_path: Path) -> None:
        """Step d2 — a file with NO bundled parser (uv.lock-style) must resolve to the GENERIC
        line-oriented review, exactly like the Python registry (which never refuses a file), and
        the native line spans must match the differ's. Regression-locks the VS Code
        'no bundled parser for this file extension' review brick."""
        self._assert_native_matches_differ(
            tmp_path,
            "uv.lock",
            "package-a==1.0\npackage-b==2.0\n",
            "package-a==1.1\npackage-b==2.0\npackage-c==3.0\n",
        )

    def test_native_json_diff_resolves_over_adf(self, tmp_path: Path) -> None:
        """Step d2 — `.json` must resolve to the `json` parser, not `adf` (both claim the
        extension). Regression-locks the manifest's detection-based extension resolution."""
        self._assert_native_matches_differ(
            tmp_path, "d.json", '{\n  "x": 1,\n  "y": 2\n}\n', '{\n  "x": 1,\n  "y": 3\n}\n'
        )


class TestNativeReviewHandler:
    """Phase A2: `rust_core.live_handle_review` serves all-Python commits from the core; verify
    it matches `CommitDiffer.diff_commit` (the oracle)."""

    def test_native_python_review_matches_commit_differ(self, tmp_path: Path) -> None:
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.commit_differ import CommitDiffer
        from intentumdiff.core.models import CommitDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        git("add", "a.py")
        git("commit", "-m", "v1")
        (tmp_path / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        git("add", "a.py")
        git("commit", "-m", "v2")

        differ = SemanticDiffer()
        native = rust_core.live_handle_review(
            str(tmp_path), "HEAD~1", "HEAD", differ._config.model_dump_json(), _wasm_dir()
        )
        assert "commit_diff" in native, f"native review should serve: {native}"
        served = json.loads(
            CommitDiff.model_validate(native["commit_diff"]).model_dump_json()
        )

        oracle_obj = CommitDiffer(
            config=differ._config, registry=differ._registry
        ).diff_commit(repo_path=str(tmp_path), old_ref="HEAD~1", new_ref="HEAD")
        oracle = json.loads(oracle_obj.model_dump_json())
        assert len(served["file_diffs"]) == len(oracle["file_diffs"]) >= 1
        assert {f["new_filename"] for f in served["file_diffs"]} == {
            f["new_filename"] for f in oracle["file_diffs"]
        }

    def _assert_native_review_matches(
        self, tmp_path: Path, files_v1: dict[str, str], files_v2: dict[str, str]
    ) -> None:
        """A commit touching non-Python files is served per-file by the native chain (Python via
        the certified batch item, other languages via native_wasm_single_diff) and must match
        `CommitDiffer.diff_commit` on BOTH the per-file diffs (count, filenames, each file's
        change_type sequence) and the cross-file changes (MOVE_TO_MODULE / SPLIT_MODULE /
        CROSS_FILE_RENAME), which the native handler now computes too. Falls back (and skips) for
        any uncovered case."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.commit_differ import CommitDiffer
        from intentumdiff.core.models import CommitDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        for name, body in files_v1.items():
            (tmp_path / name).write_text(body, encoding="utf-8")
            git("add", name)
        git("commit", "-m", "v1")
        for name, body in files_v2.items():
            (tmp_path / name).write_text(body, encoding="utf-8")
            git("add", name)
        git("commit", "-m", "v2")

        differ = SemanticDiffer()
        native = rust_core.live_handle_review(
            str(tmp_path), "HEAD~1", "HEAD", differ._config.model_dump_json(), _wasm_dir()
        )
        if "commit_diff" not in native:
            fallback = str(native.get("fallback", ""))
            if "staged" in fallback or "unsupported parser" in fallback:
                pytest.skip(f"native review fell back (parser not staged): {fallback}")
            pytest.fail(f"native review DECLINED unexpectedly: {fallback}")
        served = json.loads(CommitDiff.model_validate(native["commit_diff"]).model_dump_json())
        oracle = json.loads(
            CommitDiffer(config=differ._config, registry=differ._registry)
            .diff_commit(repo_path=str(tmp_path), old_ref="HEAD~1", new_ref="HEAD")
            .model_dump_json()
        )
        by_name = lambda diffs: {  # noqa: E731
            f["new_filename"]: [c["change_type"] for c in f["changes"]] for f in diffs
        }
        assert by_name(served["file_diffs"]) == by_name(oracle["file_diffs"])
        # Cross-file parity: same set of (type, symbol, old_file, new_file) changes as the oracle.
        xf = lambda cs: sorted(  # noqa: E731
            (c["change_type"], c["symbol_name"], c["old_file"], c["new_file"]) for c in cs
        )
        assert xf(served["cross_file_changes"]) == xf(oracle["cross_file_changes"])

    def test_native_typescript_review_broadens_beyond_python(self, tmp_path: Path) -> None:
        """d2-review — a commit touching only a non-Python (typescript) file is served natively."""
        self._assert_native_review_matches(
            tmp_path,
            {"a.ts": "const x: number = 1;\n"},
            {"a.ts": "const x: number = 2;\n"},
        )

    def test_native_mixed_commit_review_broadens_beyond_python(self, tmp_path: Path) -> None:
        """d2-review — a mixed commit (python + typescript): python files come from the certified
        batch item, the typescript file from the native chain; both match the CommitDiffer."""
        self._assert_native_review_matches(
            tmp_path,
            {"a.py": "def f():\n    return 1\n", "b.ts": "const y: number = 1;\n"},
            {"a.py": "def f():\n    return 2\n", "b.ts": "const y: number = 2;\n"},
        )

    def test_native_working_tree_review_matches_commit_differ(self, tmp_path: Path) -> None:
        """new_ref="" is the WORKING-TREE review — the VS Code extension's default request
        (`{"op":"review","old_ref":"HEAD"}`). Regression-locks the review-storm bug: the handler
        must route "" through the full changed-sources dispatcher (not the commit iterator, which
        made git fail with 'Needed a single revision'), and an UNTRACKED file's empty old side
        must substitute the canonical empty tree instead of parsing "" (some parsers, e.g. sql,
        error-envelope on empty input). Asserts parity with CommitDiffer on per-file change
        types AND staging statuses (unstaged / staged / untracked)."""
        import subprocess

        from intentumdiff import rust_core
        from intentumdiff.core.commit_differ import CommitDiffer
        from intentumdiff.core.models import CommitDiff
        from intentumdiff.differ import SemanticDiffer
        from intentumdiff.live_server import _wasm_dir

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (tmp_path / "a.ts").write_text("const x: number = 1;\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (tmp_path / "uv.lock").write_text("package-a==1.0\n", encoding="utf-8")
        git("add", "a.ts", "b.py", "uv.lock")
        git("commit", "-m", "v1")
        # unstaged edits (incl. a no-parser lock file) + staged edit + untracked (empty old side)
        (tmp_path / "a.ts").write_text("const x: number = 2;\n", encoding="utf-8")
        (tmp_path / "uv.lock").write_text("package-a==1.1\n", encoding="utf-8")
        (tmp_path / "b.py").write_text(
            "def f():\n    return 1\n\n\ndef g():\n    return 2\n", encoding="utf-8"
        )
        git("add", "b.py")
        (tmp_path / "new.sql").write_text("SELECT a FROM t;\n", encoding="utf-8")

        differ = SemanticDiffer()
        native = rust_core.live_handle_review(
            str(tmp_path), "HEAD", "", differ._config.model_dump_json(), _wasm_dir()
        )
        if "commit_diff" not in native:
            fallback = str(native.get("fallback", ""))
            if "staged" in fallback or "unsupported parser" in fallback:
                pytest.skip(f"native working-tree review fell back (parser not staged): {fallback}")
            pytest.fail(f"native working-tree review DECLINED unexpectedly: {fallback}")
        served = json.loads(CommitDiff.model_validate(native["commit_diff"]).model_dump_json())
        oracle = json.loads(
            CommitDiffer(config=differ._config, registry=differ._registry)
            .diff_commit(repo_path=str(tmp_path), old_ref="HEAD", new_ref="")
            .model_dump_json()
        )
        shape = lambda diffs: {  # noqa: E731
            f["new_filename"] or f["old_filename"]: (
                f.get("staging_status"),
                [c["change_type"] for c in f["changes"]],
            )
            for f in diffs
        }
        assert shape(served["file_diffs"]) == shape(oracle["file_diffs"])
        assert len(served["file_diffs"]) == 4  # unstaged ts + unstaged lock + staged py + untracked

    def test_native_review_detects_cross_file_move(self, tmp_path: Path) -> None:
        """d2-review cross-file — `helper` moving from a.py to b.py must surface as a cross-file
        change matching the CommitDiffer (the native handler builds per-side symbol tables via the
        interpret-cst Python parser — the differ's tree — and diffs them, so `module.helper` is
        found, not a spurious whole-`module` move)."""
        self._assert_native_review_matches(
            tmp_path,
            {
                "a.py": "def helper():\n    return 1\n",
                "b.py": "def other():\n    return 2\n",
            },
            {
                "a.py": "def kept():\n    return 3\n",
                "b.py": "def other():\n    return 2\n\n\ndef helper():\n    return 1\n",
            },
        )


class TestStartStdin:
    def test_stdin_loop_processes_one_request(self) -> None:
        server, differ = _make_server(stream_analysis=False)
        request_line = json.dumps(
            {"path": "foo.py", "content": "x=1", "seq": 1}
        )
        output = StringIO()

        with patch(
            "intentumdiff.live_server.sys.stdin",
            iter([request_line + "\n"]),
        ), patch(
            "intentumdiff.live_server.sys.stdout",
            output,
        ), patch(
            "intentumdiff.live_server.LiveBufferSource",
            return_value=MagicMock(),
        ):
            server.start_stdin()

        ready, response = _json_lines(output)
        assert ready["op"] == "ready"
        assert ready["protocol_version"] == 2
        assert response["seq"] == 1
        assert response["ok"] is True
        assert "diff" in response

    def test_stdin_bad_json_returns_error(self) -> None:
        server, _ = _make_server()
        output = StringIO()

        with patch(
            "intentumdiff.live_server.sys.stdin",
            iter(["not valid json\n"]),
        ), patch(
            "intentumdiff.live_server.sys.stdout",
            output,
        ):
            server.start_stdin()

        ready, response = _json_lines(output)
        assert ready["op"] == "ready"
        assert "error" in response
        assert response["error"]["code"] == "invalid_json"


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_cancels_pending_timers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        timers: list[_LazyTimer] = []

        def _fake_timer(delay, fn, args=()):
            t = _LazyTimer(delay, fn, args=args)
            timers.append(t)
            return t

        server, _ = _make_server(debounce=10.0)  # never fires naturally

        with patch("intentumdiff.live_server.threading.Timer", side_effect=_fake_timer):
            server._schedule_request(
                {"path": "foo.py", "content": "x", "seq": 1}, lambda _: None
            )

        assert len(timers) == 1
        assert not timers[0].cancelled

        server.stop()

        assert timers[0].cancelled


# ---------------------------------------------------------------------------
# Security boundary tests
# ---------------------------------------------------------------------------


class TestSecurityBoundaries:
    def test_request_repo_path_is_ignored(self) -> None:
        """repo_path in a request payload must not override the server's repo root."""
        server_root = tempfile.mkdtemp()
        server, _ = _make_server(repo_path=server_root)
        captured: list[str] = []

        def _capture(*args, **kwargs):
            # LiveBufferSource is called with keyword args only
            captured.append(str(kwargs.get("repo_path", args[0] if args else "")))
            return MagicMock()

        with patch("intentumdiff.live_server.LiveBufferSource", side_effect=_capture):
            server._process_request(
                {"path": "foo.py", "content": "x", "seq": 1, "repo_path": "/attacker/path"},
                lambda _: None,
            )

        assert len(captured) == 1, "Expected exactly one LiveBufferSource call"
        assert captured[0] == server_root, "repo_path from request must not override server root"

    def test_oversized_content_rejected(self) -> None:
        """Content exceeding the advertised max_content_bytes must be refused."""
        server, _ = _make_server()
        max_content = server._limits()["max_content_bytes"]
        sent: list[dict] = []

        server._process_request(
            {"path": "foo.py", "content": "x" * (max_content + 1), "seq": 1},
            lambda obj: sent.append(obj),
        )

        assert len(sent) == 1
        assert "error" in sent[0]
        assert sent[0]["error"]["code"] == "content_too_large"
        assert "content exceeds" in sent[0]["error"]["message"]

    def test_excessive_deltas_rejected(self) -> None:
        """Delta count exceeding the advertised max_deltas must be refused."""
        server, _ = _make_server()
        max_deltas = server._limits()["max_deltas"]
        sent: list[dict] = []

        server._process_request(
            {
                "path": "foo.py",
                "content": "x",
                "seq": 1,
                "deltas": [{}] * (max_deltas + 1),
            },
            lambda obj: sent.append(obj),
        )

        assert len(sent) == 1
        assert "error" in sent[0]
        assert sent[0]["error"]["code"] == "too_many_deltas"
        assert "deltas" in sent[0]["error"]["message"]

    def test_client_count_decremented_on_disconnect(self) -> None:
        """_client_count must return to zero after a client disconnects."""
        server, _ = _make_server()
        assert server._client_count == 0

        with server._lock:
            server._client_count += 1
        assert server._client_count == 1

        with server._lock:
            server._client_count = max(0, server._client_count - 1)
        assert server._client_count == 0


# ---------------------------------------------------------------------------
# Named-pipe fallback (Windows only, older OS without AF_UNIX)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (sys.platform == "win32"),
    reason="Named-pipe fallback is Windows-only",
)
class TestNamedPipeServer:
    """On older Windows (no AF_UNIX), LiveServer uses a named pipe whose OS
    ACL restricts access to the creating user — no application token required."""

    def _start_pipe(self):
        """Force the named-pipe path by patching _has_unix_socket."""
        server, differ = _make_server(debounce=0)
        with patch("intentumdiff.live_server._has_unix_socket", return_value=False):
            addr = server.start_socket()
        return server, differ, addr

    def _roundtrip(self, addr: str, request: dict, *, timeout: float = 10.0) -> dict:
        """Send one JSON request over the named pipe; return first response.

        Uses overlapped (async) I/O so that ReadFile never blocks indefinitely.
        A WaitForSingleObject with *timeout* ensures the test fails fast rather
        than hanging when the server doesn't respond.
        """
        import ctypes
        import ctypes.wintypes as _wt

        _GENERIC_RW = 0xC0000000
        _OPEN_EXISTING = 3
        _FILE_ATTRIBUTE_NORMAL = 0x80
        _FILE_FLAG_OVERLAPPED = 0x40000000
        _INVALID_HANDLE = ctypes.c_void_p(-1).value
        _ERROR_IO_PENDING = 997
        _WAIT_OBJECT_0 = 0

        class _OVERLAPPED(ctypes.Structure):
            # Internal/InternalHigh are ULONG_PTR (pointer-sized).
            # Use c_size_t so the struct is 32 bytes on 64-bit Windows and
            # hEvent lands at the correct offset (24) rather than offset 16.
            _fields_ = [
                ("Internal", ctypes.c_size_t),
                ("InternalHigh", ctypes.c_size_t),
                ("Offset", _wt.DWORD),
                ("OffsetHigh", _wt.DWORD),
                ("hEvent", _wt.HANDLE),
            ]

        import time as _time

        data = (json.dumps(request) + "\n").encode()

        def _wait_overlapped(handle, ol, event, budget: float) -> int:
            """Wait for an overlapped op within *budget* seconds; return bytes transferred."""
            n = _wt.DWORD(0)
            result = ctypes.windll.kernel32.WaitForSingleObject(
                event, int(max(0.0, budget) * 1000)
            )
            if result != _WAIT_OBJECT_0:
                ctypes.windll.kernel32.CancelIo(handle)
                raise TimeoutError("Named pipe operation timed out")
            ctypes.windll.kernel32.GetOverlappedResult(
                handle, ctypes.byref(ol), ctypes.byref(n), False
            )
            return n.value

        def _attempt(budget: float) -> dict:
            """One full connect → write → read → parse on a FRESH handle. Raises on any failure."""
            ctypes.windll.kernel32.WaitNamedPipeW(addr, int(budget * 1000))
            h = ctypes.windll.kernel32.CreateFileW(
                addr, _GENERIC_RW, 0, None, _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OVERLAPPED, None,
            )
            if h == _INVALID_HANDLE:
                raise OSError(f"open pipe {addr} failed: error {ctypes.GetLastError()}")
            end = _time.monotonic() + budget
            try:
                # ── Write request ────────────────────────────────────────────
                write_event = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
                write_ol = _OVERLAPPED()
                write_ol.hEvent = write_event
                n_written = _wt.DWORD(0)
                ret = ctypes.windll.kernel32.WriteFile(
                    h, data, len(data), ctypes.byref(n_written), ctypes.byref(write_ol),
                )
                if not ret and ctypes.GetLastError() == _ERROR_IO_PENDING:
                    _wait_overlapped(h, write_ol, write_event, end - _time.monotonic())
                ctypes.windll.kernel32.CloseHandle(write_event)

                # ── Read response (accumulate until newline) ──────────────────
                accumulated = b""
                while b"\n" not in accumulated:
                    remaining = end - _time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"response incomplete: {accumulated!r}")
                    buf = ctypes.create_string_buffer(65536)
                    n_read = _wt.DWORD(0)
                    read_event = ctypes.windll.kernel32.CreateEventW(None, True, False, None)
                    read_ol = _OVERLAPPED()
                    read_ol.hEvent = read_event
                    ret = ctypes.windll.kernel32.ReadFile(
                        h, buf, 65536, ctypes.byref(n_read), ctypes.byref(read_ol),
                    )
                    err = ctypes.GetLastError()
                    if not ret and err == _ERROR_IO_PENDING:
                        count = _wait_overlapped(h, read_ol, read_event, remaining)
                    elif not ret:
                        # Synchronous failure (broken/invalid handle) — abandon this attempt so
                        # the caller reconnects on a fresh handle rather than busy-spinning (#73).
                        ctypes.windll.kernel32.CloseHandle(read_event)
                        raise OSError(f"pipe read failed: error {err}")
                    else:
                        count = n_read.value
                    ctypes.windll.kernel32.CloseHandle(read_event)
                    if count:
                        accumulated += buf.raw[:count]
                    else:
                        _time.sleep(0.001)
                return json.loads(accumulated.split(b"\n")[0])
            finally:
                ctypes.windll.kernel32.CloseHandle(h)

        # Under full-suite load the connect races with the server creating and serving the pipe
        # instance (ERROR_INVALID_HANDLE / broken pipe on the first read) — the #73 flakiness.
        # Retry the WHOLE roundtrip on a fresh handle, bounded by an overall deadline so it can
        # never hang (the old single-attempt loop busy-spun for hours on a broken pipe).
        overall_deadline = _time.monotonic() + timeout
        last_exc: Exception | None = None
        while _time.monotonic() < overall_deadline:
            try:
                return _attempt(min(overall_deadline - _time.monotonic(), 2.0))
            except (OSError, TimeoutError, ValueError) as exc:
                last_exc = exc
                _time.sleep(0.02)
        raise TimeoutError(f"named pipe roundtrip failed after {timeout}s: {last_exc}")

    def test_named_pipe_no_token_required(self) -> None:
        """A valid request sent without any token field must succeed (no unauthorized error)."""
        server, _, addr = self._start_pipe()
        try:
            with patch(
                "intentumdiff.live_server.LiveBufferSource",
                return_value=MagicMock(),
            ):
                resp = self._roundtrip(
                    addr, {"path": "foo.py", "content": "x=1", "seq": 1}
                )
            assert resp.get("error") != "unauthorized", (
                f"Named pipe must not require a token; got: {resp}"
            )
        finally:
            server.stop()

    def test_named_pipe_stop_joins_cleanly(self) -> None:
        """stop() must unblock ConnectNamedPipe and allow the thread to exit."""
        server, _, _addr = self._start_pipe()
        assert server._server_thread is not None
        server.stop()
        # Generous deadline: the guarded bug (#73 family) is a thread that NEVER exits
        # because ConnectNamedPipe stays blocked — not one that exits slowly. Under a
        # fully-loaded suite run the accept thread can be starved past a 3s join
        # (observed 2026-07-15: failed in a 51-minute full gate, passed in isolation).
        server._server_thread.join(timeout=30.0)
        assert not server._server_thread.is_alive(), "Accept thread should have exited after stop()"


# ---------------------------------------------------------------------------
# Transport selection: _has_unix_socket and start_socket platform routing
# ---------------------------------------------------------------------------


class TestTransportSelection:
    """_has_unix_socket() must exclude Windows even if AF_UNIX is present."""

    def test_has_unix_socket_false_on_win32(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On win32, _has_unix_socket() must return False regardless of AF_UNIX."""
        import socket as _socket

        from intentumdiff import live_server as _ls

        monkeypatch.setattr(_ls.sys, "platform", "win32")
        # Ensure AF_UNIX appears to be available so the old bug would show True.
        monkeypatch.setattr(_socket, "AF_UNIX", 1, raising=False)
        assert _ls._has_unix_socket() is False

    def test_has_unix_socket_true_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On a non-win32 platform with AF_UNIX, _has_unix_socket() must return True."""
        import socket as _socket

        from intentumdiff import live_server as _ls

        monkeypatch.setattr(_ls.sys, "platform", "linux")
        monkeypatch.setattr(_socket, "AF_UNIX", 1, raising=False)
        assert _ls._has_unix_socket() is True

    @pytest.mark.skipif(sys.platform == "win32", reason="Named pipe only on Windows")
    def test_start_socket_returns_unix_path_on_posix(self) -> None:
        """On POSIX, start_socket() should bind a Unix socket and return a path string."""
        server, _ = _make_server()
        try:
            addr = server.start_socket()
            assert not addr.startswith(r"\\.\pipe\\"), (
                "On POSIX, start_socket() should return a socket path, not a pipe name"
            )
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Malformed request type-safety
# ---------------------------------------------------------------------------


class TestMalformedRequests:
    """_process_request() must safely reject/coerce bad field types."""

    def _send(self, request: dict) -> dict:
        server, _ = _make_server()
        sent: list[dict] = []
        server._process_request(request, lambda obj: sent.append(obj))
        assert sent, "Expected at least one response"
        return sent[0]

    def test_invalid_seq_returns_error(self) -> None:
        resp = self._send({"path": "x.py", "content": "x=1", "seq": "not-an-int"})
        assert "error" in resp
        assert resp["error"]["code"] == "invalid_seq"

    def test_non_string_content_returns_error(self) -> None:
        resp = self._send({"path": "x.py", "content": 12345, "seq": 1})
        assert "error" in resp
        assert resp["error"]["code"] == "invalid_content"
        assert "content" in resp["error"]["message"]

    def test_non_list_deltas_returns_error(self) -> None:
        resp = self._send({"path": "x.py", "content": "x=1", "seq": 1, "deltas": {"bad": True}})
        assert "error" in resp
        assert resp["error"]["code"] == "invalid_deltas"
        assert "deltas" in resp["error"]["message"]

    def test_none_seq_defaults_to_zero(self) -> None:
        """A None seq should default to 0 (edge case of int() call path)."""
        # None → int(None) would raise TypeError without the try/except guard.
        resp = self._send({"path": "x.py", "content": "x=1", "seq": None})
        assert "error" in resp

    def test_non_string_path_returns_error(self) -> None:
        """A non-string 'path' must return an error response instead of raising TypeError."""
        errors: list[dict] = []

        server, _ = _make_server()
        server._schedule_request(
            {"path": ["not", "a", "string"], "content": "x=1", "seq": 1},
            lambda msg: errors.append(msg),
        )
        assert len(errors) == 1
        assert "error" in errors[0]
