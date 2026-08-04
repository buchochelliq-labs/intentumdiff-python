"""
intentumdiff
~~~~~~~~~~~~~~~~

IntentumDiff public API package.

Quick start::

    from intentumdiff import SemanticDiffer, GitSource, DiffConfig

    differ = SemanticDiffer(DiffConfig(detect_refactorings=True))
    diff = differ.diff(GitSource("/path/to/repo", "src/main.py"))

    print(diff.has_semantic_changes)
    for change in diff.changes:
        print(change.change_type, change.description)
"""

from intentumdiff.core.models import (
    Change,
    ChangeGroup,
    ChangeGroupKind,
    ChangeStreamEvent,
    ChangeStreamPhase,
    ChangeType,
    CommitDiff,
    CrossFileChange,
    DetectionResult,
    DiffConfig,
    EditDelta,
    FUEL_UNLIMITED,
    GuardrailCheckResult,
    GuardrailSeverity,
    GuardrailViolation,
    NodePosition,
    RefactoringKind,
    ReferenceKind,
    ReferenceUsage,
    SemanticDiff,
    SemanticNode,
    SymbolDefinition,
)
from intentumdiff.core.index import SemanticIndex
from intentumdiff.core.commit_differ import CommitDiffer
from intentumdiff.core.config import find_intentumdiff_config, load_project_diff_config
from intentumdiff.core.indexer import IndexProgress, IndexResult, Indexer
from intentumdiff.differ import SemanticDiffer
from intentumdiff.sources.file_source import FileSource
from intentumdiff.sources.git_source import GitSource, WorkingTreeSource
from intentumdiff.sources.live_buffer_source import LiveBufferSource
from intentumdiff.sources.patch_source import PatchSource
from intentumdiff.sources.string_source import StringSource
from intentumdiff.cache.store import CacheStore
from intentumdiff.cache.sqlite_store import SqliteCacheStore
from intentumdiff.live_server import LiveServer

__all__ = [
    # Differ
    "SemanticDiffer",
    # Cross-file analysis
    "CommitDiffer",
    "SemanticIndex",
    # Project config
    "find_intentumdiff_config",
    "load_project_diff_config",
    # Indexing
    "Indexer",
    "IndexProgress",
    "IndexResult",
    # Sources
    "GitSource",
    "WorkingTreeSource",
    "FileSource",
    "LiveBufferSource",
    "StringSource",
    "PatchSource",
    # Models
    "DiffConfig",
    "DetectionResult",
    "FUEL_UNLIMITED",
    "SemanticDiff",
    "Change",
    "ChangeGroup",
    "ChangeGroupKind",
    "ChangeStreamEvent",
    "ChangeStreamPhase",
    "ChangeType",
    "EditDelta",
    "GuardrailCheckResult",
    "GuardrailSeverity",
    "GuardrailViolation",
    "RefactoringKind",
    "SemanticNode",
    "NodePosition",
    "CommitDiff",
    "CrossFileChange",
    "SymbolDefinition",
    "ReferenceKind",
    "ReferenceUsage",
    # Cache
    "CacheStore",
    "SqliteCacheStore",
    # Indexing
    "Indexer",
    # Live server
    "LiveServer",
    # Testing
    "PluginTestHarness",
]

def _installed_version() -> str:
    from importlib import metadata as importlib_metadata

    # The distribution is "intentumdiff-python"; the import package is "intentumdiff".
    # Looking up the import name finds no metadata once this is installed from a release
    # wheel, which silently pinned __version__ to the literal below. "intentumdiff" is
    # kept as a second candidate so pre-rename installs still report their real version.
    for distribution in ("intentumdiff-python", "intentumdiff"):
        try:
            return importlib_metadata.version(distribution)
        except importlib_metadata.PackageNotFoundError:
            continue
    return "0.0.1"


__version__ = _installed_version()


def __getattr__(name: str) -> object:
    if name == "PluginTestHarness":
        from intentumdiff.testing import PluginTestHarness

        return PluginTestHarness
    raise AttributeError(f"module 'intentumdiff' has no attribute {name!r}")
