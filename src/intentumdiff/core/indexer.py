"""
intentumdiff.core.indexer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

High-level indexing engine that walks a repository (or a set of files),
parses each file, and builds a :class:`~intentumdiff.core.index.SemanticIndex`.

Progress
--------
Every public method accepts an *on_progress* callback that fires before each
file is processed::

    from intentumdiff.core.indexer import Indexer, IndexProgress

    def my_callback(p: IndexProgress) -> None:
        pct = int(p.fraction * 100)
        print(f"[{pct:3d}%]  {p.current_file}")

    indexer = Indexer(differ, on_progress=my_callback)
    result = indexer.index_repo(".")

The callback receives an :class:`IndexProgress` instance *before* each file is
processed (so the caller can display a spinner with the active filename) and a
final "done" event with ``current_file == ""`` when indexing completes.

A ``None`` per-call *on_progress* falls back to the constructor-level default.
Pass a no-op lambda to suppress events for a specific call::

    result = indexer.index_repo(".", on_progress=lambda _: None)

Caching
-------
When the :class:`~intentumdiff.differ.SemanticDiffer` was created with a
``DiffConfig.cache_path``, two levels of caching apply automatically:

* **Parse-tree cache** — each file's :class:`~intentumdiff.core.models.SemanticNode`
  is cached keyed by content hash.  Re-indexing the same file content skips
  the Wasm call entirely.

* **Symbol-index cache** — after :meth:`Indexer.index_repo` finishes, the
  resulting symbol and reference tables are stored in SQLite keyed by
  ``(repo_root, commit_sha)``.  A subsequent call for the *same* commit returns
  the cached index immediately (unless *force=True*).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from intentumdiff.core.diffignore import load_diffignore

if TYPE_CHECKING:
    from intentumdiff.core.index import SemanticIndex
    from intentumdiff.core.models import SemanticNode
    from intentumdiff.differ import SemanticDiffer

logger = logging.getLogger(__name__)

# Maximum number of files enriched concurrently by _index_files_lsp.
# Each slot holds one file open against the LSP server and fires its hover
# requests in parallel (bounded separately by TypeEnricher._MAX_CONCURRENT).
# Raise this if your server is fast and you have many files; lower it to
# reduce server load on shared/slow servers.
_FILE_CONCURRENCY = 8

# Number of threads used to read git blobs in parallel during file collection.
# Git blob reads are pure disk I/O, so threading gives near-linear speed-up
# up to the throughput limit of the storage device.
_BLOB_READ_WORKERS = 32

# Bytes read from each blob for language detection.  Content-based detectors
# (tsql, plsql, adf, dax, postscript …) only need the file header; reading
# the full blob during a language-scan pass wastes bandwidth.
_DETECT_HEAD_BYTES = 2048

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class IndexProgress:
    """
    Snapshot of indexing progress, emitted via the *on_progress* callback.

    The callback fires **before** each file is processed, allowing the UI to
    show the name of the file currently being worked on.  A final event with
    ``current_file == ""`` signals that indexing has completed.
    """

    total: int
    """Total number of candidate files discovered."""

    done: int
    """Files processed so far (includes skipped and errored files)."""

    current_file: str
    """Path of the file about to be processed; empty string in the final event."""

    skipped: int = 0
    """Files that had no registered parser and were silently skipped."""

    errors: int = 0
    """Files that raised an unexpected error during parsing."""

    @property
    def fraction(self) -> float:
        """Progress as a float in ``[0.0, 1.0]``."""
        return self.done / self.total if self.total > 0 else 0.0


@dataclass
class IndexResult:
    """Returned by every :class:`Indexer` indexing method."""

    files_indexed: int
    """Number of files successfully parsed and added to the index."""

    files_skipped: int
    """Number of files skipped because no registered parser was found."""

    errors: list[tuple[str, str]]
    """``(filename, error_message)`` for files that failed to parse."""

    semantic_index: "SemanticIndex"
    """The fully built :class:`~intentumdiff.core.index.SemanticIndex`."""

    from_cache: bool = False
    """``True`` when the symbol index was loaded from the local cache."""

    skipped_files: list[str] = field(default_factory=list)
    """Filenames that were skipped (no registered parser)."""

    type_enriched_count: int = 0
    """Number of nodes that received ``type_info`` from an LSP hover call."""

    languages_found: set[str] = field(default_factory=set)
    """Set of language identifiers successfully parsed in this run."""

    files_ignored: int = 0
    """Number of files excluded by a ``.diffignore`` file."""

    ignored_files: list[str] = field(default_factory=list)
    """Relative paths excluded by ``.diffignore``."""


# Callback type alias — a plain callable so any UX layer can subscribe.
ProgressCallback = Callable[[IndexProgress], None]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_index_key(repo_root: str, commit_sha: str) -> str:
    """Return a stable cache key for ``(repo_root, commit_sha)``."""
    encoded = repo_root.encode() + b"\x00" + commit_sha.encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_catfile_output(
    buf: bytes,
    sha_to_path: "dict[str, str]",
) -> "list[tuple[str, str]]":
    """Parse ``git cat-file --batch`` stdout into ``(path, content)`` pairs."""
    files: list[tuple[str, str]] = []
    i = 0
    n = len(buf)
    while i < n:
        nl = buf.find(b"\n", i)
        if nl == -1:
            break
        header = buf[i:nl].decode("ascii", errors="replace")
        i = nl + 1
        parts = header.split()
        # Expected: "<sha> blob <size>"; skip "missing" / non-blob entries.
        if len(parts) < 3 or parts[1] != "blob":
            continue
        try:
            size = int(parts[2])
        except ValueError:
            continue
        content_bytes = buf[i : i + size]
        i += size + 1  # skip trailing \n
        if b"\x00" in content_bytes[:8192]:  # binary heuristic
            continue
        path = sha_to_path.get(parts[0], "")
        if not path:
            continue
        try:
            content = content_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            content = content_bytes.decode("utf-8", errors="replace")
        files.append((path, content))
    return files


def _collect_blob_files(
    repo_root: str,
    ref: str,
    diffignore: Any,
) -> "tuple[list[tuple[str, str]], list[str]]":
    """Return all text files at *ref* as ``(relative_path, decoded_content)``.

    Shells to the git CLI (#98, A2.4.3): ``git ls-tree -r`` lists every blob and a
    single ``git cat-file --batch`` reads them through git's optimised pack reader.
    Binary blobs (NUL-byte heuristic in :func:`_parse_catfile_output`) are skipped.

    Returns ``(files, ignored_files)`` where *ignored_files* are the paths excluded
    by *diffignore*.
    """
    from intentumdiff.vcs.git_cli import run_git_bytes  # noqa: PLC0415

    ignored_files: list[str] = []
    sha_to_path: dict[str, str] = {}
    # ls-tree -r -z: "<mode> <type> <sha>\t<path>\0" per entry.
    for entry in run_git_bytes(repo_root, ["ls-tree", "-r", "-z", ref]).split(b"\0"):
        if not entry:
            continue
        meta, _tab, raw_path = entry.partition(b"\t")
        parts = meta.split()
        if len(parts) < 3 or parts[1] != b"blob":
            continue
        path = raw_path.decode("utf-8", errors="replace")
        if diffignore and diffignore.is_ignored(path):
            ignored_files.append(path)
            continue
        sha_to_path[parts[2].decode("ascii", errors="replace")] = path

    if not sha_to_path:
        return [], ignored_files

    shas_input = "\n".join(sha_to_path).encode()
    out = run_git_bytes(repo_root, ["cat-file", "--batch"], input_bytes=shas_input)
    return _parse_catfile_output(out, sha_to_path), ignored_files


def _attach_type_info(
    node: "SemanticNode",
    type_map: dict[str, str],
) -> "SemanticNode":
    """Return a copy of *node* tree with ``type_info`` set from *type_map*.

    ``SemanticNode`` is frozen so we must rebuild changed nodes from the leaves
    up.  Only nodes whose id appears in *type_map* are rebuilt.
    """
    from intentumdiff.core.models import SemanticNode  # noqa: PLC0415

    new_children = [_attach_type_info(c, type_map) for c in node.children]
    if node.id in type_map or new_children != list(node.children):
        return node.model_copy(
            update={
                "children": new_children,
                **({"type_info": type_map[node.id]} if node.id in type_map else {}),
            }
        )
    return node


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------


class Indexer:
    """
    Walk a repository (or an explicit list of files) and build a
    :class:`~intentumdiff.core.index.SemanticIndex`.

    Parameters
    ----------
    differ:
        A :class:`~intentumdiff.differ.SemanticDiffer` instance.  Configure
        it with ``DiffConfig.cache_path`` to activate parse-tree and
        symbol-index caching for faster re-indexing.
    on_progress:
        Default :data:`ProgressCallback` applied to every method call that
        accepts *on_progress*.  Individual calls may override this or pass
        ``None`` to suppress progress events for that call.
    """

    def __init__(
        self,
        differ: "SemanticDiffer",
        *,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._differ = differ
        self._on_progress = on_progress

    # ── Public API ──────────────────────────────────────────────────────────

    def detect_languages(
        self,
        repo_path: str | Path = ".",
        ref: str = "HEAD",
        *,
        on_progress: "Callable[[int, int], None] | None" = None,
    ) -> "tuple[set[str], int]":
        """Return the set of languages present in *ref* without parsing any file.

        Reads only the first :data:`_DETECT_HEAD_BYTES` of each blob (enough
        for all content-based detectors) and calls the plugin
        ``detect_language`` heuristics.  Far faster than a full
        :meth:`index_repo` scan — useful as a lightweight pre-pass to decide
        which LSP servers to start before a type-enrichment run.

        *on_progress(done, total)* is called after each blob is processed.
        """
        from intentumdiff.plugins.exceptions import (  # noqa: PLC0415
            PluginNotFoundError,
        )
        from intentumdiff.vcs.git_cli import resolve_repo_root  # noqa: PLC0415

        repo_root = resolve_repo_root(repo_path)
        diffignore = load_diffignore(repo_root)
        files, _ignored = _collect_blob_files(repo_root, ref, diffignore)

        total = len(files)
        if on_progress:
            on_progress(0, total)

        # Detection inspects only the head of each blob (enough for all content-based
        # detectors); content comes from the shared git cat-file batch collector.
        languages: set[str] = set()
        for i, (path, content) in enumerate(files):
            if on_progress:
                on_progress(i + 1, total)
            content_head = content[:_DETECT_HEAD_BYTES]
            try:
                _, lang = self._differ._registry.detect_parser(path, content_head)
                languages.add(lang)
            except PluginNotFoundError:
                pass

        return languages, total

    def index_repo(
        self,
        repo_path: str | Path = ".",
        ref: str = "HEAD",
        *,
        on_progress: ProgressCallback | None = None,
        force: bool = False,
    ) -> IndexResult:
        """
        Parse every text file in a git repository at *ref* and build an index.

        Parameters
        ----------
        repo_path:
            Path to the repository root (or any subdirectory; GitPython will
            locate the root automatically).
        ref:
            Git ref to index.  Defaults to ``"HEAD"``.
        on_progress:
            Progress callback for this call.  Falls back to the callback
            passed to ``__init__`` when ``None``.
        force:
            Re-index even if a cached symbol index already exists for this
            commit.  Useful after a plugin or wasm binary update.

        Returns
        -------
        IndexResult
            Fully built index.  Check :attr:`IndexResult.from_cache` to
            determine whether the symbol table was loaded from cache.
        """
        from intentumdiff.vcs.git_cli import (  # noqa: PLC0415
            resolve_repo_root,
            run_git_bytes,
        )

        repo_root = resolve_repo_root(repo_path)
        commit_sha = (
            run_git_bytes(repo_root, ["rev-parse", "--verify", ref]).decode().strip()
        )
        callback = on_progress or self._on_progress

        # ── Fast path: cached symbol index ────────────────────────────────
        if not force and self._differ._cache is not None:
            index_key = _make_index_key(repo_root, commit_sha)
            cached = self._differ._cache.get_symbol_index(index_key)
            if cached is not None:
                logger.debug(
                    "Symbol index cache hit for %s @ %s",
                    repo_root,
                    commit_sha[:8],
                )
                from intentumdiff.core.index import SemanticIndex  # noqa: PLC0415

                sem_index = SemanticIndex()
                symbols_json, refs_json = cached
                sem_index.load_symbol_table_json(symbols_json)
                sem_index.load_reference_table_json(refs_json)
                return IndexResult(
                    files_indexed=0,
                    files_skipped=0,
                    errors=[],
                    semantic_index=sem_index,
                    from_cache=True,
                )

        # ── Collect all text files in commit tree ─────────────────────────
        diffignore = load_diffignore(repo_root)
        files, ignored_files = _collect_blob_files(repo_root, ref, diffignore)

        result = self._index_files(files, on_progress=callback)
        result.files_ignored = len(ignored_files)
        result.ignored_files = ignored_files

        # ── Persist symbol index ───────────────────────────────────────────
        if self._differ._cache is not None:
            index_key = _make_index_key(repo_root, commit_sha)
            self._store_symbol_index(
                result.semantic_index, index_key, result.files_indexed
            )

        return result

    def index_directory(
        self,
        directory: str | Path,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> IndexResult:
        """
        Parse every text file under *directory* (recursively) and build an index.

        Parse-tree caching applies if the differ has a cache configured.
        Symbol-index caching is **not** applied because there is no stable
        commit key for a plain directory.

        Parameters
        ----------
        directory:
            Root directory to walk recursively.
        on_progress:
            Progress callback for this call.  Falls back to the callback
            passed to ``__init__`` when ``None``.
        """
        directory = Path(directory)
        callback = on_progress or self._on_progress

        diffignore = load_diffignore(directory)
        ignored_files: list[str] = []
        files: list[tuple[str, str]] = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            rel_posix = path.relative_to(directory).as_posix()
            if diffignore and diffignore.is_ignored(rel_posix):
                ignored_files.append(rel_posix)
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files.append((rel_posix, content))

        result = self._index_files(files, on_progress=callback)
        result.files_ignored = len(ignored_files)
        result.ignored_files = ignored_files
        return result

    # ── Internal ────────────────────────────────────────────────────────────

    def _index_files(
        self,
        files: list[tuple[str, str]],
        *,
        on_progress: ProgressCallback | None,
    ) -> IndexResult:
        """Core indexing loop — parse each (filename, content) pair."""
        from intentumdiff.core.index import SemanticIndex  # noqa: PLC0415
        from intentumdiff.plugins.exceptions import (  # noqa: PLC0415
            PluginNotFoundError,
        )

        sem_index = SemanticIndex()
        errors: list[tuple[str, str]] = []
        skipped_files: list[str] = []
        languages_found: set[str] = set()
        done = 0
        total = len(files)

        for filename, content in files:
            # Emit progress *before* processing so the UI shows the active file.
            if on_progress is not None:
                on_progress(
                    IndexProgress(
                        total=total,
                        done=done,
                        current_file=filename,
                        skipped=len(skipped_files),
                        errors=len(errors),
                    )
                )

            try:
                node, language = self._differ.parse(content, filename)
                sem_index.add_tree(filename, language, node)
                languages_found.add(language)
            except PluginNotFoundError:
                skipped_files.append(filename)
            except Exception as exc:  # noqa: BLE001
                errors.append((filename, str(exc)))
                logger.debug("Failed to parse %r: %s", filename, exc)

            done += 1

        # Final event: done == total, current_file empty.
        if on_progress is not None:
            on_progress(
                IndexProgress(
                    total=total,
                    done=done,
                    current_file="",
                    skipped=len(skipped_files),
                    errors=len(errors),
                )
            )

        sem_index.build()
        indexed = max(0, done - len(skipped_files) - len(errors))
        return IndexResult(
            files_indexed=indexed,
            files_skipped=len(skipped_files),
            skipped_files=skipped_files,
            errors=errors,
            semantic_index=sem_index,
            languages_found=languages_found,
        )

    def _store_symbol_index(
        self,
        sem_index: "SemanticIndex",
        index_key: str,
        file_count: int,
    ) -> None:
        """Serialise and persist *sem_index* to the symbol-index cache."""
        cache = self._differ._cache
        if cache is None:
            raise RuntimeError("symbol-index cache store is not configured")
        try:
            symbols_json = json.dumps(
                {k: [d.model_dump() for d in v] for k, v in sem_index.symbols.items()}
            )
            refs_json = json.dumps(
                {k: [r.model_dump() for r in v] for k, v in sem_index.references.items()}
            )
            cache.put_symbol_index(
                index_key,
                symbols_json,
                refs_json,
                file_count=file_count,
            )
            logger.debug("Stored symbol index in cache (key=%s…)", index_key[:12])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to store symbol index in cache: %s", exc)

    async def index_repo_lsp(
        self,
        repo_path: str | Path = ".",
        ref: str = "HEAD",
        *,
        lsp_clients: "dict[str, Any]",
        on_progress: ProgressCallback | None = None,
        force: bool = False,
        file_concurrency: int = _FILE_CONCURRENCY,
    ) -> IndexResult:
        """Like :meth:`index_repo` but attaches LSP type info to each parsed tree.

        Parameters
        ----------
        lsp_clients:
            Map of language name → connected ``AsyncLspClient`` instance.
            Files whose language has no matching client are parsed normally.
        file_concurrency:
            Maximum number of files processed concurrently.  Each slot holds
            one file open against the LSP server while firing its hover
            requests.  Defaults to :data:`_FILE_CONCURRENCY` (8).  Set higher
            for fast local servers, lower for shared or rate-limited servers.
        """
        import asyncio
        from intentumdiff.vcs.git_cli import (  # noqa: PLC0415
            resolve_repo_root,
            run_git_bytes,
        )

        repo_root = resolve_repo_root(repo_path)
        commit_sha = (
            run_git_bytes(repo_root, ["rev-parse", "--verify", ref]).decode().strip()
        )
        callback = on_progress or self._on_progress

        # LSP-enriched indexes are stored under a separate key so they don't
        # collide with plain (non-enriched) index_repo cache entries.
        lsp_index_key = _make_index_key(repo_root, "lsp:" + commit_sha)

        # ── Fast path: cached LSP symbol index ────────────────────────────
        if not force and self._differ._cache is not None:
            cached = self._differ._cache.get_symbol_index(lsp_index_key)
            if cached is not None:
                logger.debug(
                    "LSP symbol index cache hit for %s @ %s",
                    repo_root,
                    commit_sha[:8],
                )
                from intentumdiff.core.index import SemanticIndex  # noqa: PLC0415

                sem_index = SemanticIndex()
                symbols_json, refs_json = cached
                sem_index.load_symbol_table_json(symbols_json)
                sem_index.load_reference_table_json(refs_json)
                return IndexResult(
                    files_indexed=0,
                    files_skipped=0,
                    errors=[],
                    semantic_index=sem_index,
                    from_cache=True,
                )

        # Collect files (parallel blob reads; same filtering as index_repo)
        diffignore = load_diffignore(repo_root)
        files, ignored_files = await asyncio.to_thread(
            _collect_blob_files, repo_root, ref, diffignore
        )

        result = await self._index_files_lsp(
            files,
            lsp_clients=lsp_clients,
            on_progress=callback,
            file_concurrency=file_concurrency,
        )
        result.files_ignored = len(ignored_files)
        result.ignored_files = ignored_files

        if self._differ._cache is not None:
            self._store_symbol_index(result.semantic_index, lsp_index_key, result.files_indexed)

        return result

    async def _index_files_lsp(
        self,
        files: list[tuple[str, str]],
        *,
        lsp_clients: "dict[str, Any]",
        on_progress: ProgressCallback | None,
        file_concurrency: int = _FILE_CONCURRENCY,
    ) -> IndexResult:
        """Async indexing loop — parse then type-enrich files concurrently.

        Files are processed up to *file_concurrency* at a time.  Within each
        file all hover requests are already parallelised by
        :class:`~intentumdiff.lsp.enricher.TypeEnricher`.  After all tasks
        complete, results are merged into the ``SemanticIndex`` sequentially so
        the index itself never sees concurrent writes.
        """
        import asyncio  # noqa: PLC0415
        import concurrent.futures  # noqa: PLC0415

        from intentumdiff.core.index import SemanticIndex  # noqa: PLC0415
        from intentumdiff.lsp.enricher import TypeEnricher  # noqa: PLC0415
        from intentumdiff.plugins.exceptions import PluginNotFoundError  # noqa: PLC0415

        total = len(files)

        # Build enrichers once per language.
        enrichers: dict[str, TypeEnricher] = {
            lang: TypeEnricher(client, language=lang)
            for lang, client in lsp_clients.items()
        }

        # Result type for each file task.
        # Success: (filename, language, node, enriched_count)
        # Skipped: PluginNotFoundError  (re-raised so we can detect it)
        # Error:   any other exception

        sem = asyncio.Semaphore(max(1, file_concurrency))
        cache = self._differ._cache
        differ = self._differ  # captured for executor

        # Use a single-worker executor so Wasm parse calls are serialised.
        # Concurrent Wasm execution across multiple Stores/Engines triggers
        # non-deterministic "bad parameter or other API misuse" errors from
        # wasmtime.  A single worker keeps parse calls sequential while still
        # freeing the event loop so LSP enrich for completed files overlaps
        # with the next file's parse (pipeline parallelism).
        loop = asyncio.get_event_loop()
        parse_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wasm_parse"
        )

        async def _process_one(
            filename: str,
            content: str,
        ) -> "tuple[str, str, Any, int]":
            """Parse + optionally enrich one file; always returns a result tuple.

            Returns ``(filename, language, node, enriched_count)`` on success,
            ``(filename, 'SKIP', exc, 0)`` when no parser is registered, or
            ``(filename, 'ERROR', exc, 0)`` on any other failure.
            """
            async with sem:
                # Run the blocking Wasm parse in a dedicated single-worker
                # thread so the event loop stays free for concurrent LSP I/O.
                # A 1-worker pool prevents concurrent Wasm Store access which
                # causes non-deterministic wasmtime errors.
                try:
                    node, language = await loop.run_in_executor(
                        parse_executor, differ.parse, content, filename
                    )
                except PluginNotFoundError as exc:
                    return filename, "SKIP", exc, 0
                except Exception as exc:
                    return filename, "ERROR", exc, 0

                enricher = enrichers.get(language)
                enriched_count = 0
                if enricher is not None:
                    # Check hover-map cache first.
                    type_map: dict[str, str] | None = None
                    hm_key: str | None = None
                    if cache is not None:
                        hm_key = cache.hover_map_key(content, language)
                        type_map = cache.get_hover_map(hm_key)
                    if type_map is None:
                        try:
                            type_map = await enricher.enrich(filename, content, node)
                        except Exception:
                            type_map = None
                        if type_map and hm_key is not None and cache is not None:
                            cache.put_hover_map(hm_key, type_map)
                    if type_map:
                        enriched_count = len(type_map)
                        node = _attach_type_info(node, type_map)
                return filename, language, node, enriched_count

        tasks = [asyncio.create_task(_process_one(f, c)) for f, c in files]

        # Merge results as tasks complete so progress updates in real time.
        sem_index = SemanticIndex()
        errors: list[tuple[str, str]] = []
        skipped_files: list[str] = []
        languages_found: set[str] = set()
        total_enriched = 0
        done = 0

        for future in asyncio.as_completed(tasks):
            filename, language, payload, enriched_count = await future
            done += 1

            if language == "SKIP":
                skipped_files.append(filename)
            elif language == "ERROR":
                errors.append((filename, str(payload)))
                logger.debug("Failed to process %r: %s", filename, payload)
            else:
                sem_index.add_tree(filename, language, payload)
                languages_found.add(language)
                total_enriched += enriched_count

            if on_progress is not None:
                on_progress(
                    IndexProgress(
                        total=total,
                        done=done,
                        current_file=filename,
                        skipped=len(skipped_files),
                        errors=len(errors),
                    )
                )

        sem_index.build()
        parse_executor.shutdown(wait=False)
        indexed = max(0, done - len(skipped_files) - len(errors))
        return IndexResult(
            files_indexed=indexed,
            files_skipped=len(skipped_files),
            skipped_files=skipped_files,
            errors=errors,
            semantic_index=sem_index,
            languages_found=languages_found,
            type_enriched_count=total_enriched,
        )
