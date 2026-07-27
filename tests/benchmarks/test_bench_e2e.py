"""
tests/benchmarks/test_bench_e2e.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

End-to-end Wasm-plugin benchmarks for large-file diffing and plugin dispatch
overhead.

Closes backlog item:
  "Performance benchmarks (diffing 10,000-line files; plugin dispatch overhead)"

Structure
---------
TestPluginDispatch
    Measures the fixed per-call Wasm dispatch overhead using a tiny 1-line
    input so that parse + diff work is negligible.  Both 'warm' (shared
    SemanticDiffer instance) and 'cold' (fresh SemanticDiffer per round)
    variants are included for the generic and Python parsers.

TestParseLargeFile
    Diffs identical old/new files (zero changes), isolating parse and
    structural-hash cost from the matching and edit-script passes.

TestDiffLargeFile  ← PRIMARY backlog closure
    Full pipeline (parse → Phase 1 → Phase 2 → edit script → analysis) on
    a ~10 500-line Python file with ~10 % renames, ~4 % deletions, and 50
    new functions added.

The ``python`` language is always available in this project (it is registered
as a built-in entry-point).  A module-level availability probe still skips
Python-specific tests gracefully if the built-in is somehow absent.  The
``generic`` parser is used for the dispatch-overhead tests to avoid
Python-parser-specific variance.

Run:
    pytest tests/benchmarks/test_bench_e2e.py \\
        --benchmark-columns=mean,stddev,rounds --benchmark-sort=mean -v
"""

from __future__ import annotations

import pytest

from intentdiff import DiffConfig, SemanticDiffer, StringSource

from tests.benchmarks.helpers import make_python_source


# ---------------------------------------------------------------------------
# Module-level pre-built sources  (constructed once, reused every round)
# ---------------------------------------------------------------------------

_SRC_SMALL = "x = 1\n"
_SRC_SMALL_MOD = "x = 2\n"

# ~1 050-line Python module (medium — fits within default 4 MB CST limit)
_SRC_MEDIUM = make_python_source(65)
_SRC_MEDIUM_MOD = make_python_source(65, modified=True)

# ~10 500-line Python module (large — exceeds default max_cst_bytes; use _DIFFER_LARGE)
_SRC_LARGE = make_python_source(650)
_SRC_LARGE_MOD = make_python_source(650, modified=True)

# Warm differ with default limits (medium tests)
_DIFFER = SemanticDiffer()
# Warm differ with increased CST limit for 10k-line files
_DIFFER_LARGE = SemanticDiffer(
    DiffConfig(max_cst_bytes=16 * 1024 * 1024, max_nodes=100_000)
)


# ---------------------------------------------------------------------------
# Python-parser availability probe
# ---------------------------------------------------------------------------

def _check_python_available() -> bool:
    try:
        _DIFFER.diff(StringSource(_SRC_SMALL, _SRC_SMALL_MOD, "probe.py"))
        return True
    except Exception:
        return False


def _check_generic_available() -> bool:
    try:
        _DIFFER.diff(
            StringSource("hello world\n", "hello earth\n", "probe.txt",
                         language_hint="generic")
        )
        return True
    except Exception:
        return False


_PYTHON_AVAILABLE = _check_python_available()
_GENERIC_AVAILABLE = _check_generic_available()

_SKIP_NO_PYTHON = pytest.mark.skipif(
    not _PYTHON_AVAILABLE,
    reason="Python Wasm parser not installed",
)
_SKIP_NO_GENERIC = pytest.mark.skipif(
    not _GENERIC_AVAILABLE,
    reason="Generic Wasm parser not available in this environment",
)


# ---------------------------------------------------------------------------
# TestPluginDispatch — isolate per-call Wasm dispatch overhead
# ---------------------------------------------------------------------------

class TestPluginDispatch:
    """
    Measure the fixed Wasm-plugin dispatch overhead using a tiny 1-line input
    so that parse + diff work is negligible.

    'warm' means the SemanticDiffer instance (and its PluginRegistry) is
    shared across all rounds.  'cold' means a fresh SemanticDiffer is
    created inside each timed round, capturing any plugin-loading penalty.
    """

    @_SKIP_NO_GENERIC
    def test_roundtrip_generic_warm(self, benchmark):
        """Warm generic: single diff call on a 1-line input (generic parser)."""
        src = StringSource(
            "hello world\n", "hello earth\n", "bench.txt", language_hint="generic"
        )
        benchmark(_DIFFER.diff, src)

    @_SKIP_NO_GENERIC
    def test_roundtrip_generic_cold(self, benchmark):
        """Cold generic: new SemanticDiffer + diff on 1-line input per round."""
        def _cold():
            d = SemanticDiffer()
            return d.diff(
                StringSource(
                    "hello world\n", "hello earth\n", "bench.txt",
                    language_hint="generic",
                )
            )
        benchmark(_cold)

    @_SKIP_NO_PYTHON
    def test_roundtrip_python_warm(self, benchmark):
        """Warm Python: single diff call on a 1-line Python snippet."""
        src = StringSource(_SRC_SMALL, _SRC_SMALL_MOD, "bench.py")
        benchmark(_DIFFER.diff, src)

    @_SKIP_NO_PYTHON
    def test_roundtrip_python_cold(self, benchmark):
        """Cold Python: new SemanticDiffer + diff on 1-line Python per round."""
        def _cold():
            d = SemanticDiffer()
            return d.diff(StringSource(_SRC_SMALL, _SRC_SMALL_MOD, "bench.py"))
        benchmark(_cold)


# ---------------------------------------------------------------------------
# TestParseLargeFile — parse cost in isolation (old == new, zero changes)
# ---------------------------------------------------------------------------

class TestParseLargeFile:
    """
    Full pipeline with identical old and new content — measures parse and
    structural-hash computation cost without matching or edit-script work.

    Because old == new, Phase 1 trivially matches every node, Phase 2 has
    nothing to do, and the edit script is empty.
    """

    @_SKIP_NO_PYTHON
    def test_parse_medium_warm(self, benchmark):
        """Parse ~1 050-line Python file (old == new, warm differ, default limits)."""
        src = StringSource(_SRC_MEDIUM, _SRC_MEDIUM, "bench.py")
        benchmark(_DIFFER.diff, src)

    @_SKIP_NO_PYTHON
    def test_parse_large_warm(self, benchmark):
        """Parse ~10 500-line Python file (old == new, warm differ, 16 MB CST limit)."""
        src = StringSource(_SRC_LARGE, _SRC_LARGE, "bench.py")
        benchmark(_DIFFER_LARGE.diff, src)


# ---------------------------------------------------------------------------
# TestDiffLargeFile — full end-to-end diff (primary backlog closure)
# ---------------------------------------------------------------------------

class TestDiffLargeFile:
    """
    Full diff pipeline (parse → Phase 1 → Phase 2 → edit script → analysis)
    on large modified source files.

    This class directly closes the backlog item:
      "Performance benchmarks (diffing 10,000-line files; plugin dispatch overhead)"

    Modified source has ~10 % renamed functions, ~4 % deleted functions, and
    50 newly appended functions — a realistic mix of changes.

    'warm' variants reuse ``_DIFFER`` across rounds.
    'cold' creates a fresh ``SemanticDiffer`` inside each timed round,
    measuring whether plugin initialisation adds latency at file-diff scale.
    """

    @_SKIP_NO_PYTHON
    def test_diff_medium_warm(self, benchmark):
        """Full diff on ~1 050-line Python files (warm differ, default limits)."""
        src = StringSource(_SRC_MEDIUM, _SRC_MEDIUM_MOD, "bench.py")
        benchmark(_DIFFER.diff, src)

    @_SKIP_NO_PYTHON
    def test_diff_large_warm(self, benchmark):
        """Full diff on ~10 500-line Python files (warm differ, 16 MB CST limit)."""
        src = StringSource(_SRC_LARGE, _SRC_LARGE_MOD, "bench.py")
        benchmark(_DIFFER_LARGE.diff, src)

    @_SKIP_NO_PYTHON
    def test_diff_large_cold(self, benchmark):
        """Cold: fresh SemanticDiffer (16 MB limit) per round on ~10 500-line files."""
        cfg = DiffConfig(max_cst_bytes=16 * 1024 * 1024, max_nodes=100_000)
        def _cold():
            d = SemanticDiffer(cfg)
            return d.diff(StringSource(_SRC_LARGE, _SRC_LARGE_MOD, "bench.py"))
        benchmark(_cold)
