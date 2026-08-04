"""
intentumdiff.lsp_server._handlers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Server state dataclass and async diff-scheduling helpers used by
``intentumdiff.lsp_server.server``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from intentumdiff.core.models import SemanticDiff
from intentumdiff.differ import SemanticDiffer

try:
    from pygls.lsp.server import LanguageServer  # pygls >= 2.x
except ImportError:
    from pygls.server import LanguageServer  # type: ignore[assignment]  # pygls 1.x

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

@dataclass
class _ServerState:
    """Mutable state shared across all feature handlers for a single server instance."""

    differ: SemanticDiffer
    ref: str
    debounce: float
    diff_cache: dict[str, SemanticDiff] = field(default_factory=dict)
    pending_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    supports_codelens_refresh: bool = False


# ---------------------------------------------------------------------------
# URI helpers
# ---------------------------------------------------------------------------

def uri_to_path(uri: str, workspace_root: Path | None = None) -> Path:
    """Convert a ``file://`` URI to a :class:`~pathlib.Path`.

    Raises :class:`ValueError` for non-file schemes, path-traversal attempts,
    or when the resolved path escapes *workspace_root* (if provided).
    """
    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        raise ValueError(f"Only file:// URIs are supported, got: {uri!r}")
    path = Path(unquote(parsed.path))
    # Reject explicit traversal components
    if ".." in path.parts:
        raise ValueError(f"Path traversal rejected in URI: {uri!r}")
    # On Windows, urlparse puts a leading '/' before the drive letter —
    # strip it so Path('C:/foo') is constructed correctly.
    if path.parts and path.parts[0] in ("/", "\\") and len(path.parts) > 1:
        # Join with "/" so we get an absolute path ("C:/Users/...") rather than
        # a drive-relative one ("C:Users/...") which Path(*parts) would produce.
        candidate = Path("/".join(path.parts[1:]))
        if candidate.drive:
            path = candidate
    resolved = path.resolve()
    if workspace_root is not None:
        root_resolved = workspace_root.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise ValueError(
                f"URI {uri!r} resolves to {resolved!r} which is outside the "
                f"workspace root {root_resolved!r}."
            )
    return resolved


# ---------------------------------------------------------------------------
# Async diff scheduling
# ---------------------------------------------------------------------------

async def schedule_diff(
    server,  # LanguageServer — avoid circular import at module level
    state: _ServerState,
    uri: str,
    content: str,
) -> None:
    """Cancel any in-flight diff for *uri* and schedule a fresh one."""
    existing = state.pending_tasks.get(uri)
    if existing is not None and not existing.done():
        existing.cancel()

    task = asyncio.create_task(_compute_and_push(server, state, uri, content))
    state.pending_tasks[uri] = task


async def _compute_and_push(
    server,
    state: _ServerState,
    uri: str,
    content: str,
) -> None:
    """Debounce, compute diff in a thread-pool, then push results to the client."""
    try:
        await asyncio.sleep(state.debounce)

        loop = asyncio.get_running_loop()
        diff = await loop.run_in_executor(
            None, _do_diff, state, server, uri, content
        )
        if diff is None:
            return

        state.diff_cache[uri] = diff

        # Publish diagnostics (push model — no client request needed).
        from intentumdiff.lsp_server._diagnostics import semantic_diff_to_diagnostics

        diags = semantic_diff_to_diagnostics(diff)
        server.publish_diagnostics(uri, diags)

        # Ask the client to refresh code-lens decorations when supported.
        if state.supports_codelens_refresh:
            try:
                server.lsp.send_request(
                    "workspace/codeLens/refresh",
                    None,
                )
            except Exception:  # noqa: BLE001
                log.debug("intentumdiff: code lens refresh request failed", exc_info=True)

    except asyncio.CancelledError:
        raise  # propagate cancellation — do not swallow
    except Exception as exc:  # noqa: BLE001
        log.warning("intentumdiff: unhandled error during diff for %s: %s", uri, exc)


# ---------------------------------------------------------------------------
# Blocking diff computation (runs in thread-pool executor)
# ---------------------------------------------------------------------------

def _do_diff(
    state: _ServerState,
    server,
    uri: str,
    content: str,
) -> SemanticDiff | None:
    """Compute a ``SemanticDiff`` synchronously.

    Intended to be called via ``loop.run_in_executor`` so the event loop is
    never blocked by parser/wasm work.
    """
    try:
        root_uri: str | None = getattr(server.workspace, "root_uri", None)
        repo_root = uri_to_path(root_uri) if root_uri else Path.cwd()
        file_path = uri_to_path(uri)
        rel_path = file_path.relative_to(repo_root)

        from intentumdiff.sources.live_buffer_source import LiveBufferSource

        source = LiveBufferSource(
            repo_path=repo_root,
            file_path=str(rel_path),
            live_content=content,
            ref=state.ref,
        )
        return state.differ.diff(source)

    except Exception as exc:  # noqa: BLE001
        log.warning("intentumdiff: diff failed for %s: %s", uri, exc)
        return None
