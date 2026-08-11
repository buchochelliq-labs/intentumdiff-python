"""A parser that aborts the process on an architecture must not be loaded there.

WHY THIS EXISTS

Loading `powershell_parser.wasm` on aarch64 does not raise - it kills the interpreter:

    thread '<unnamed>' panicked at crates/cranelift/src/obj.rs:332:17: function too large

wasmtime's compiler panics, and a Rust panic built with panic=abort cannot be caught from
Python. There is no try/except that helps; the process is gone, with no traceback, taking the
CLI, the LSP server or the editor extension with it.

Found by running the suite on windows-11-arm for the first time (#9). 72 of 73 components load
there; that one aborts alone, and no wasmtime setting avoids it - opt_level none and speed,
simd off, relaxed_simd off, parallel compilation off and tail_call off all abort identically.

The exclusion is therefore load-bearing rather than cosmetic, and it is the kind of guard that
gets "tidied away" by someone who cannot reproduce the crash on their laptop. Hence a test that
states what it costs.
"""

from __future__ import annotations

from unittest import mock

import pytest

from intentumdiff.plugins import registry


@pytest.mark.parametrize("machine", ["ARM64", "aarch64", "AArch64"])
def test_powershell_is_excluded_on_every_spelling_of_arm64(machine: str) -> None:
    """Windows reports ARM64, Linux and macOS report aarch64. Case varies.

    A gate that only matched one spelling would pass on the platform that happened to be
    tested and abort on the others.
    """
    with mock.patch.object(registry.platform, "machine", return_value=machine):
        reason = registry.arch_incompatible_reason("powershell")
    assert reason, f"powershell must be excluded on {machine}"
    assert "aborts the process" in reason, "the reason must say the process DIES, not that it degrades"


@pytest.mark.parametrize("machine", ["x86_64", "AMD64", "arm64e"])
def test_powershell_is_available_everywhere_else(machine: str) -> None:
    """The exclusion must not leak onto architectures that are fine.

    `arm64e` is Apple's pointer-authentication variant and is deliberately NOT in the set:
    only machines where the abort was actually observed belong there. Guessing wider costs
    users a language for no evidence.
    """
    with mock.patch.object(registry.platform, "machine", return_value=machine):
        assert registry.arch_incompatible_reason("powershell") is None


def test_no_other_parser_is_excluded_anywhere() -> None:
    """Exactly one entry, and it is the one we measured.

    Every entry here removes a language from someone's install, so the list must never grow by
    accident or by precaution - only by an observed abort.
    """
    assert set(registry._ARCH_INCOMPATIBLE_PARSERS) == {"powershell"}

    for machine in ("ARM64", "aarch64", "x86_64", "AMD64"):
        with mock.patch.object(registry.platform, "machine", return_value=machine):
            for parser in ("python", "rust", "markdown", "json", "yaml"):
                assert registry.arch_incompatible_reason(parser) is None, (
                    f"{parser} must never be excluded (checked on {machine})"
                )


def test_the_exclusion_is_announced_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    """A missing language must be visible, or it looks like a broken diff.

    Without this the user sees a PowerShell file report no semantic changes and has no way to
    learn why. That is the same shape as the 0.0.1 defects: a capability quietly absent while
    everything reports success.
    """
    import logging

    entry = mock.Mock()
    entry.name = "powershell"

    with mock.patch.object(registry.platform, "machine", return_value="ARM64"), \
            mock.patch.object(registry.importlib.metadata, "entry_points", return_value=[entry]), \
            caplog.at_level(logging.WARNING):
        registry._discover_parser_catalog()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "excluding a parser must warn; a silent downgrade is indistinguishable from a bug"
    joined = " ".join(r.getMessage() for r in warnings)
    assert "powershell" in joined.lower()
    assert "token-level" in joined, "the user needs to know what they get INSTEAD, not just what is missing"
