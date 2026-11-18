"""#327: a named-pipe response is either written completely or fails visibly.

`_write_all` is the platform-independent write loop behind `_NamedPipeConn.sendall`; the
Windows-only tests run it over a real named pipe on the Windows CI runners.
"""

from __future__ import annotations

import ctypes
import sys
import threading

import pytest

from intentumdiff import live_server
from intentumdiff.live_server import _write_all


def test_partial_writes_are_continued_until_everything_is_sent() -> None:
    sent: list[bytes] = []

    def write(chunk: bytes) -> int:
        sent.append(chunk[:3])
        return min(3, len(chunk))

    _write_all(write, b"0123456789")
    assert b"".join(sent) == b"0123456789"
    assert [len(c) for c in sent] == [3, 3, 3, 1]


@pytest.mark.parametrize("failure", [0, None, False])
def test_a_failed_or_empty_write_raises_instead_of_dropping_the_rest(failure) -> None:
    calls: list[bytes] = []

    def write(chunk: bytes):
        calls.append(chunk)
        return 4 if len(calls) == 1 else failure

    with pytest.raises(BrokenPipeError):
        _write_all(write, b"abcdefgh")
    assert calls == [b"abcdefgh", b"efgh"]


def test_an_empty_payload_writes_nothing() -> None:
    _write_all(lambda chunk: pytest.fail("no write expected"), b"")


@pytest.mark.skipif(sys.platform != "win32", reason="Named pipes are Windows-only")
def test_real_named_pipe_large_write_completes_and_disconnect_fails_visibly() -> None:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    name = rf"\\.\pipe\intentumdiff-test-{threading.get_ident()}"
    handle = live_server._create_named_pipe_handle(name)
    payload = b"x" * (3 * 65536 + 17)  # larger than the pipe buffers: needs several writes
    received = bytearray()

    def client() -> None:
        kernel32.WaitNamedPipeW(name, 2000)
        h = kernel32.CreateFileW(
            name, live_server._GENERIC_RW, 0, None, live_server._OPEN_EXISTING, 0, None
        )
        buf, n = ctypes.create_string_buffer(65536), ctypes.c_ulong(0)
        while len(received) < len(payload):
            if not kernel32.ReadFile(h, buf, 65536, ctypes.byref(n), None):
                break
            received.extend(buf.raw[: n.value])
        kernel32.CloseHandle(h)

    reader = threading.Thread(target=client)
    reader.start()
    kernel32.ConnectNamedPipe(handle, None)
    conn = live_server._NamedPipeConn(handle, threading.Event())
    conn.sendall(payload)
    reader.join(10)
    assert bytes(received) == payload
    with pytest.raises(BrokenPipeError):  # the client has gone: not silently dropped
        conn.sendall(b"after disconnect")
    conn.close()
