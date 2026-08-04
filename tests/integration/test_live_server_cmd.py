"""Integration tests for ``intentumdiff watch`` LiveServer CLI args."""
from __future__ import annotations

from types import SimpleNamespace


class TestLiveServerArgParsing:
    def _parse(self, *args: str):
        from intentumdiff.cli import _build_parser

        return _build_parser().parse_args(["watch", *args])

    # ── --live ────────────────────────────────────────────────────────────────

    def test_live_default_is_false(self) -> None:
        args = self._parse()
        assert args.live is False

    def test_live_flag_sets_true(self) -> None:
        args = self._parse("--live")
        assert args.live is True

    # ── --live-stdin ──────────────────────────────────────────────────────────

    def test_live_stdin_default_is_false(self) -> None:
        args = self._parse()
        assert args.live_stdin is False

    def test_live_stdin_flag_sets_true(self) -> None:
        args = self._parse("--live-stdin")
        assert args.live_stdin is True

    # ── --live-socket ─────────────────────────────────────────────────────────

    def test_live_socket_default_is_none(self) -> None:
        args = self._parse()
        assert args.live_socket is None

    def test_live_socket_custom_path(self) -> None:
        args = self._parse("--live-socket", "/tmp/test.sock")
        assert args.live_socket == "/tmp/test.sock"

    # ── --live-debounce ───────────────────────────────────────────────────────

    def test_live_debounce_default(self) -> None:
        args = self._parse()
        assert args.live_debounce == 0.15

    def test_live_debounce_custom(self) -> None:
        args = self._parse("--live-debounce", "0.5")
        assert args.live_debounce == 0.5

    # ── --live-stream ─────────────────────────────────────────────────────────

    def test_live_stream_default_is_false(self) -> None:
        args = self._parse()
        assert args.live_stream is False

    def test_live_stream_flag_sets_true(self) -> None:
        args = self._parse("--live-stream")
        assert args.live_stream is True

    # ── Combination ───────────────────────────────────────────────────────────

    def test_live_with_paths_and_ref(self) -> None:
        args = self._parse("src/", "--ref", "main", "--live", "--live-debounce", "0.2")
        assert args.paths == ["src/"]
        assert args.ref == "main"
        assert args.live is True
        assert args.live_debounce == 0.2

    def test_live_stdin_with_stream(self) -> None:
        args = self._parse("--live-stdin", "--live-stream")
        assert args.live_stdin is True
        assert args.live_stream is True


class TestStandaloneLiveServerArgParsing:
    def _parse(self, *args: str):
        from intentumdiff.cli import _build_parser

        return _build_parser().parse_args(["live-server", *args])

    def test_defaults_to_stdio_repo_head(self) -> None:
        from intentumdiff.cli import _cmd_live_server

        args = self._parse()

        assert args.repo == "."
        assert args.stdio is False
        assert args.socket is None
        assert args.ref == "HEAD"
        assert args.debounce == 0.15
        assert args.stream is False
        assert args.func is _cmd_live_server

    def test_socket_without_path_uses_auto_generated_address(self) -> None:
        args = self._parse("--socket")
        assert args.socket == ""

    def test_socket_with_path_and_shared_options(self) -> None:
        args = self._parse(
            "repo",
            "--socket",
            "intentumdiff.sock",
            "--ref",
            "origin/main",
            "--debounce",
            "0.4",
            "--stream",
            "--fuel",
            "123",
        )

        assert args.repo == "repo"
        assert args.socket == "intentumdiff.sock"
        assert args.ref == "origin/main"
        assert args.debounce == 0.4
        assert args.stream is True
        assert args.fuel == 123

    def test_cmd_live_server_stdio_constructs_server_with_repo_path(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from intentumdiff import cli

        captured: dict = {}

        class _FakeLiveServer:
            def __init__(self, differ, **kwargs):
                captured.update(kwargs)

            def start_stdin(self) -> None:
                captured["started"] = True

            def stop(self) -> None:
                captured["stopped"] = True

        args = cli._build_parser().parse_args(
            [
                "live-server",
                str(tmp_path),
                "--ref",
                "origin/main",
                "--debounce",
                "0.25",
                "--stream",
            ]
        )

        monkeypatch.setattr("intentumdiff.live_server.LiveServer", _FakeLiveServer)
        monkeypatch.setattr(cli._commands, "SemanticDiffer", lambda cfg: SimpleNamespace(_cache=None))

        cli._cmd_live_server(args)

        assert captured["repo_path"] == tmp_path.resolve()
        assert captured["ref"] == "origin/main"
        assert captured["debounce"] == 0.25
        assert captured["stream_analysis"] is True
        assert captured["started"] is True
        assert captured["stopped"] is True


class TestWatchLiveServerRuntimeWiring:
    def test_watch_live_stdin_constructs_server_with_repo_path(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from intentumdiff import cli

        captured: dict = {}

        class _FakeLiveServer:
            def __init__(self, differ, **kwargs):
                captured.update(kwargs)

            def start_stdin(self) -> None:
                captured["started"] = True

            def stop(self) -> None:
                captured["stopped"] = True

        args = cli._build_parser().parse_args(
            ["watch", str(tmp_path), "--live-stdin", "--ref", "origin/main"]
        )

        monkeypatch.setattr("intentumdiff.live_server.LiveServer", _FakeLiveServer)
        monkeypatch.setattr(cli._commands, "SemanticDiffer", lambda cfg: SimpleNamespace(_cache=None))

        cli._cmd_watch(args)

        assert captured["repo_path"] == tmp_path.resolve()
        assert captured["ref"] == "origin/main"
        assert captured["started"] is True
        assert captured["stopped"] is True

    def test_watch_live_socket_constructs_server_with_repo_path(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from intentumdiff import cli

        captured: dict = {}

        class _FakeLiveServer:
            def __init__(self, differ, **kwargs):
                captured.update(kwargs)

            def start_socket(self, socket_path=None) -> str:
                captured["socket_path"] = socket_path
                return "intentumdiff-test-socket"

            def stop(self) -> None:
                captured["stopped"] = True

        class _FakeWatcher:
            def __init__(self, *args, **kwargs):
                pass

            def start(self) -> None:
                captured["watch_started"] = True

            def wait(self) -> None:
                captured["watch_waited"] = True

            def stop(self) -> None:
                captured["watch_stopped"] = True

        args = cli._build_parser().parse_args(
            [
                "watch",
                str(tmp_path),
                "--live",
                "--live-socket",
                "custom.sock",
                "--ref",
                "origin/main",
            ]
        )

        monkeypatch.setattr("intentumdiff.live_server.LiveServer", _FakeLiveServer)
        monkeypatch.setattr("intentumdiff.watcher.FileWatcher", _FakeWatcher)
        monkeypatch.setattr(cli._commands, "SemanticDiffer", lambda cfg: SimpleNamespace(_cache=None))

        cli._cmd_watch(args)

        assert captured["repo_path"] == tmp_path.resolve()
        assert captured["ref"] == "origin/main"
        assert captured["socket_path"] == "custom.sock"
        assert captured["watch_started"] is True
        assert captured["watch_waited"] is True
