"""
tests/unit/test_registry_thread_safety.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Stress-tests the double-checked locking in PluginRegistry to verify that
concurrent access to the ``parsers`` and ``renderers`` lazy-loaded properties
never produces None or an inconsistent result.

The test uses ``threading.Barrier`` to force all threads to hit the lazy-init
path simultaneously, maximising the chance of exposing a race condition.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from intentdiff.plugins.registry import PluginRegistry
from intentdiff.core.models import DiffConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry() -> PluginRegistry:
    """Return a fresh PluginRegistry whose _load_parsers is mocked to a fast stub."""
    return PluginRegistry(DiffConfig())


# ---------------------------------------------------------------------------
# Thread-safety tests
# ---------------------------------------------------------------------------


class TestRegistryThreadSafety:
    def test_parsers_loaded_exactly_once_under_concurrency(self):
        """
        N threads all access registry.parsers at the same instant.
        The load function must be called exactly once (double-checked locking).
        """
        N = 16
        call_count = 0
        load_lock = threading.Lock()

        def fake_load_parsers(
            fuel: int = 10_000_000,
            *,
            load_errors: list[str] | None = None,
        ):
            nonlocal call_count
            with load_lock:
                call_count += 1
            return []

        registry = PluginRegistry(DiffConfig())
        barrier = threading.Barrier(N)
        results: list[object] = [None] * N
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                barrier.wait()  # force all threads to start simultaneously
                results[i] = registry.parsers
            except Exception as exc:
                errors.append(exc)

        with patch(
            "intentdiff.plugins.registry._load_parsers",
            side_effect=fake_load_parsers,
        ):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Thread errors: {errors}"
        assert call_count == 1, (
            f"_load_parsers was called {call_count} times; expected exactly 1 "
            "(double-checked locking is broken)"
        )
        # All threads must see the same list object (same reference)
        first = results[0]
        assert all(r is first for r in results), "Not all threads saw the same parsers list"

    def test_renderers_loaded_exactly_once_under_concurrency(self):
        """Same guarantee for the renderers property."""
        N = 16
        call_count = 0
        load_lock = threading.Lock()

        def fake_load_renderers(fuel: int = 10_000_000):
            nonlocal call_count
            with load_lock:
                call_count += 1
            return []

        registry = PluginRegistry(DiffConfig())
        barrier = threading.Barrier(N)
        results: list[object] = [None] * N
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                barrier.wait()
                results[i] = registry.renderers
            except Exception as exc:
                errors.append(exc)

        with patch(
            "intentdiff.plugins.registry._load_renderers",
            side_effect=fake_load_renderers,
        ):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Thread errors: {errors}"
        assert call_count == 1, (
            f"_load_renderers was called {call_count} times; expected exactly 1"
        )
        first = results[0]
        assert all(r is first for r in results)

    def test_parsers_and_renderers_can_be_loaded_concurrently(self):
        """Accessing parsers and renderers from different threads simultaneously is safe."""
        N = 8
        p_results: list[object] = [None] * N
        r_results: list[object] = [None] * N
        errors: list[Exception] = []

        registry = PluginRegistry(DiffConfig())
        barrier = threading.Barrier(N * 2)

        def parser_worker(i: int) -> None:
            try:
                barrier.wait()
                p_results[i] = registry.parsers
            except Exception as exc:
                errors.append(exc)

        def renderer_worker(i: int) -> None:
            try:
                barrier.wait()
                r_results[i] = registry.renderers
            except Exception as exc:
                errors.append(exc)

        with (
            patch("intentdiff.plugins.registry._load_parsers", return_value=[]),
            patch("intentdiff.plugins.registry._load_renderers", return_value=[]),
        ):
            threads = (
                [threading.Thread(target=parser_worker, args=(i,)) for i in range(N)]
                + [threading.Thread(target=renderer_worker, args=(i,)) for i in range(N)]
            )
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Thread errors: {errors}"
        assert all(r is not None for r in p_results)
        assert all(r is not None for r in r_results)
