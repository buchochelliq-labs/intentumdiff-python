"""#323: `intentumdiff serve` preflight binds with the host's address family."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import pytest

from intentumdiff.cli import _build_parser
from intentumdiff.cli._commands import _cmd_serve

pytest.importorskip("uvicorn")


def _ipv6_loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
        return True
    except OSError:
        return False


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    import uvicorn

    import intentumdiff.serve as serve

    run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", run)
    monkeypatch.setattr(serve, "create_app", lambda **_kw: object())
    return run


def _serve(*argv: str) -> None:
    _cmd_serve(_build_parser().parse_args(["serve", *argv]))


def test_ipv4_loopback_launches(served) -> None:
    _serve("--host", "127.0.0.1", "--port", "0")
    assert served.call_args.kwargs["host"] == "127.0.0.1"


def test_ipv6_loopback_launches_or_fails_with_a_diagnostic(served, capsys) -> None:
    # Both outcomes are real-platform behaviour: launch where ::1 can be bound, otherwise a
    # deliberate error (never the old "Address family for hostname not supported" traceback).
    if _ipv6_loopback_available():
        _serve("--host", "::1", "--port", "0")
        assert served.call_args.kwargs["host"] == "::1"
        assert "http://[::1]:" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as exc_info:
            _serve("--host", "::1", "--port", "0")
        assert exc_info.value.code == 1
        assert "cannot bind '::1'" in capsys.readouterr().err
        served.assert_not_called()


def test_ipv6_host_preflight_uses_af_inet6(served, monkeypatch) -> None:
    families: list[int] = []
    real_socket = socket.socket

    class _Recording(real_socket):
        def __init__(self, family=-1, *args, **kwargs):
            families.append(family)
            super().__init__(socket.AF_INET, *args, **kwargs)

        def bind(self, address):
            return super().bind(("127.0.0.1", address[1]))

    monkeypatch.setattr(socket, "socket", _Recording)
    _serve("--host", "::1", "--port", "0")
    assert families == [socket.AF_INET6]


def test_nonloopback_still_requires_allow_remote(served, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _serve("--host", "0.0.0.0", "--port", "0")
    assert exc_info.value.code == 1
    assert "--allow-remote" in capsys.readouterr().err
    served.assert_not_called()
