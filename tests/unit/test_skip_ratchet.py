"""The skip ratchet (pre-release skip audit, 2026-07-27 maintainer directive).

Every skip reason that appears in the test tree must match an ALLOWED pattern in
`skip_reasons_baseline.json`, where it is classified (platform / env / split-guard /
scenario-matrix / staging) and justified. A new skip with an unclassified reason fails
this gate — skips cannot accumulate silently. Unused baseline entries fail too, so the
allowlist cannot rot.

Scenario-matrix entries are a tracked burn-down backlog, not a licence: shrinking that
class is release work (#45/#65); this gate keeps it visible and bounded.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BASELINE = json.loads((TESTS_DIR / "skip_reasons_baseline.json").read_text(encoding="utf8"))

# reason="..." in markers, pytest.skip("..."), importorskip(..., reason="..."), and the
# construct-matrix helper's `return "skip", f"..."` tuple form — both quote styles,
# f-strings included (dynamic parts stay literal in the pattern match).
_REASON_RE = re.compile(
    r'(?:reason\s*=\s*|pytest\.skip\(\s*|"skip",\s*)(?:f?)'
    r'(?:"((?:[^"\\]|\\.){3,200})"|\'((?:[^\'\\]|\\.){3,200})\')'
)


def _collect_reasons() -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    # Scan ALL python under tests/ (scenario helpers + conftest skip too, not just test_*).
    for path in sorted(TESTS_DIR.parent.rglob("*.py")):
        if path.name == "test_skip_ratchet.py":
            continue
        text = path.read_text(encoding="utf8", errors="replace")
        for match in _REASON_RE.finditer(text):
            reason = match.group(1) or match.group(2)
            reasons.setdefault(reason, []).append(path.name)
    return reasons


def test_every_skip_reason_is_classified_in_the_baseline() -> None:
    patterns = [(e["pattern"], re.compile(e["pattern"])) for e in BASELINE["allowed"]]
    unmatched = {
        reason: files
        for reason, files in _collect_reasons().items()
        if not any(rx.search(reason) for _, rx in patterns)
    }
    assert not unmatched, (
        "Unclassified skip reasons — classify + justify them in "
        f"skip_reasons_baseline.json or fix the underlying gap: {unmatched}"
    )


def test_no_baseline_entry_is_unused() -> None:
    reasons = list(_collect_reasons())
    unused = [
        e["pattern"]
        for e in BASELINE["allowed"]
        if not any(re.search(e["pattern"], reason) for reason in reasons)
    ]
    assert not unused, (
        f"Baseline entries no longer matched by any test — remove them: {unused}"
    )
