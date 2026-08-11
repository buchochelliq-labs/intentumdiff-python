"""Skip markers for capabilities that genuinely do not exist on the running platform.

The condition is read from the PRODUCT, not restated here. `arch_incompatible_reason` is the
same function the registry uses to decide whether to load the parser, so a test can never
disagree with the shipping behaviour - and when the underlying defect is fixed and the entry
is removed from `_ARCH_INCOMPATIBLE_PARSERS`, these tests start running again automatically,
with nobody having to remember they were gated.

That property is the whole point. A skip condition duplicated by hand goes stale silently and
keeps tests switched off long after the reason has gone.

Why a skip rather than asserting the fallback: these tests assert SEMANTIC behaviour - entity
anchoring, index population, move detection across a commit. On a platform where the parser
cannot be loaded there is no semantic result to assert, only token-level output, and rewriting
four tests to assert the absence of the thing they exist to check would leave them passing
while testing nothing.

The reason string is classified in tests/unit/skip_reasons_baseline.json; the skip ratchet
fails the build if it is not.
"""

from __future__ import annotations

import pytest

from intentumdiff.plugins import registry

_POWERSHELL_REASON = registry.arch_incompatible_reason("powershell")

#: Skip when the PowerShell parser cannot be loaded here (Windows/aarch64 - see #42).
powershell_unavailable = pytest.mark.skipif(
    _POWERSHELL_REASON is not None,
    reason=f"powershell parser unavailable on this platform (#42): {_POWERSHELL_REASON}",
)
