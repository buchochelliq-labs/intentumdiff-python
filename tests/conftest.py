"""
tests/conftest.py — shared fixtures.
"""

from __future__ import annotations

import os
import sys
import textwrap
import gc
import weakref
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# ---------------------------------------------------------------------------
# Set INTENTDIFF_ALLOW_VULNERABLE_WASMTIME=1 at import time so benchmark modules
# that run availability probes during collection keep working in unsynced
# local environments. The normal lockfile path uses wasmtime>=45.0.
# The session fixture below restores the original value after the run.
# ---------------------------------------------------------------------------
os.environ.setdefault("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", "1")

_GIT_REPO_REFS: list[weakref.ReferenceType[Any]] = []
_ORIGINAL_GIT_REPO_INIT: Any = None


def _install_git_repo_tracker() -> None:
    """Track real GitPython repos so tests can close persistent cat-file helpers."""
    global _ORIGINAL_GIT_REPO_INIT
    if _ORIGINAL_GIT_REPO_INIT is not None:
        return
    try:
        import git
    except Exception:
        return

    _ORIGINAL_GIT_REPO_INIT = git.Repo.__init__

    def _tracked_init(self: Any, *args: Any, **kwargs: Any) -> None:
        _ORIGINAL_GIT_REPO_INIT(self, *args, **kwargs)
        _GIT_REPO_REFS.append(weakref.ref(self))

    git.Repo.__init__ = _tracked_init


def _restore_git_repo_tracker() -> None:
    global _ORIGINAL_GIT_REPO_INIT
    if _ORIGINAL_GIT_REPO_INIT is None:
        return
    with suppress(Exception):
        import git

        git.Repo.__init__ = _ORIGINAL_GIT_REPO_INIT
    _ORIGINAL_GIT_REPO_INIT = None


def _dispose_git_auto_interrupt(cmd: Any) -> None:
    proc = getattr(cmd, "proc", None)
    with suppress(Exception):
        cmd.proc = None
    if proc is None:
        return

    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, stream_name, None)
        if stream is not None:
            with suppress(Exception):
                stream.close()

    with suppress(Exception):
        if proc.poll() is None:
            with suppress(Exception):
                proc.terminate()
            with suppress(Exception):
                proc.wait(timeout=1)
    with suppress(Exception):
        if proc.poll() is None:
            with suppress(Exception):
                proc.kill()
            with suppress(Exception):
                proc.wait(timeout=1)

    # GitPython can leave a Popen object with an already-invalid Windows handle.
    # Mark it as no longer owning a child so subprocess.__del__ has no poll work.
    with suppress(Exception):
        proc._child_created = False


def _clear_tracked_git_repos() -> None:
    for repo_ref in list(_GIT_REPO_REFS):
        repo = repo_ref()
        if repo is None:
            continue
        git_cmd = getattr(repo, "git", None)
        if git_cmd is None:
            continue
        for attr in ("cat_file_all", "cat_file_header"):
            cmd = getattr(git_cmd, attr, None)
            if cmd is not None:
                _dispose_git_auto_interrupt(cmd)
                with suppress(Exception):
                    setattr(git_cmd, attr, None)
    _GIT_REPO_REFS.clear()


# ---------------------------------------------------------------------------
# Allow Wasm plugins to load during tests even when a local environment has not
# yet synced the fixed wasmtime. Tests that specifically verify the security
# policy clear this env var themselves.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _allow_vulnerable_wasmtime_in_tests():
    """Keep non-policy plugin tests runnable in stale local environments."""
    _install_git_repo_tracker()
    prev = os.environ.get("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME")
    os.environ["INTENTDIFF_ALLOW_VULNERABLE_WASMTIME"] = "1"
    yield
    _clear_tracked_git_repos()
    _restore_git_repo_tracker()
    gc.collect()
    if prev is None:
        os.environ.pop("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", None)
    else:
        os.environ["INTENTDIFF_ALLOW_VULNERABLE_WASMTIME"] = prev


# ---------------------------------------------------------------------------
# Python source fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def py_old() -> str:
    return textwrap.dedent("""\
        def greet(name):
            print("Hello, " + name)

        class Greeter:
            def run(self):
                greet("world")
    """)


@pytest.fixture
def py_new_rename() -> str:
    """Rename 'greet' to 'say_hello' — should detect RENAME_SYMBOL."""
    return textwrap.dedent("""\
        def say_hello(name):
            print("Hello, " + name)

        class Greeter:
            def run(self):
                say_hello("world")
    """)


@pytest.fixture
def py_new_style_only() -> str:
    """Same semantics, different whitespace/comments."""
    return textwrap.dedent("""\
        # greet the user
        def greet(name):
            print("Hello, " + name)  # output

        class Greeter:
            def run(self):
                greet("world")
    """)


@pytest.fixture
def py_new_extract() -> str:
    """Extract a helper function from greet — should detect EXTRACT_FUNCTION."""
    return textwrap.dedent("""\
        def _build_message(name):
            return "Hello, " + name

        def greet(name):
            print(_build_message(name))

        class Greeter:
            def run(self):
                greet("world")
    """)


# ---------------------------------------------------------------------------
# SQL source fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sql_old() -> str:
    return "SELECT id, name FROM users WHERE active = 1;"


@pytest.fixture
def sql_new_column_add() -> str:
    return "SELECT id, name, email FROM users WHERE active = 1;"


# ---------------------------------------------------------------------------
# Minimal SemanticNode factory
# ---------------------------------------------------------------------------


@pytest.fixture
def make_node():
    from intentdiff.core.models import NodePosition, SemanticNode

    def _make(
        node_type: str = "identifier",
        label: str = "foo",
        *,
        id: str = "0",
        start_line: int = 0,
        start_col: int = 0,
        end_line: int = 0,
        end_col: int = 10,
        structural_hash: str = "abc123",
        children: list | None = None,
    ) -> SemanticNode:
        return SemanticNode(
            id=id,
            node_type=node_type,
            label=label,
            position=NodePosition(
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
            ),
            structural_hash=structural_hash,
            children=children or [],
        )

    return _make
