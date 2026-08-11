"""A parser that aborts the process on a platform must not be loaded there.

WHY THIS EXISTS

Loading `powershell_parser.wasm` on Windows/aarch64 does not raise - it kills the interpreter:

    thread '<unnamed>' panicked at crates/cranelift/src/obj.rs:332:17: function too large

wasmtime's compiler panics, and a Rust panic built with panic=abort cannot be caught from
Python. No try/except helps; the process is gone, without a traceback, taking the CLI, the LSP
server or the editor extension with it.

Found by running the suite on windows-11-arm for the first time (#9). 72 of 73 components load
there; that one aborts alone, and no wasmtime setting avoids it - opt_level none and speed,
simd off, relaxed_simd off, parallel compilation off and tail_call off all abort identically.

THE MISTAKE THIS FILE NOW GUARDS

The first version of the exclusion keyed on ARCHITECTURE alone. macOS on Apple Silicon is also
aarch64, so it lost PowerShell for every Mac user - despite macOS arm64 loading all 73
components with zero aborts, measured on the same CI run. The rule is OS *and* architecture,
and a test that only checked "aarch64 is excluded" would have called that correct.
"""

from __future__ import annotations

import logging
from unittest import mock

import pytest

from intentumdiff.plugins import registry


def _on(system: str, machine: str):
    """Pretend to be a platform, for both of the calls the gate makes."""
    return (
        mock.patch.object(registry.platform, "system", return_value=system),
        mock.patch.object(registry.platform, "machine", return_value=machine),
    )


@pytest.mark.parametrize("machine", ["ARM64", "aarch64", "AArch64"])
def test_powershell_is_excluded_on_every_spelling_of_windows_arm(machine: str) -> None:
    """Windows reports ARM64; case and spelling vary between reporting paths.

    A gate matching one spelling would pass wherever it was tested and abort elsewhere.
    """
    sys_p, mach_p = _on("Windows", machine)
    with sys_p, mach_p:
        reason = registry.arch_incompatible_reason("powershell")
    assert reason, f"powershell must be excluded on Windows/{machine}"
    assert "aborts the process" in reason, "the reason must say the process DIES, not that it degrades"


@pytest.mark.parametrize(
    ("system", "machine"),
    [
        ("Darwin", "arm64"),   # Apple Silicon: ALSO aarch64, and demonstrably fine
        ("Darwin", "arm64e"),
        ("Linux", "aarch64"),
        ("Windows", "AMD64"),
        ("Linux", "x86_64"),
    ],
)
def test_powershell_stays_available_everywhere_it_works(system: str, machine: str) -> None:
    """The exclusion must not leak onto platforms where the component loads.

    macOS/arm64 is the case that matters: it is aarch64, and an architecture-only gate took
    PowerShell away from every Mac. Measured on the same CI run that found the Windows abort,
    macOS arm64 loaded 73 of 73 components with zero aborts.
    """
    sys_p, mach_p = _on(system, machine)
    with sys_p, mach_p:
        assert registry.arch_incompatible_reason("powershell") is None, (
            f"powershell must remain available on {system}/{machine}"
        )


def test_no_other_parser_is_excluded_on_any_platform() -> None:
    """Exactly one entry, and only the one measured.

    Every entry removes a language from someone's install, so this list must never grow by
    precaution - only by an observed abort.
    """
    assert set(registry._ARCH_INCOMPATIBLE_PARSERS) == {"powershell"}

    for system, machine in (("Windows", "ARM64"), ("Darwin", "arm64"), ("Linux", "x86_64")):
        sys_p, mach_p = _on(system, machine)
        with sys_p, mach_p:
            for parser in ("python", "rust", "markdown", "json", "yaml"):
                assert registry.arch_incompatible_reason(parser) is None, (
                    f"{parser} must never be excluded (checked on {system}/{machine})"
                )


def test_discovery_is_silent_so_an_ordinary_diff_stays_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """Excluding a parser must not put text on stderr during an unrelated diff.

    Parser discovery runs on the first diff of ANY file. Warning there told someone diffing
    Python that PowerShell was unavailable - noise beside a correct result, which is exactly
    what made 0.0.1 look broken, and it failed
    test_cli_header_and_stderr.py::test_a_successful_diff_logs_nothing_at_warning_or_above.
    """
    entry = mock.Mock()
    entry.name = "powershell"

    sys_p, mach_p = _on("Windows", "ARM64")
    with sys_p, mach_p, \
            mock.patch.object(registry.importlib.metadata, "entry_points", return_value=[entry]), \
            caplog.at_level(logging.DEBUG):
        registry._discover_parser_catalog()

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "discovery must not warn; the user is told at the point of use instead"
    )
    assert [r for r in caplog.records if r.levelno == logging.DEBUG], (
        "it should still be visible at DEBUG for anyone diagnosing"
    )


@pytest.mark.parametrize("filename", ["deploy.ps1", "Module.psm1", "Manifest.psd1", "DEPLOY.PS1"])
def test_the_user_is_told_when_they_diff_the_affected_language(filename: str) -> None:
    """Silence is only acceptable if the explanation arrives where it is relevant.

    Without this the user sees "unknown language" on a PowerShell file and has no way to learn
    why - indistinguishable from the product being broken.
    """
    sys_p, mach_p = _on("Windows", "ARM64")
    with sys_p, mach_p:
        found = registry.unavailable_parser_for(filename)
    assert found is not None, f"{filename} should be explained on Windows/ARM64"
    name, reason = found
    assert name == "powershell"
    assert "aborts the process" in reason


@pytest.mark.parametrize("filename", ["main.py", "lib.rs", "README.md", "deploy.ps1"])
def test_nothing_is_explained_away_on_a_platform_that_works(filename: str) -> None:
    """Including .ps1 itself: on macOS arm64 the parser loads, so there is nothing to explain."""
    sys_p, mach_p = _on("Darwin", "arm64")
    with sys_p, mach_p:
        assert registry.unavailable_parser_for(filename) is None
