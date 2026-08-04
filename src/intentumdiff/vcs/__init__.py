"""
intentumdiff.vcs
~~~~~~~~~~~~~~~~~~~~~

VCS backend abstraction layer.

Provides a ``VcsBackend`` abstract base and concrete implementations for the
version-control systems supported by IntentumDiff.

All backends are thin wrappers over the shared Rust ``vcs_backend`` core, which shells the
corresponding CLI (``git`` / ``svn`` / ``hg`` / ``p4``); none require a third-party VCS Python
library.

Exports
-------
:class:`VcsBackend`         Abstract base class for all VCS backends.
:class:`ChangedFile`        Frozen dataclass describing a single file-level change.
:class:`GitVcsBackend`      Git backend (``git`` CLI).
:class:`SvnVcsBackend`      Subversion backend (``svn`` CLI).
:class:`HgVcsBackend`       Mercurial backend (``hg`` CLI).
:class:`PerforceVcsBackend` Perforce backend (``p4`` CLI; connection from env / ``P4CONFIG``).
"""

from intentumdiff.vcs.base import ChangedFile, VcsBackend
from intentumdiff.vcs.git_backend import GitVcsBackend
from intentumdiff.vcs.hg_backend import HgVcsBackend
from intentumdiff.vcs.perforce_backend import PerforceVcsBackend
from intentumdiff.vcs.svn_backend import SvnVcsBackend

__all__ = [
    "VcsBackend",
    "ChangedFile",
    "GitVcsBackend",
    "SvnVcsBackend",
    "HgVcsBackend",
    "PerforceVcsBackend",
]
