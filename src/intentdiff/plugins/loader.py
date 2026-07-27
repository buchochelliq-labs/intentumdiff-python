"""
intentdiff.plugins.loader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wasm Component Model loader.

Uses wasmtime.component.Component + wasmtime.component.Linker to load and
instantiate plugins built with wasm32-wasip2 (WebAssembly Component Model).

Security properties
────────────────────
- ``WasiConfig()`` grants zero capabilities (no dirs, env, stdio, args).
- ``store.set_fuel(n)`` caps CPU; traps are caught and re-raised as typed
  Python exceptions.
- All dynamic content from the plugin is treated as untrusted.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as _importlib_metadata
import json
import logging
import os
import ssl
import threading
import time as _time
import urllib.request
from pathlib import Path
from typing import Any

from wasmtime import Config, Engine, Store, WasiConfig, WasmtimeError
from wasmtime.component import Component, Linker

from intentdiff.plugins.exceptions import (
    PluginFuelExhausted,
    PluginLoadError,
    PluginOutputError,
    PluginSandboxViolation,
)

logger = logging.getLogger(__name__)
_MAX_TELEMETRY_RECORDS = 128

# ---------------------------------------------------------------------------
# Wasmtime vulnerability blocklist
# ---------------------------------------------------------------------------

# Exact PyPI versions of wasmtime that are known-vulnerable.  Add new entries
# here when new CVEs are disclosed; each entry should carry a comment with the
# advisory ID so the reason is never ambiguous.  The *runtime* gate uses this
# fast offline check.  For a live OSV scan run: ``intentdiff security-check``.
_VULNERABLE_WASMTIME_VERSIONS: frozenset[str] = frozenset({
    "44.0.0",   # CVE-2026-44216 / GHSA-p8xm-42r7-89xg — host panic via memory64
})
_WASMTIME_ADVISORY = "CVE-2026-44216 / GHSA-p8xm-42r7-89xg"
_WASMTIME_ADVISORY_URL = (
    "https://github.com/bytecodealliance/wasmtime/security/advisories/"
    "GHSA-p8xm-42r7-89xg"
)
_BUILTIN_WASM_DIR = (Path(__file__).parent.parent / "wasm").resolve()


def _is_trusted_wasm_path(wasm_path: str | Path | None) -> bool:
    """Return true only for bundled first-party Wasm assets in this package."""
    if wasm_path is None:
        return False
    try:
        path = Path(wasm_path).resolve()
        return path.is_relative_to(_BUILTIN_WASM_DIR)
    except (OSError, RuntimeError, ValueError):
        return False


#: The #89 provenance manifest shipped alongside the bundled parsers (filename -> SHA-256).
_PROVENANCE_MANIFEST = _BUILTIN_WASM_DIR / "wasm_provenance.json"
#: Opt-in to make a bundled-wasm provenance mismatch a hard load failure (release/CI). Off by
#: default so a dev tree without the manifest — or with a stale one after a manual wasm copy —
#: is not broken; the mismatch is still logged.
_ENFORCE_PROVENANCE_ENV = "INTENTDIFF_ENFORCE_WASM_PROVENANCE"


def _verify_builtin_provenance(path: Path) -> None:
    """Verify a bundled first-party Wasm against the #89 provenance manifest, when present.

    Parallels hub.py's third-party checksum path, but for the first-party bundled parsers: a
    mismatch — or an artifact the manifest never saw (the #87 stale-artifact case) — is a
    supply-chain red flag. It is logged as a warning, and raised as a ``PluginLoadError`` when
    ``INTENTDIFF_ENFORCE_WASM_PROVENANCE=1``. A missing/unreadable manifest (a dev build without
    the package-time provenance step) is skipped — verification is optional by design (#89).
    """
    if not _is_trusted_wasm_path(path) or not _PROVENANCE_MANIFEST.is_file():
        return
    try:
        manifest = json.loads(_PROVENANCE_MANIFEST.read_text(encoding="utf-8"))
        artifacts = manifest.get("artifacts", {})
    except (OSError, ValueError):
        return  # unreadable manifest — do not block on a broken dev artifact
    if not isinstance(artifacts, dict):
        return
    name = Path(path).name
    expected = artifacts.get(name)
    problem: str | None = None
    if expected is None:
        problem = f"bundled Wasm {name!r} is not listed in the provenance manifest"
    else:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if digest != expected.get("sha256"):
            problem = f"bundled Wasm {name!r} does not match its provenance SHA-256"
    if problem is None:
        return
    if os.environ.get(_ENFORCE_PROVENANCE_ENV) == "1":
        raise PluginLoadError(str(path), problem)
    logger.warning(
        "Wasm provenance check: %s (set %s=1 to make this a hard failure).",
        problem,
        _ENFORCE_PROVENANCE_ENV,
    )


def _check_wasmtime_version(
    wasm_path: str | Path | None = None,
    *,
    trusted: bool = False,
) -> None:
    """Refuse to load plugins under known-vulnerable wasmtime versions.

    wasmtime==44.0.0 is affected by CVE-2026-44216, which allows a malicious
    Wasm module to panic the host process by allocating an extremely large
    WebAssembly table under the default on-demand allocator with memory64.

    The normal dependency path now requires a fixed Python binding
    (wasmtime>=45.0).  This offline runtime gate remains as defense-in-depth
    for stale or manually pinned environments.

    See: https://github.com/bytecodealliance/wasmtime/security/advisories/GHSA-p8xm-42r7-89xg
    """
    try:
        version = _importlib_metadata.version("wasmtime")
    except _importlib_metadata.PackageNotFoundError:
        # wasmtime not installed as a proper package — skip version gate.
        return

    if version not in _VULNERABLE_WASMTIME_VERSIONS:
        return

    override = os.environ.get("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", "").strip()
    if override in ("1", "true", "yes"):
        logger.warning(
            "INTENTDIFF_ALLOW_VULNERABLE_WASMTIME is set: loading Wasm plugin under "
            "wasmtime==%s which is in the known-vulnerable blocklist (%s). "
            "See: %s",
            version,
            _WASMTIME_ADVISORY,
            _WASMTIME_ADVISORY_URL,
        )
        return

    raise PluginLoadError(
        "<wasmtime-version-check>",
        f"wasmtime=={version} is in the known-vulnerable blocklist "
        f"({_WASMTIME_ADVISORY} — host-process panic on large table allocation "
        f"with memory64). Upgrade wasmtime or set INTENTDIFF_ALLOW_VULNERABLE_WASMTIME=1 "
        f"to override (not recommended). Run 'intentdiff security-check' for a live "
        f"OSV scan. See: {_WASMTIME_ADVISORY_URL}",
    )


# ---------------------------------------------------------------------------
# OSV advisory cache  (once-per-day background fetch, API-friendly)
# ---------------------------------------------------------------------------
#
# Design: two separate files in the user cache directory.
#
#   osv_cache.json        — the actual OSV results (vuln list + checked_at)
#   osv_last_fetch.stamp  — epoch timestamp of the last *fetch attempt*
#
# The 24-hour rate limit is enforced by the STAMP file, not by the cache.
# Deleting osv_cache.json clears the displayed results but does NOT unlock
# a new fetch — the stamp still gates it.  This prevents cache-delete loops
# from hammering the OSV API.  The stamp is written BEFORE the network
# request so that even a failed/timed-out call counts against the budget.
#
# To force a manual refresh (e.g. from `intentdiff security-check --refresh`):
#   delete osv_last_fetch.stamp explicitly — that is the documented escape.

_OSV_FETCH_INTERVAL = 86_400  # 24 hours — one fetch per day, hard-coded

_PACKAGES_TO_AUDIT = (
    "wasmtime",
    "intentdiff",
    "fastapi",
    "starlette",
    "cryptography",
    "defusedxml",
)


def _osv_dir() -> Path:
    """Platform-appropriate cache directory for OSV advisory files."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "intentdiff"


def _osv_cache_path() -> Path:
    return _osv_dir() / "osv_cache.json"


def _osv_stamp_path() -> Path:
    """Fetch-gate stamp — rate-limits OSV calls independently of the cache."""
    return _osv_dir() / "osv_last_fetch.stamp"


def _read_stamp() -> float:
    """Return the epoch time recorded in the stamp file, or 0.0 if absent."""
    try:
        return float(_osv_stamp_path().read_text(encoding="utf-8").strip())
    except Exception:
        return 0.0


def _write_stamp() -> None:
    """Record the current time as the last fetch attempt (called BEFORE fetch)."""
    p = _osv_stamp_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(_time.time()), encoding="utf-8")
    except Exception:
        logger.debug("Could not write OSV stamp file", exc_info=True)


def _load_osv_cache() -> list[dict] | None:
    """Return cached vuln list if the cache file exists, else None."""
    try:
        data = json.loads(_osv_cache_path().read_text(encoding="utf-8"))
        return data.get("vulns", [])
    except Exception:
        return None


def _save_osv_cache(vulns: list[dict]) -> None:
    p = _osv_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"checked_at": _time.time(), "vulns": vulns}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("Could not save OSV advisory cache", exc_info=True)


def query_osv(packages: list[tuple[str, str]]) -> list[dict]:
    """Query the OSV batch API for a list of (package, version) pairs.

    Returns a flat list of finding dicts, each with keys:
      package, version, id, aliases, summary.

    Public so that ``intentdiff security-check`` can call it directly.
    Raises ``urllib.error.URLError`` or ``OSError`` on network failure.
    """
    queries = [
        {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
        for name, version in packages
    ]
    body = json.dumps({"queries": queries}).encode()
    osv_url = "https://api.osv.dev/v1/querybatch"
    if not osv_url.startswith("https://"):  # defense-in-depth (#76 semgrep): urllib accepts file://
        raise RuntimeError("OSV query URL must be https")
    req = urllib.request.Request(
        osv_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "intentdiff/osv-check",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    logger.info(
        "OSV API request: querying %d package(s): %s",
        len(packages),
        ", ".join(f"{n}=={v}" for n, v in packages),
    )
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- literal https constant asserted above. noqa: S310  # nosec B310 - https scheme asserted above
        data = json.loads(resp.read())
    logger.info("OSV API response received (%d result(s))", len(data.get("results", [])))

    vulns: list[dict] = []
    for (pkg, ver), result in zip(packages, data.get("results", [])):
        for v in result.get("vulns", []):
            vulns.append({
                "package": pkg,
                "version": ver,
                "id": v.get("id", ""),
                "aliases": v.get("aliases", []),
                "summary": v.get("summary", ""),
            })
    return vulns


def _log_osv_findings(vulns: list[dict], *, cached: bool) -> None:
    tag = " (cached)" if cached else ""
    for v in vulns:
        pkg_ver = f"{v['package']}=={v['version']}"
        aliases = ", ".join(v.get("aliases", []))
        logger.warning(
            "OSV advisory%s: %s affects %s — %s%s. "
            "Run 'intentdiff security-check' for details.",
            tag,
            v["id"],
            pkg_ver,
            v.get("summary", ""),
            f" (also: {aliases})" if aliases else "",
        )


def _collect_audit_packages() -> list[tuple[str, str]]:
    """Return (package, version) pairs for all auditable installed packages."""
    packages: list[tuple[str, str]] = []
    for pkg in _PACKAGES_TO_AUDIT:
        try:
            packages.append((pkg, _importlib_metadata.version(pkg)))
        except _importlib_metadata.PackageNotFoundError:
            pass
    return packages


def _osv_check_worker() -> None:
    """Background daemon thread: write stamp, fetch OSV, save cache, log.

    The stamp is written BEFORE the network call so that a timeout or
    network failure still counts against the 24-hour budget.  On failure,
    no cache file is written, which is detectable by
    ``_check_osv_cache_or_block()`` as a "known-failed fetch".
    """
    packages = _collect_audit_packages()
    if not packages:
        return

    _write_stamp()
    try:
        vulns = query_osv(packages)
    except Exception as exc:
        logger.debug("OSV background check failed (network?): %s", exc)
        # No cache written — the absent cache + fresh stamp signals failure.
        return

    _save_osv_cache(vulns)
    if vulns:
        _log_osv_findings(vulns, cached=False)


def refresh_osv_cache() -> list[dict]:
    """Synchronously refresh the OSV advisory cache, bypassing the 24h gate.

    Deletes the stamp file first, then performs a blocking OSV fetch, saves
    the result, and returns the vuln list.  Called by
    ``intentdiff security-check --refresh``.

    Raises ``urllib.error.URLError`` or ``OSError`` on network failure.
    The stamp is still written before the fetch so that a failure counts
    against the rate limit (prevents rapid retry loops).
    """
    try:
        _osv_stamp_path().unlink()
    except FileNotFoundError:
        pass

    packages = _collect_audit_packages()
    if not packages:
        return []

    _write_stamp()
    vulns = query_osv(packages)  # raises on network failure
    _save_osv_cache(vulns)
    return vulns


def _check_osv_cache_or_block(
    wasm_path: str | Path | None = None,
    *,
    trusted: bool = False,
) -> None:
    """Sync gate at plugin-load time: block if the last OSV fetch failed.

    Logic:
    - Stamp absent / stale (>24h): proceed optimistically; background thread
      will refresh.  We have no reason to block.
    - Stamp fresh + cache present: validate against cached advisories.
    - Stamp fresh + cache ABSENT: last fetch failed within 24h.  Block until
      the user runs ``intentdiff security-check --refresh`` or sets the env override.
    """
    stamp_age = _time.time() - _read_stamp()
    if stamp_age >= _OSV_FETCH_INTERVAL:
        # Stamp stale or absent — background thread will handle it.
        return

    cache = _load_osv_cache()
    if cache is None:
        # Stamp is fresh but no cache file: the last fetch failed.
        override = os.environ.get("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", "").strip()
        if override in ("1", "true", "yes"):
            logger.warning(
                "INTENTDIFF_ALLOW_VULNERABLE_WASMTIME: loading plugins with "
                "unverified OSV status — last advisory fetch failed."
            )
            return
        if trusted or _is_trusted_wasm_path(wasm_path):
            logger.warning(
                "Loading first-party trusted Wasm plugin with unverified OSV "
                "status because the last advisory fetch failed. Third-party "
                "plugins remain blocked without INTENTDIFF_ALLOW_VULNERABLE_WASMTIME=1."
            )
            return
        raise PluginLoadError(
            "<osv-check>",
            "The OSV advisory check failed on its last attempt (network "
            "unavailable?). Plugin loading is blocked to protect against "
            "undetected vulnerabilities. Run 'intentdiff security-check --refresh' "
            "to retry, or set INTENTDIFF_ALLOW_VULNERABLE_WASMTIME=1 to bypass "
            "(not recommended).",
        )

    # Cache is present — check for any wasmtime advisories in it.
    try:
        wasmtime_ver = _importlib_metadata.version("wasmtime")
    except _importlib_metadata.PackageNotFoundError:
        return

    for v in cache:
        if v.get("package") == "wasmtime" and v.get("version") == wasmtime_ver:
            override = os.environ.get("INTENTDIFF_ALLOW_VULNERABLE_WASMTIME", "").strip()
            if override in ("1", "true", "yes"):
                logger.warning(
                    "INTENTDIFF_ALLOW_VULNERABLE_WASMTIME: loading plugins despite "
                    "OSV advisory %s for wasmtime==%s.",
                    v["id"],
                    wasmtime_ver,
                )
                return
            aliases = ", ".join(v.get("aliases", []))
            raise PluginLoadError(
                "<osv-check>",
                f"OSV advisory {v['id']}"
                + (f" ({aliases})" if aliases else "")
                + f" affects wasmtime=={wasmtime_ver}: {v.get('summary', '')}. "
                "Upgrade wasmtime, run 'intentdiff security-check --refresh' for "
                "details, or set INTENTDIFF_ALLOW_VULNERABLE_WASMTIME=1 to bypass "
                "(not recommended).",
            )


# Module-level guard: fire the background thread at most once per process.
_osv_thread_lock = threading.Lock()
_osv_thread_started = False


def _maybe_start_osv_check() -> None:
    """Start the async OSV background thread if the daily budget allows it.

    - Stamp fresh + cache present → keep startup quiet, no network.
    - Stamp fresh + cache absent → last fetch failed; ``_check_osv_cache_or_block``
      already handled blocking; nothing more to do here.
    - Stamp stale / absent → spawn daemon thread to refresh in the background.

    The background thread is async (daemon) so it never blocks the caller.
    Deleting osv_cache.json does NOT bypass the rate limit; only deleting
    osv_last_fetch.stamp is the documented escape hatch.
    """
    global _osv_thread_started
    with _osv_thread_lock:
        if _osv_thread_started:
            return

        age = _time.time() - _read_stamp()
        if age < _OSV_FETCH_INTERVAL:
            # Stamp fresh: keep normal startup/tests quiet. The synchronous
            # gate above still blocks vulnerable wasmtime, while explicit
            # advisory visibility belongs to `intentdiff security-check`.
            cached = _load_osv_cache()
            if cached:
                logger.debug("OSV advisory cache has %d cached finding(s).", len(cached))
            _osv_thread_started = True
            return

        # Stamp stale or absent: schedule an async background refresh.
        t = threading.Thread(
            target=_osv_check_worker,
            daemon=True,
            name="intentdiff-osv-check",
        )
        t.start()
        _osv_thread_started = True


# ---------------------------------------------------------------------------
# Host-side implementations of WIT host-utils imports
# ---------------------------------------------------------------------------

_HOST_UTILS_MAX_JSON_BYTES = 8 * 1024 * 1024
_HOST_UTILS_MAX_JSON_DEPTH = 256
# CST JSON is verbose: a supported 10k-line file can exceed 250k container and
# scalar JSON nodes while still fitting under the byte cap.
_HOST_UTILS_MAX_JSON_NODES = 1_000_000
_HOST_UTILS_MAX_TRIVIA_TYPES = 1_024
_HOST_UTILS_MAX_TRIVIA_TYPE_BYTES = 256
_HOST_UTILS_MAX_TRIVIA_BYTES = 64 * 1024
_HOST_UTILS_MAX_LOG_BYTES = 16 * 1024
_MAX_PLUGIN_OUTPUT_BYTES = 16 * 1024 * 1024
# Hard linear-memory cap per plugin store (issue #87): the output cap above is
# enforced post-hoc, so a pathological input (binary decoded as text) could
# transiently balloon guest memory far past it BEFORE the host measured. The
# store limiter bails DURING parse instead - a trap, classified as a sandbox
# violation like any other, and the tainted store reloads. Default is generous
# (grammar tables + tree + output for legit large sources); deployments can
# tighten via the env var.
_PLUGIN_MEMORY_LIMIT_ENV = "INTENTDIFF_PLUGIN_MEMORY_LIMIT_BYTES"
_DEFAULT_PLUGIN_MEMORY_LIMIT_BYTES = 192 * 1024 * 1024


def _plugin_memory_limit_bytes() -> int:
    raw = os.environ.get(_PLUGIN_MEMORY_LIMIT_ENV, "")
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PLUGIN_MEMORY_LIMIT_BYTES
    return value if value > 0 else _DEFAULT_PLUGIN_MEMORY_LIMIT_BYTES


def _host_utils_error(detail: str) -> ValueError:
    return ValueError(f"host-utils input rejected: {detail}")


def _bounded_plugin_text(plugin_id: str, export_name: str, value: Any) -> str:
    text = str(value)
    size = len(text.encode("utf-8"))
    if size > _MAX_PLUGIN_OUTPUT_BYTES:
        raise PluginOutputError(
            plugin_id,
            f"{export_name} output is {size} bytes; limit is {_MAX_PLUGIN_OUTPUT_BYTES} bytes",
        )
    return text


def _check_json_text_limits(cst_json: str) -> None:
    size = len(cst_json.encode("utf-8"))
    if size > _HOST_UTILS_MAX_JSON_BYTES:
        raise _host_utils_error(
            f"JSON payload is {size} bytes; limit is "
            f"{_HOST_UTILS_MAX_JSON_BYTES} bytes"
        )

    depth = 0
    in_string = False
    escaped = False
    for ch in cst_json:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > _HOST_UTILS_MAX_JSON_DEPTH:
                raise _host_utils_error(
                    f"JSON nesting depth exceeds {_HOST_UTILS_MAX_JSON_DEPTH}"
                )
        elif ch in "]}":
            depth = max(0, depth - 1)


def _check_trivia_type_limits(trivia_types: list[str]) -> None:
    if len(trivia_types) > _HOST_UTILS_MAX_TRIVIA_TYPES:
        raise _host_utils_error(
            f"trivia type count is {len(trivia_types)}; limit is "
            f"{_HOST_UTILS_MAX_TRIVIA_TYPES}"
        )

    total = 0
    for trivia_type in trivia_types:
        size = len(str(trivia_type).encode("utf-8"))
        if size > _HOST_UTILS_MAX_TRIVIA_TYPE_BYTES:
            raise _host_utils_error(
                f"trivia type is {size} bytes; limit is "
                f"{_HOST_UTILS_MAX_TRIVIA_TYPE_BYTES} bytes"
            )
        total += size
        if total > _HOST_UTILS_MAX_TRIVIA_BYTES:
            raise _host_utils_error(
                f"trivia type payload is {total} bytes; limit is "
                f"{_HOST_UTILS_MAX_TRIVIA_BYTES} bytes"
            )


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    marker = b"...[truncated]"
    budget = max(0, max_bytes - len(marker))
    return encoded[:budget].decode("utf-8", errors="ignore") + marker.decode("ascii")


def _load_limited_cst_json(cst_json: str) -> Any:
    _check_json_text_limits(cst_json)
    try:
        data = json.loads(cst_json)
    except json.JSONDecodeError as exc:
        raise _host_utils_error(f"invalid JSON: {exc.msg}") from exc

    stack: list[tuple[Any, int]] = [(data, 1)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > _HOST_UTILS_MAX_JSON_NODES:
            raise _host_utils_error(
                f"JSON node count exceeds {_HOST_UTILS_MAX_JSON_NODES}"
            )
        if depth > _HOST_UTILS_MAX_JSON_DEPTH:
            raise _host_utils_error(
                f"JSON nesting depth exceeds {_HOST_UTILS_MAX_JSON_DEPTH}"
            )
        if isinstance(node, dict):
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)
    return data


def _strip_trivia_impl(cst_json: str, trivia_types: list[str]) -> str:
    """Remove trivia nodes from a bounded CST JSON document."""
    _check_trivia_type_limits(trivia_types)
    trivia_set = set(trivia_types)
    data = _load_limited_cst_json(cst_json)
    if not isinstance(data, dict):
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    results: dict[int, Any] = {}
    stack: list[tuple[Any, bool]] = [(data, False)]
    while stack:
        node, visited = stack.pop()
        if not isinstance(node, dict):
            results[id(node)] = node
            continue
        if not visited:
            stack.append((node, True))
            children = node.get("children")
            if isinstance(children, list):
                stack.extend((child, False) for child in reversed(children))
            continue
        if node.get("type") in trivia_set:
            results[id(node)] = None
            continue
        children = node.get("children")
        if isinstance(children, list):
            filtered = [
                results[id(child)]
                for child in children
                if results.get(id(child)) is not None
            ]
            results[id(node)] = dict(node, children=filtered)
        else:
            results[id(node)] = node

    result = results[id(data)]
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False)


def _structural_hash_impl(cst_json: str) -> str:
    """
    Compute a structural hash of a CST node (JSON object).

    Algorithm matches the engine: SHA-256, hex-encoded.
      leaf:     sha256(type + ":" + text)
      internal: sha256(type + "|" + "|".join(hash(child) for child in children))
    """

    data = _load_limited_cst_json(cst_json)
    hashes: dict[int, str] = {}
    stack: list[tuple[Any, bool]] = [(data, False)]
    while stack:
        node, visited = stack.pop()
        if not isinstance(node, dict):
            hashes[id(node)] = hashlib.sha256(str(node).encode()).hexdigest()
            continue
        children = node.get("children")
        has_children = isinstance(children, list) and bool(children)
        if has_children and not visited:
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(children))
            continue
        # Accept BOTH key spellings (#49 item 2, latent bug): CST nodes carry
        # type/text, but FullParse SemanticNode trees carry node_type/label — the
        # old type/text-only reads hashed FullParse trees as all-blank SHAPE.
        node_type = node.get("node_type") or node.get("type", "")
        if has_children:
            child_hashes = "|".join(hashes[id(c)] for c in children)
            payload = f"{node_type}|{child_hashes}"
        else:
            text = node.get("label", node.get("text", ""))
            payload = f"{node_type}:{text}"
        hashes[id(node)] = hashlib.sha256(payload.encode()).hexdigest()
    return hashes[id(data)]


def _log_impl(level: str, message: str) -> None:
    lvl = getattr(logging, level.upper(), logging.DEBUG)
    logger.log(lvl, "[plugin] %s", _truncate_utf8(str(message), _HOST_UTILS_MAX_LOG_BYTES))


def _record_field(record: Any, *names: str) -> Any:
    for name in names:
        if isinstance(record, dict) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
        attr = name.replace("-", "_")
        if hasattr(record, attr):
            return getattr(record, attr)
    return None


def _language_info_record_to_dict(record: Any) -> dict[str, Any]:
    """Convert a WIT ``language-info`` record into a Python dict."""
    return {
        "language_id": str(_record_field(record, "language-id", "language_id") or ""),
        "language_name": str(_record_field(record, "language-name", "language_name") or ""),
        "language_short_name": str(
            _record_field(record, "language-short-name", "language_short_name") or ""
        ),
        "monaco_language": str(_record_field(record, "monaco-language", "monaco_language") or ""),
        "default_filename": str(
            _record_field(record, "default-filename", "default_filename") or ""
        ),
        "language_file_extensions": list(
            _record_field(
                record,
                "language-file-extensions",
                "language_file_extensions",
            )
            or []
        ),
        "author": str(_record_field(record, "author") or ""),
        "plugin_version": str(_record_field(record, "plugin-version", "plugin_version") or ""),
        "last_updated": str(_record_field(record, "last-updated", "last_updated") or ""),
    }


# ---------------------------------------------------------------------------
# Loaded plugin wrapper
# ---------------------------------------------------------------------------


class LoadedPlugin:
    """
    Wraps a wasmtime-instantiated Wasm component.

    All public methods translate Wasm traps into typed Python exceptions.
    """

    # Qualified interface export names as emitted by wit-bindgen
    _PARSER_IFACE = "intentdiff:plugin/parser@1.0.0"
    _RENDERER_IFACE = "intentdiff:plugin/renderer@1.0.0"
    _ENRICHER_IFACE = "intentdiff:plugin/enricher@1.0.0"
    _DIFF_ANALYZER_IFACE = "intentdiff:plugin/diff-analyzer@1.0.0"

    def __init__(
        self,
        wasm_path: str,
        instance: Any,
        store: Store,
        fuel: int,
        iface_name: str,
        trusted: bool = False,
    ) -> None:
        self._wasm_path = wasm_path
        self._instance = instance
        self._store = store
        self._fuel = fuel
        self._iface_name = iface_name
        self._trusted = trusted
        # Cache the interface-level ExportIndex for nested function lookups
        self._iface_idx = instance.get_export_index(store, iface_name)
        # Wasm Store is not thread-safe; serialise all calls through this lock.
        self._lock = threading.Lock()
        # Set to True after any Wasm trap; triggers a fresh instantiation on
        # the next call so one bad file doesn't poison subsequent ones.
        self._tainted = False
        self._telemetry: list[dict[str, Any]] = []

    @property
    def wasm_path(self) -> str:
        """Path of the ``.wasm`` file that backs this plugin instance."""
        return self._wasm_path

    @property
    def trusted(self) -> bool:
        """True when the plugin was loaded through a trusted first-party path."""
        return self._trusted

    def drain_telemetry(self) -> list[dict[str, Any]]:
        """Return and clear bounded Wasm-call telemetry for this plugin."""
        with self._lock:
            records = list(self._telemetry)
            self._telemetry.clear()
            return records

    # ── Parser calls ────────────────────────────────────────────────────────

    def call_parser_mode(self) -> str:
        raw = self._call("get-parser-mode")
        # Component Model enums come back as integers; map to string
        _mode_map = {0: "interpret-cst", 1: "full-parse"}
        if isinstance(raw, int):
            return _mode_map.get(raw, "interpret-cst")
        return str(raw)

    def call_grammar_id(self) -> str:
        return _bounded_plugin_text(self._wasm_path, "grammar-id", self._call("grammar-id"))

    def call_detect_language(self, filename: str, content: str) -> str:
        return _bounded_plugin_text(
            self._wasm_path,
            "detect-language",
            self._call("detect-language", filename, content),
        )

    def call_process(
        self,
        input_: str,
        language: str,
        filename: str,
        fuel: int | None = None,
    ) -> str:
        return _bounded_plugin_text(
            self._wasm_path,
            "process",
            self._call("process", input_, language, filename, fuel_override=fuel),
        )

    def call_trivia_node_types(self) -> list[str]:
        result = self._call("trivia-node-types")
        return list(result) if result is not None else []

    def call_language_ids(self) -> list[str]:
        result = self._call("language-ids")
        return list(result) if result is not None else []

    def call_language_info(self) -> list[dict[str, Any]]:
        """Return plugin-owned language metadata when the export exists."""
        if not self._has_export("language-info"):
            return []
        result = self._call("language-info")
        if result is None:
            return []
        return [_language_info_record_to_dict(item) for item in list(result)]

    def call_priority(self, iface: str = "parser") -> int:
        return int(self._call("priority"))

    def call_preprocess_source(self, source: str) -> str:
        return _bounded_plugin_text(
            self._wasm_path,
            "preprocess-source",
            self._call("preprocess-source", source),
        )

    def call_example(self, language: str) -> dict[str, str]:
        result = self._call("example", language)
        if result is None:
            return {"old": "", "new": ""}
        # wasmtime returns WIT records as objects with field attributes
        if hasattr(result, "old"):
            return {"old": str(result.old), "new": str(result.new)}
        if isinstance(result, (tuple, list)) and len(result) >= 2:
            return {"old": str(result[0]), "new": str(result[1])}
        if isinstance(result, dict):
            return {"old": str(result.get("old", "")), "new": str(result.get("new", ""))}
        return {"old": "", "new": ""}

    # ── Enricher calls ──────────────────────────────────────────────────────

    def call_enrich(
        self,
        tree_json: str,
        raw_source: str,
        language: str,
        filename: str,
        fuel: int | None = None,
    ) -> str:
        return _bounded_plugin_text(
            self._wasm_path,
            "enrich",
            self._call(
                "enrich",
                tree_json,
                raw_source,
                language,
                filename,
                fuel_override=fuel,
            ),
        )

    # ── Diff-analyzer calls ─────────────────────────────────────────────────

    def call_analyze_diff(
        self, diff_json: str, language: str, filename: str, fuel: int | None = None
    ) -> str:
        return _bounded_plugin_text(
            self._wasm_path,
            "analyze-diff",
            self._call("analyze-diff", diff_json, language, filename, fuel_override=fuel),
        )

    # ── Renderer calls ──────────────────────────────────────────────────────

    def call_format_name(self) -> str:
        return _bounded_plugin_text(self._wasm_path, "format-name", self._call("format-name"))

    def call_render(self, diff_json: str, fuel: int | None = None) -> str:
        return _bounded_plugin_text(
            self._wasm_path,
            "render",
            self._call("render", diff_json, fuel_override=fuel),
        )

    def call_supported_options(self) -> list[str]:
        result = self._call("supported-options")
        return list(result) if result is not None else []

    # ── Internal ────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        """Recreate the Wasm store and instance after a trap (called with lock held)."""
        logger.debug("Reloading Wasm plugin %r after trap", self._wasm_path)
        fresh = load_plugin(self._wasm_path, self._fuel, trusted=self._trusted)
        self._instance = fresh._instance
        self._store = fresh._store
        self._iface_idx = fresh._instance.get_export_index(fresh._store, self._iface_name)
        self._tainted = False

    def _call(self, func_name: str, *args: Any, fuel_override: int | None = None) -> Any:
        """Call a component export by name (kebab-case), within the plugin interface."""
        with self._lock:
            return self._call_locked(func_name, *args, fuel_override=fuel_override)

    def _has_export(self, func_name: str) -> bool:
        """Return whether the current plugin interface exposes *func_name*."""
        with self._lock:
            if self._tainted:
                self._reload()
            return (
                self._instance.get_export_index(
                    self._store, func_name, self._iface_idx
                )
                is not None
            )

    def _call_locked(self, func_name: str, *args: Any, fuel_override: int | None = None) -> Any:
        """Internal: called with self._lock held."""
        if self._tainted:
            self._reload()

        fuel = fuel_override if fuel_override is not None else self._fuel
        unlimited = fuel == -1
        started = _time.perf_counter()
        status = "ok"
        remaining: int | None = None
        consumed: int | None = None
        if not unlimited:
            try:
                self._store.set_fuel(fuel)
            except WasmtimeError:
                status = "fuel_set_failed"
                logger.warning(
                    "set_fuel failed for plugin %r — fuel cap may not be active",
                    self._wasm_path,
                )

        try:
            # Two-level lookup: interface instance → function
            fn_idx = self._instance.get_export_index(
                self._store, func_name, self._iface_idx
            )
            if fn_idx is None:
                raise PluginSandboxViolation(
                    self._wasm_path,
                    f"export '{func_name}' not found in '{self._iface_name}'",
                )
            fn = self._instance.get_func(self._store, fn_idx)
            if fn is None:
                raise PluginSandboxViolation(
                    self._wasm_path, f"export '{func_name}' is not a function"
                )
            result = fn(self._store, *args)
            if not unlimited:
                remaining = self._store.get_fuel()
                consumed = fuel - remaining
                logger.debug(
                    "Plugin %r: %r consumed %d instructions (budget %d, %.1f%% used)",
                    self._wasm_path,
                    func_name,
                    consumed,
                    fuel,
                    consumed / fuel * 100,
                )
            return result
        except PluginFuelExhausted:
            status = "fuel_exhausted"
            self._tainted = True
            raise
        except PluginSandboxViolation:
            status = "sandbox_violation"
            self._tainted = True
            raise
        except WasmtimeError as exc:
            self._tainted = True
            msg = str(exc)
            if "fuel" in msg.lower() or "out of fuel" in msg.lower():
                status = "fuel_exhausted"
                raise PluginFuelExhausted(self._wasm_path, fuel) from exc
            if "memory" in msg.lower() and ("limit" in msg.lower() or "grow" in msg.lower()):
                # The issue-#87 store limiter fired mid-parse (early output
                # bounding) - still a sandbox violation, but named for triage.
                status = "memory_limit"
                raise PluginSandboxViolation(
                    self._wasm_path,
                    f"linear memory limit hit during {func_name}: {msg}",
                ) from exc
            status = "wasmtime_error"
            # Treat all other wasmtime errors (traps, bad-parameter, etc.) as
            # sandbox violations so they are classified consistently.
            raise PluginSandboxViolation(self._wasm_path, msg) from exc
        except Exception as exc:
            status = "exception"
            self._tainted = True
            raise PluginSandboxViolation(self._wasm_path, str(exc)) from exc
        finally:
            elapsed_ms = (_time.perf_counter() - started) * 1000.0
            if not unlimited and remaining is None:
                try:
                    remaining = self._store.get_fuel()
                    consumed = fuel - remaining
                except Exception:
                    remaining = None
                    consumed = None
            self._record_telemetry(
                func_name=func_name,
                fuel_budget=fuel,
                fuel_unlimited=unlimited,
                fuel_consumed=consumed,
                fuel_remaining=remaining,
                elapsed_ms=elapsed_ms,
                status=status,
                args=args,
            )

    def _record_telemetry(
        self,
        *,
        func_name: str,
        fuel_budget: int,
        fuel_unlimited: bool,
        fuel_consumed: int | None,
        fuel_remaining: int | None,
        elapsed_ms: float,
        status: str,
        args: tuple[Any, ...] = (),
    ) -> None:
        input_bytes: int | None = None
        input_lines: int | None = None
        language: str | None = None
        filename: str | None = None
        if func_name == "process" and len(args) >= 3:
            source = args[0]
            if isinstance(source, str):
                input_bytes = len(source.encode("utf-8"))
                input_lines = len(source.splitlines()) or (1 if source else 0)
            if isinstance(args[1], str):
                language = args[1]
            if isinstance(args[2], str):
                filename = args[2]
        record = {
            "plugin": str(self._wasm_path),
            "interface": self._iface_name,
            "function": func_name,
            "engine_owner": "python",
            "engine": "python_wasmtime_plugin_host",
            "provenance": "first_party_wasm" if self._trusted else "third_party_wasm",
            "trusted": self._trusted,
            "status": status,
            "fuel_budget": None if fuel_unlimited else fuel_budget,
            "fuel_consumed": fuel_consumed,
            "fuel_remaining": fuel_remaining,
            "fuel_used_percent": (
                None
                if fuel_unlimited or fuel_budget <= 0 or fuel_consumed is None
                else round(fuel_consumed / fuel_budget * 100.0, 3)
            ),
            "elapsed_ms": round(elapsed_ms, 3),
        }
        if language is not None:
            record["language"] = language
        if filename is not None:
            record["filename"] = filename
        if input_bytes is not None:
            record["input_bytes"] = input_bytes
        if input_lines is not None:
            record["input_lines"] = input_lines
        self._telemetry.append(record)
        if len(self._telemetry) > _MAX_TELEMETRY_RECORDS:
            del self._telemetry[: len(self._telemetry) - _MAX_TELEMETRY_RECORDS]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_plugin(
    wasm_path: str | Path,
    fuel: int = 10_000_000,
    *,
    trusted: bool = False,
) -> LoadedPlugin:
    """
    Instantiate a Wasm component plugin with an empty WASI sandbox.

    Parameters
    ----------
    wasm_path:
        Filesystem path to the compiled ``.wasm`` file.
    fuel:
        Maximum Wasm instructions before the engine traps.  Pass ``-1``
        (``FUEL_UNLIMITED``) to disable the cap entirely — useful when
        indexing very large files.  A warning is emitted at load time.
    trusted:
        Whether this plugin is a first-party trusted package asset. This can
        still affect unavailable-OSV-cache handling, but it does not bypass a
        known-vulnerable Wasmtime version.
    """
    _unlimited = fuel == -1
    path = Path(wasm_path)
    trusted = trusted or _is_trusted_wasm_path(path)
    _verify_builtin_provenance(path)                     # #89 first-party provenance (optional)
    _check_wasmtime_version(path, trusted=trusted)       # fast sync blocklist
    _check_osv_cache_or_block(path, trusted=trusted)     # fast sync OSV gate
    _maybe_start_osv_check()                             # async refresh

    if not path.exists():
        raise PluginLoadError(str(path), "file not found")

    try:
        cfg = Config()
        cfg.consume_fuel = not _unlimited  # disabled → no cap, no tracking
        # CVE-2026-44216 mitigation: disable memory64 if the Python binding
        # exposes the setter (added in wasmtime-py post-44.0.0 builds).
        if hasattr(cfg, "wasm_memory64"):
            cfg.wasm_memory64 = False  # type: ignore[attr-defined]
        if _unlimited:
            logger.warning(
                "Unlimited Wasm fuel for plugin %r — calls are uncapped and "
                "may run indefinitely.",
                str(wasm_path),
            )
        engine = Engine(cfg)

        component = Component.from_file(engine, str(path))

        linker = Linker(engine)
        linker.add_wasip2()

        # Register host-utils imports using add_instance context manager.
        # root must be closed before instantiate() is called.
        root = linker.root()
        with root.add_instance("intentdiff:plugin/host-utils") as host:
            host.add_func(
                "strip-trivia",
                lambda store, args: _strip_trivia_impl(args[0], list(args[1])),
            )
            host.add_func(
                "structural-hash",
                lambda store, args: _structural_hash_impl(args[0]),
            )
            host.add_func(
                "log",
                lambda store, args: _log_impl(args[0], args[1]),
            )
        root.close()

        wasi_cfg = WasiConfig()
        # Zero capabilities: no dirs, no env, no args, no stdio

        store: Store = Store(engine)
        store.set_wasi(wasi_cfg)
        # Early output/memory bounding (issue #87): bail DURING parse, not
        # after the balloon.
        store.set_limits(memory_size=_plugin_memory_limit_bytes())
        if not _unlimited:
            store.set_fuel(fuel)

        instance = linker.instantiate(store, component)

        # Detect whether this is a parser, renderer, or enricher plugin
        if instance.get_export_index(store, LoadedPlugin._PARSER_IFACE) is not None:
            iface_name = LoadedPlugin._PARSER_IFACE
        elif instance.get_export_index(store, LoadedPlugin._RENDERER_IFACE) is not None:
            iface_name = LoadedPlugin._RENDERER_IFACE
        elif instance.get_export_index(store, LoadedPlugin._ENRICHER_IFACE) is not None:
            iface_name = LoadedPlugin._ENRICHER_IFACE
        elif instance.get_export_index(store, LoadedPlugin._DIFF_ANALYZER_IFACE) is not None:
            iface_name = LoadedPlugin._DIFF_ANALYZER_IFACE
        else:
            raise PluginLoadError(
                str(path),
                "component exports neither parser, renderer, enricher, nor diff-analyzer interface",
            )

        return LoadedPlugin(
            str(path),
            instance,
            store,
            fuel,
            iface_name,
            trusted=trusted,
        )

    except (PluginLoadError, PluginSandboxViolation):
        raise
    except WasmtimeError as exc:
        raise PluginLoadError(str(path), str(exc)) from exc
    except Exception as exc:
        raise PluginLoadError(str(path), str(exc)) from exc
