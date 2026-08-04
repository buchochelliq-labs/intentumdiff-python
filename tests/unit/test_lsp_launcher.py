from __future__ import annotations

import asyncio
import socket

import pytest

import intentumdiff.lsp.launcher as launcher_mod
from intentumdiff.lsp.exceptions import LspConnectionError
from intentumdiff.lsp.launcher import LspServerProcess, _reserve_port
from intentumdiff.lsp.servers import KNOWN_SERVER_SPECS, LspServerEntry, LspServerSpec


def test_reserved_lsp_port_cannot_be_co_bound_with_reuseaddr() -> None:
    port, reservation = _reserve_port("127.0.0.1")
    competitor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        competitor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with pytest.raises(OSError):
            competitor.bind(("127.0.0.1", port))
    finally:
        competitor.close()
        reservation.close()


def test_builtin_lsp_specs_no_longer_auto_start_over_tcp() -> None:
    for language in ("python", "go", "ruby", "php"):
        spec = KNOWN_SERVER_SPECS[language]
        assert spec.transport == "stdio"
        assert spec.allow_unverified_tcp_autostart is False


def test_user_defined_tcp_autostart_defaults_to_rejected() -> None:
    entry = LspServerEntry.model_validate(
        {
            "language": "go",
            "transport": "tcp",
            "command": ["gopls", "serve", "-listen", "{host}:{port}"],
        }
    )

    spec = entry.to_spec()

    assert spec.transport == "tcp"
    assert spec.allow_unverified_tcp_autostart is False


def test_manual_tcp_entry_remains_allowed() -> None:
    entry = LspServerEntry.model_validate(
        {
            "language": "go",
            "transport": "tcp",
            "host": "127.0.0.1",
            "port": 2091,
        }
    )

    assert entry.is_manual_connect is True


def test_tcp_autostart_rejected_before_port_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_reserve(_host: str):
        raise AssertionError("port reservation should not run for blocked TCP auto-start")

    monkeypatch.setattr(launcher_mod, "_reserve_port", _fail_reserve)
    spec = LspServerSpec(
        command=("fake-lsp", "--listen", "{host}:{port}"),
        transport="tcp",
    )

    async def _run() -> None:
        with pytest.raises(LspConnectionError, match="disabled by default"):
            await LspServerProcess("fake", spec=spec).start()

    asyncio.run(_run())


def test_tcp_autostart_opt_in_keeps_legacy_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Reservation:
        closed = False

        def close(self) -> None:
            self.closed = True

    class _Proc:
        pid = 4321

        def terminate(self) -> None:
            return None

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:
            return None

    reservation = _Reservation()
    captured: dict[str, object] = {}

    def _fake_reserve(host: str):
        captured["host"] = host
        return 4242, reservation

    async def _fake_create_subprocess_exec(*cmd: str, **kwargs: object) -> _Proc:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _Proc()

    async def _fake_wait_for_port(host: str, port: int, *, timeout: float) -> None:
        captured["wait"] = (host, port, timeout)

    monkeypatch.setattr(launcher_mod, "_reserve_port", _fake_reserve)
    monkeypatch.setattr(launcher_mod, "_resolve_command", lambda cmd, _env: cmd)
    monkeypatch.setattr(
        launcher_mod.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(launcher_mod, "_wait_for_port", _fake_wait_for_port)

    spec = LspServerSpec(
        command=("fake-lsp", "--listen", "{host}:{port}"),
        transport="tcp",
        startup_timeout=3.0,
        allow_unverified_tcp_autostart=True,
    )

    async def _run() -> None:
        proc = LspServerProcess("fake", spec=spec)
        cfg = await proc.start()
        await proc.stop()
        assert cfg.transport == "tcp"
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 4242

    asyncio.run(_run())

    assert captured["cmd"] == ("fake-lsp", "--listen", "127.0.0.1:4242")
    assert captured["wait"] == ("127.0.0.1", 4242, 3.0)
    assert reservation.closed is True
