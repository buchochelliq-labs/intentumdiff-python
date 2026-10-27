"""Source-derived, transport-only C ABI regressions for release 0.0.2.

Rust's success_envelope always sends {"ok": true, "result": ...}.  Python
must reject malformed native responses rather than fabricating success.
The stub models native bytes at the ctypes boundary, not Rust semantics.
"""

import ctypes

import pytest

from intentumdiff import rust_core


def _stub_native_envelope(monkeypatch, raw: bytes) -> None:
    class Stub:
        def __init__(self) -> None:
            # Keep the backing bytes alive until the C ABI consumer frees them.
            self.buffer = ctypes.create_string_buffer(raw)
            self.free_count = 0

        def intentumdiff_call(self, operation: bytes, arguments: bytes) -> int:
            assert operation == b"supports_language"
            assert arguments == b'["python"]'
            return ctypes.addressof(self.buffer)

        def intentumdiff_free(self, pointer: int) -> None:
            assert pointer == ctypes.addressof(self.buffer)
            self.free_count += 1

    stub = Stub()
    monkeypatch.setattr(rust_core, "_c_abi_lib", lambda: stub)
    return stub


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"ok":true}', "success missing 'result'"),
        (b'{"ok":"false","result":[]}', "expected a boolean 'ok'"),
        (b'{"ok":1,"result":[]}', "expected a boolean 'ok'"),
        (b"null", "expected a boolean 'ok'"),
    ],
)
def test_malformed_abi_envelope_never_becomes_success(monkeypatch, raw, message):
    stub = _stub_native_envelope(monkeypatch, raw)
    with pytest.raises(RuntimeError, match=message):
        rust_core._c_abi_call("supports_language", "python")
    assert stub.free_count == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b'{"ok":true,"result":null}', None),
        (b'{"ok":true,"result":false}', False),
        (b'{"ok":true,"result":[]}', []),
    ],
)
def test_explicit_rust_result_preserves_null_false_and_empty(monkeypatch, raw, expected):
    stub = _stub_native_envelope(monkeypatch, raw)
    assert rust_core._c_abi_call("supports_language", "python") == expected
    assert stub.free_count == 1


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("not_found", FileNotFoundError),
        ("internal", RuntimeError),
        ("value_error", ValueError),
    ],
)
def test_rust_error_kind_is_not_reclassified(monkeypatch, kind, error):
    raw = ('{"ok":false,"error":"core rejected input","error_type":"' + kind + '"}').encode()
    stub = _stub_native_envelope(monkeypatch, raw)
    with pytest.raises(error, match="core rejected input"):
        rust_core._c_abi_call("supports_language", "python")
    assert stub.free_count == 1
