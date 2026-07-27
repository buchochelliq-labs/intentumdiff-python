"""
intentdiff
~~~~~~~~~~~~~~~~

IntentDiff public API package.

Quick start::

    from intentdiff import SemanticDiffer, GitSource, DiffConfig

    differ = SemanticDiffer(DiffConfig(detect_refactorings=True))
    diff = differ.diff(GitSource("/path/to/repo", "src/main.py"))

    print(diff.has_semantic_changes)
    for change in diff.changes:
        print(change.change_type, change.description)
"""

from intentdiff.core.models import (
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
from intentdiff.core.index import SemanticIndex
from intentdiff.core.commit_differ import CommitDiffer
from intentdiff.core.config import find_intentdiff_config, load_project_diff_config
from intentdiff.core.indexer import IndexProgress, IndexResult, Indexer
from intentdiff.differ import SemanticDiffer
from intentdiff.sources.file_source import FileSource
from intentdiff.sources.git_source import GitSource, WorkingTreeSource
from intentdiff.sources.live_buffer_source import LiveBufferSource
from intentdiff.sources.patch_source import PatchSource
from intentdiff.sources.string_source import StringSource
from intentdiff.cache.store import CacheStore
from intentdiff.cache.sqlite_store import SqliteCacheStore
from intentdiff.live_server import LiveServer

__all__ = [
    # Differ
    "SemanticDiffer",
    # Cross-file analysis
    "CommitDiffer",
    "SemanticIndex",
    # Project config
    "find_intentdiff_config",
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
    try:
        from importlib import metadata as importlib_metadata

        return importlib_metadata.version("intentdiff")
    except importlib_metadata.PackageNotFoundError:
        return "0.0.1b1"


__version__ = _installed_version()


def __getattr__(name: str) -> object:
    if name == "PluginTestHarness":
        from intentdiff.testing import PluginTestHarness

        return PluginTestHarness
    raise AttributeError(f"module 'intentdiff' has no attribute {name!r}")
