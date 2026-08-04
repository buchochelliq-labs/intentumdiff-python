"""
tests/unit/test_lsp_server.py — unit tests for the LSP server module.

Tests cover:
  - ``semantic_diff_to_diagnostics`` translation (5 tests)
  - ``semantic_diff_to_codelens`` translation (6 tests)
  - ``uri_to_path`` security / correctness (3 tests)
  - ``create_server`` factory (2 tests)

All translation tests use in-process model construction — no pygls running.
``create_server`` tests skip gracefully when pygls is not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared model helpers
# ---------------------------------------------------------------------------

from intentumdiff.core.models import (
    Change,
    ChangeType,
    NodePosition,
    RefactoringKind,
    SemanticDiff,
    SemanticNode,
)


def _pos(sl: int = 0, sc: int = 0, el: int = 1, ec: int = 10) -> NodePosition:
    return NodePosition(start_line=sl, start_col=sc, end_line=el, end_col=ec)


def _node(label: str = "foo", sl: int = 0, sc: int = 0, el: int = 1, ec: int = 10) -> SemanticNode:
    return SemanticNode(
        id="n-" + label,
        node_type="function",
        label=label,
        position=_pos(sl, sc, el, ec),
        structural_hash="abc123",
    )


def _diff(*changes: Change, parse_errors: list[str] | None = None) -> SemanticDiff:
    return SemanticDiff(
        old_filename="test.py",
        new_filename="test.py",
        language="python",
        changes=list(changes),
        parse_errors=parse_errors or [],
    )


# ===========================================================================
# TestDiagnosticsTranslation
# ===========================================================================

from intentumdiff.lsp_server._diagnostics import (
    node_to_lsp_range,
    semantic_diff_to_diagnostics,
)


class TestDiagnosticsTranslation:
    def test_empty_diff_produces_no_diagnostics(self):
        result = semantic_diff_to_diagnostics(_diff())
        assert result == []

    def test_style_only_change_with_new_node_produces_hint(self):
        node = _node("bar", sl=3, sc=4, el=5, ec=8)
        change = Change(
            change_type=ChangeType.STYLE_ONLY,
            new_node=node,
            description="whitespace",
        )
        diags = semantic_diff_to_diagnostics(_diff(change))
        assert len(diags) == 1
        d = diags[0]
        from lsprotocol import types as lsp_types

        assert d.severity == lsp_types.DiagnosticSeverity.Hint
        assert "Style-only" in d.message
        assert d.source == "intentumdiff"
        assert d.range.start.line == 3
        assert d.range.start.character == 4
        assert d.range.end.line == 5
        assert d.range.end.character == 8

    def test_style_only_without_new_node_skipped(self):
        """A STYLE_ONLY change with no new_node must not produce a diagnostic."""
        change = Change(
            change_type=ChangeType.STYLE_ONLY,
            old_node=_node("old"),
            description="comment removed",
        )
        assert semantic_diff_to_diagnostics(_diff(change)) == []

    def test_non_style_only_change_produces_no_diagnostic(self):
        change = Change(
            change_type=ChangeType.MODIFICATION,
            old_node=_node("x"),
            new_node=_node("x"),
            description="body changed",
        )
        assert semantic_diff_to_diagnostics(_diff(change)) == []

    def test_parse_error_produces_warning_at_full_doc_range(self):
        from lsprotocol import types as lsp_types

        diags = semantic_diff_to_diagnostics(
            _diff(parse_errors=["SyntaxError at line 7"])
        )
        assert len(diags) == 1
        d = diags[0]
        assert d.severity == lsp_types.DiagnosticSeverity.Warning
        assert "SyntaxError at line 7" in d.message
        assert d.range.start.line == 0
        assert d.range.start.character == 0


# ===========================================================================
# TestCodeLensTranslation
# ===========================================================================

from intentumdiff.lsp_server._codelens import semantic_diff_to_codelens


class TestCodeLensTranslation:
    def test_empty_diff_produces_no_lenses(self):
        assert semantic_diff_to_codelens(_diff()) == []

    def test_addition_change_produces_no_lens(self):
        change = Change(
            change_type=ChangeType.ADDITION,
            new_node=_node("new_fn"),
            description="added",
        )
        assert semantic_diff_to_codelens(_diff(change)) == []

    def test_deletion_change_produces_no_lens(self):
        change = Change(
            change_type=ChangeType.DELETION,
            old_node=_node("removed_fn"),
            description="removed",
        )
        assert semantic_diff_to_codelens(_diff(change)) == []

    def test_refactoring_change_produces_lens(self):
        change = Change(
            change_type=ChangeType.REFACTORING,
            old_node=_node("old_fn"),
            new_node=_node("new_fn"),
            refactoring_kind=RefactoringKind.EXTRACT_FUNCTION,
            description="extracted helper",
        )
        lenses = semantic_diff_to_codelens(_diff(change))
        assert len(lenses) == 1
        # refactoring_kind.name is used in the title when present
        assert "EXTRACT_FUNCTION" in lenses[0].command.title

    def test_refactoring_kind_in_lens_title(self):
        change = Change(
            change_type=ChangeType.REFACTORING,
            old_node=_node("compute"),
            new_node=_node("calculate"),
            refactoring_kind=RefactoringKind.RENAME_METHOD,
            description="compute → calculate",
        )
        lenses = semantic_diff_to_codelens(_diff(change))
        assert len(lenses) == 1
        title = lenses[0].command.title
        assert "RENAME_METHOD" in title
        assert "compute → calculate" in title

    def test_move_change_uses_new_node_for_range(self):
        old = _node("fn", sl=0, sc=0, el=5, ec=0)
        new = _node("fn", sl=10, sc=0, el=15, ec=0)
        change = Change(
            change_type=ChangeType.MOVE,
            old_node=old,
            new_node=new,
            description="moved down",
        )
        lenses = semantic_diff_to_codelens(_diff(change))
        assert len(lenses) == 1
        assert lenses[0].range.start.line == 10  # new_node preferred

    def test_reorder_change_falls_back_to_old_node(self):
        old = _node("fn", sl=2, sc=0, el=4, ec=0)
        change = Change(
            change_type=ChangeType.REORDER,
            old_node=old,
            description="reordered",
        )
        lenses = semantic_diff_to_codelens(_diff(change))
        assert len(lenses) == 1
        assert lenses[0].range.start.line == 2  # old_node fallback


# ===========================================================================
# TestUriToPath
# ===========================================================================

from intentumdiff.lsp_server._handlers import uri_to_path


class TestUriToPath:
    def test_valid_file_uri_round_trips(self):
        if sys.platform == "win32":
            uri = "file:///C:/Users/test/project/main.py"
            path = uri_to_path(uri)
            assert path.name == "main.py"
        else:
            uri = "file:///home/user/project/main.py"
            path = uri_to_path(uri)
            assert path == Path("/home/user/project/main.py")

    def test_uri_with_spaces_decoded(self):
        if sys.platform == "win32":
            uri = "file:///C:/Users/my%20project/main.py"
        else:
            uri = "file:///home/user/my%20project/main.py"
        path = uri_to_path(uri)
        assert "my project" in str(path)

    def test_path_traversal_rejected(self):
        uri = "file:///home/user/../../../etc/passwd"
        with pytest.raises(ValueError, match="traversal"):
            uri_to_path(uri)

    def test_non_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="file://"):
            uri_to_path("http://example.com/file.py")


# ===========================================================================
# TestServerCreation
# ===========================================================================

pygls = pytest.importorskip("pygls", reason="pygls not installed — skipping server tests")


class TestServerCreation:
    def test_create_server_returns_language_server_instance(self):
        try:
            from pygls.lsp.server import LanguageServer  # pygls >= 2.x
        except ImportError:
            from pygls.server import LanguageServer  # type: ignore[assignment]

        from intentumdiff.lsp_server import create_server

        server = create_server()
        assert isinstance(server, LanguageServer)

    def test_create_server_accepts_custom_config(self):
        from intentumdiff.core.models import DiffConfig
        from intentumdiff.lsp_server import create_server

        cfg = DiffConfig()
        server = create_server(config=cfg, ref="main", debounce=0.5)
        assert server is not None


# ===========================================================================
# TestSemanticDiffContainment
# ===========================================================================


def _get_semantic_diff_handler(server):
    """Return the inner ``_semantic_diff`` function from the feature manager.

    pygls may store the handler directly or inside a list depending on the
    version; this helper normalises both cases.
    """
    feat = server.protocol.fm._features.get("intentumdiff/semanticDiff")
    if feat is None:
        raise KeyError("intentumdiff/semanticDiff handler not registered")
    # pygls 1.x: list of handlers; 2.x: may be the function directly
    if isinstance(feat, list):
        return feat[0]
    return feat


class TestSemanticDiffContainment:
    """Workspace-root containment guard in the intentumdiff/semanticDiff handler.

    The handler must:
    - Return an error when ``server.workspace.root_uri`` is None.
    - Return an error when ``root_uri`` uses a non-``file://`` scheme (treated
      as an invalid root, same error path as None).
    - Return a path-containment error when a URI escapes the workspace root.
    - Pass silently through the containment check when both URIs are inside
      the workspace root.
    """

    @pytest.fixture()
    def _srv(self):
        from intentumdiff.lsp_server import create_server

        server = create_server()
        handler = _get_semantic_diff_handler(server)
        return server, handler

    @staticmethod
    def _ws_patch(server, root_uri):
        """Context manager that patches ``server.workspace`` (a read-only property)."""
        from unittest.mock import MagicMock, PropertyMock

        ws = MagicMock()
        ws.root_uri = root_uri
        return patch.object(type(server), "workspace", new_callable=PropertyMock, return_value=ws)

    def test_null_workspace_root_returns_error(self, _srv):
        server, handler = _srv
        with self._ws_patch(server, None):
            result = handler({"oldUri": "file:///a/old.py", "newUri": "file:///b/new.py"})
        assert "error" in result
        assert "workspace root" in result["error"]

    def test_non_file_workspace_root_returns_error(self, _srv):
        server, handler = _srv
        with self._ws_patch(server, "http://not-a-file-uri"):
            result = handler({"oldUri": "file:///a/old.py", "newUri": "file:///b/new.py"})
        assert "error" in result
        assert "workspace root" in result["error"]

    def test_uri_outside_root_returns_containment_error(self, _srv, tmp_path):
        server, handler = _srv
        root = tmp_path / "project"
        root.mkdir()
        # Both paths are siblings of the workspace root, not inside it
        outside_old = tmp_path / "evil" / "old.py"
        outside_new = tmp_path / "evil" / "new.py"
        with self._ws_patch(server, root.as_uri()):
            result = handler({"oldUri": outside_old.as_uri(), "newUri": outside_new.as_uri()})
        assert "error" in result
        assert "outside" in result["error"]

    def test_uri_inside_root_passes_containment(self, _srv, tmp_path):
        server, handler = _srv
        root = tmp_path / "project"
        root.mkdir()
        inside_old = root / "old.py"
        inside_new = root / "new.py"
        inside_old.write_text("x = 1\n", encoding="utf-8")
        inside_new.write_text("x = 2\n", encoding="utf-8")
        with self._ws_patch(server, root.as_uri()):
            result = handler({"oldUri": inside_old.as_uri(), "newUri": inside_new.as_uri()})
        # Containment check passed — any error must not be about escaping the root
        if "error" in result:
            assert "outside" not in result["error"]
            assert "requires a valid workspace root" not in result["error"]


# ===========================================================================
# Rust core parity (issue 100 S3 — the LSP shape mappings in the core)
# ===========================================================================


class TestRustShapeParity:
    """`lsp_server_codelens_json` / `lsp_server_diagnostics_json` mirror the Python
    mappings wire-shape-for-wire-shape (lsprotocol's converter is the oracle encoder)."""

    def _parity_diff(self) -> SemanticDiff:
        return _diff(
            Change(
                change_type=ChangeType.REFACTORING,
                new_node=_node("renamed", 2, 0, 5, 1),
                confidence=1.0,
                description="Rename foo to bar",
                refactoring_kind=RefactoringKind.RENAME_SYMBOL,
            ),
            Change(
                change_type=ChangeType.MOVE,
                old_node=_node("moved", 7, 2, 9, 0),
                confidence=0.9,
                description="Move block",
            ),
            Change(
                change_type=ChangeType.MODIFICATION,
                new_node=_node("edited"),
                confidence=1.0,
                description="edited",
            ),
            Change(
                change_type=ChangeType.STYLE_ONLY,
                new_node=_node("styled", 4, 0, 4, 10),
                confidence=1.0,
                description="ws",
            ),
            Change(change_type=ChangeType.STYLE_ONLY, confidence=1.0, description="ws"),
            parse_errors=["unexpected token"],
        )

    def _backend_fn(self, name: str):
        import intentumdiff.rust_core as rust_core

        backend = rust_core._load_backend()
        fn = getattr(backend, name, None)
        if not callable(fn):
            pytest.skip(f"rust core without {name}")
        return fn

    def test_codelens_parity(self) -> None:
        import json as _json

        from lsprotocol.converters import get_converter

        diff = self._parity_diff()
        rust = _json.loads(self._backend_fn("lsp_server_codelens_json")(diff.model_dump_json()))
        conv = get_converter()
        python = [conv.unstructure(lens) for lens in semantic_diff_to_codelens(diff)]
        assert rust == python
        assert len(rust) == 2  # the scenario keeps both mapping branches live

    def test_diagnostics_parity(self) -> None:
        import json as _json

        from lsprotocol.converters import get_converter

        diff = self._parity_diff()
        rust = _json.loads(
            self._backend_fn("lsp_server_diagnostics_json")(diff.model_dump_json())
        )
        conv = get_converter()
        python = [conv.unstructure(d) for d in semantic_diff_to_diagnostics(diff)]
        assert rust == python
        assert len(rust) == 2  # one style hint + one parse-error warning
