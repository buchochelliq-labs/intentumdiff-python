"""
intentumdiff.plugins.hub
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plugin hub: resolve, install, verify, and remove IntentumDiff plugins.

Sources
-------
Plugins can be installed from two sources:

  ``source: git``  (default)
      ``pip install git+<url>@<ref>``
      ``ref`` is a git tag, branch, or commit SHA.
      ``repo`` overrides the default official-org URL.

  ``source: pypi``
      ``pip install <package>[==<ref>]``
      ``ref`` is a PEP 440 version string (e.g. ``"1.2.3"``).
      ``repo`` is unused.

For official plugins (no ``repo``), the short name is expanded to the
full package in the ``buchochelliq-labs`` GitHub org:
  ``dbt``  ->  ``intentumdiff-dbt``
  ->  ``https://github.com/buchochelliq-labs/intentumdiff-dbt``

Security model
--------------
After installation, two checks are run (unless ``--skip-verify`` is passed):

1. **Wasm checksum verification** — SHA-256 of every ``.wasm`` file in the
   installed package is compared against the expected hashes stored in
   ``intentumdiff_plugins.yaml`` (populated from the official registry or supplied
   manually).  A mismatch means the installed binary is different from the
   one the author published.

2. **Wasm capability scan** — the binary's import section is scanned for
   unexpected WASI imports (filesystem, sockets, network, HTTP, …).  The only
   permitted host import is the IntentumDiff WIT package
   ``intentdiff:plugin/host-utils@1.0.0``.
   Any other import is flagged as a warning.

When installing from the **official registry**, the registry manifest already
contains the expected checksums for each release, so verification is
automatic.  When installing from a custom source, checksums are computed and
stored in ``intentumdiff_plugins.yaml`` on first install (trust-on-first-use), and
subsequent ``intentumdiff plugins install`` will verify them.

Plugin file format (intentumdiff_plugins.yaml)
----------------------------------------
::

    # intentumdiff_plugins.yaml
    version: 1
    plugins:
      - name: dbt
        source: pypi          # "pypi" or "git" (default: git)
        ref: "0.3.1"          # git: tag/branch/commit; pypi: version (e.g. "1.2.3")
        wasm_checksums:       # populated automatically; verify on every install
          dbt_sql_parser.wasm: <sha256_hex>
          dbt_schema_parser.wasm: <sha256_hex>
          dbt_enricher.wasm: <sha256_hex>

      - name: terraform
        source: git
        ref: v0.2.0
        wasm_checksums:
          terraform_parser.wasm: <sha256_hex>

      - name: custom-plugin
        source: git
        ref: abc1234
        repo: https://github.com/myorg/intentumdiff-custom
        # wasm_checksums intentionally omitted — will warn about unverified install
"""

from __future__ import annotations

import hashlib
import importlib.metadata as _meta
import logging
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Official plugin organisation
# ---------------------------------------------------------------------------

#: GitHub organisation that hosts official IntentumDiff plugins.
OFFICIAL_ORG = "buchochelliq-labs"
OFFICIAL_BASE_URL = f"https://github.com/{OFFICIAL_ORG}"

#: Raw-content URL for the official registry manifest (the intentumdiff-registry repo — the
#: #95 root of trust, populated at the #82 split; formerly the unpopulated intentumdiff-plugins).
OFFICIAL_REGISTRY_RAW_URL = (
    f"https://raw.githubusercontent.com/{OFFICIAL_ORG}/intentumdiff-registry"
)

# The safe-Git-ref pattern (registry URL path segments), the strict commit-SHA
# pattern, and the dep_hashes key/value patterns now live in the shared Rust
# core (registry.rs) — see _validate_registry_ref / _validate_dep_hashes, which
# delegate so every binding enforces the identical #88 controls.
_PLUGIN_NAME_RE = re.compile(r"^(?:intentumdiff-)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLUGIN_SOURCE_VALUES = frozenset({"git", "pypi"})


def _normalise_package_name(name: str) -> str:
    """Return a comparison key for Python package names."""
    return re.sub(r"[-_.]+", "_", name).lower()


def _package_from_dep_spec(dep_spec: str) -> str:
    """Return the normalised package name from a ``package==version`` key."""
    return _normalise_package_name(dep_spec.split("==", 1)[0])


def _package_from_wheel(wheel_path: "Path") -> str:
    """Return the normalised distribution name encoded in a wheel filename."""
    return _normalise_package_name(wheel_path.name.split("-", 1)[0])


def _main_package_names(spec: "PluginSpec", install_target: str | None) -> set[str]:
    names = {_normalise_package_name(spec.package_name)}
    if install_target:
        names.add(_package_from_dep_spec(install_target))
    return names


def _validate_registry_ref(ref: str, *, strict: bool = False) -> None:
    """Raise ValueError if *ref* is not a safe Git ref for URL interpolation.

    When *strict* is ``True``, the ref must be a full 40-character lowercase
    hex commit SHA.  This is the production/CI mode: it rejects mutable refs
    like branch names and tags, making registry fetches reproducible and
    tamper-evident.

    The validation lives in the shared Rust core (``registry.rs``) so every
    binding enforces the identical #88 guard; this is a thin delegator.
    """
    from intentumdiff.rust_core import validate_registry_ref as _rust_validate_ref

    _rust_validate_ref(ref, strict)

def _validate_dep_hashes(spec: "PluginSpec", install_target: str | None = None) -> list[str]:
    """Validate *spec.dep_hashes* keys and coverage.

    Returns a list of human-readable error strings.  An empty list means the
    dep_hashes dict is well-formed and the main package is listed.

    Rules:
    - Every key must match ``package==version`` (exact PEP 440 pin).
    - Every hash must be a SHA-256 hash in pip's ``sha256:<hex>`` form.
    - The main plugin package must appear as one of the keys.
    - Extra packages must be explicitly listed in ``allowed_dependencies``.

    The key/value/coverage checks live in the shared Rust core
    (``registry.rs``) so every binding enforces the identical #88 controls;
    this is a thin delegator. ``dep_hashes`` is passed as ordered
    ``(key, value)`` pairs so error ordering matches the dict iteration.
    """
    from intentumdiff.rust_core import validate_dep_hashes as _rust_validate_dep_hashes

    return _rust_validate_dep_hashes(
        list(spec.dep_hashes.items()),
        list(spec.allowed_dependencies),
        spec.package_name,
        install_target,
    )


#: Default filename for the project-level plugins manifest.

# Wasm imports that are legitimately expected in every plugin binary.
# Any module name NOT in this set (and not an IntentumDiff namespace)
# will be flagged by the capability scanner.
_ALLOWED_WASM_HOST_MODULES: frozenset[str] = frozenset(
    {
        "intentdiff:plugin/host-utils@1.0.0",
    }
)

# WASI capability prefixes that are always suspicious for a sandboxed plugin.
_SUSPICIOUS_WASI_PREFIXES: tuple[bytes, ...] = (
    b"wasi:filesystem",
    b"wasi:sockets",
    b"wasi:network",
    b"wasi:http",
    b"wasi:keyvalue",
    b"wasi:blobstore",
    b"wasi:messaging",
    # wasi:io/streams is used internally by the Component Model runtime for
    # async; we allow it via wasmtime's empty WasiConfig.  Flag anything else.
)


# ---------------------------------------------------------------------------
# PluginSpec dataclass
# ---------------------------------------------------------------------------


@dataclass
class PluginSpec:
    """A single plugin dependency declaration."""

    #: Short name (e.g. ``"dbt"``) or full package name.
    name: str

    #: Source type: ``"git"`` (default) or ``"pypi"``.
    source: str = "git"

    #: For git: tag, branch, or commit SHA (default ``"main"``).
    #: For pypi: version string (e.g. ``"1.2.3"``); empty = latest.
    ref: str = "main"

    #: Optional custom git repo URL (git source only).
    #: When empty the official org URL is derived from ``package_name``.
    repo: str = ""

    #: Expected SHA-256 hex digests of the installed ``.wasm`` files.
    #: Key: filename (e.g. ``"dbt_sql_parser.wasm"``); value: lowercase hex.
    #: Populated automatically by ``intentumdiff plugins add``.
    #: An empty dict means checksums have not been recorded (install will warn).
    wasm_checksums: dict[str, str] = field(default_factory=dict)

    #: Approved dependency hashes for plugins that cannot be fully self-contained.
    #: Key: ``"package==version"``; value: ``"sha256:hexdigest"``.
    #: An empty dict (default) means the plugin wheel must be self-contained and
    #: will be installed with ``--no-deps``.  When non-empty every entry is passed
    #: to pip as ``--require-hashes``, acting as a minimal inline lock-file.
    #: The main package wheel **must** be included in this dict as well.
    dep_hashes: dict[str, str] = field(default_factory=dict)

    #: Reviewed package names, without versions, that may appear in ``dep_hashes``
    #: in addition to the main plugin package.
    allowed_dependencies: list[str] = field(default_factory=list)

    # ── derived ──────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if not _PLUGIN_NAME_RE.fullmatch(self.name):
            raise ValueError(
                f"Invalid plugin name {self.name!r}: expected a package-safe "
                "IntentumDiff plugin name, not a URL, path, or requirement expression."
            )
        if self.source not in _PLUGIN_SOURCE_VALUES:
            raise ValueError(
                f"Invalid plugin source {self.source!r}: expected one of "
                f"{sorted(_PLUGIN_SOURCE_VALUES)!r}."
            )

    @property
    def package_name(self) -> str:
        """Canonical Python package name with the primary ``intentumdiff-`` prefix."""
        if self.name.startswith("intentumdiff-"):
            return self.name
        return f"intentumdiff-{self.name}"

    @property
    def short_name(self) -> str:
        """Short name without the primary package prefix."""
        if self.name.startswith("intentumdiff-"):
            return self.name[len("intentumdiff-"):]
        return self.name

    @property
    def resolved_git_url(self) -> str:
        """Git repository URL (git source only)."""
        return self.repo or f"{OFFICIAL_BASE_URL}/{self.package_name}"

    @property
    def install_target(self) -> str:
        """The pip install argument for this spec."""
        if self.source == "pypi":
            if self.ref:
                return f"{self.package_name}=={self.ref}"
            return self.package_name
        # git (default)
        url = self.resolved_git_url
        if self.ref:
            return f"git+{url}@{self.ref}"
        return f"git+{url}"

    @property
    def pip_install_url(self) -> str:
        """Deprecated alias for :attr:`install_target`."""
        return self.install_target

    @property
    def is_official(self) -> bool:
        """True when the plugin comes from the official org (no custom repo)."""
        return not self.repo


# ---------------------------------------------------------------------------
# Official registry manifest
# ---------------------------------------------------------------------------


@dataclass
class RegistryEntry:
    """Metadata for a single plugin as recorded in the official registry."""

    name: str
    source: str = "git"
    ref: str = "main"
    repo: str = ""
    description: str = ""
    #: SHA-256 hex digests keyed by ``.wasm`` filename.
    wasm_checksums: dict[str, str] = field(default_factory=dict)
    dep_hashes: dict[str, str] = field(default_factory=dict)
    allowed_dependencies: list[str] = field(default_factory=list)
    #: Plugin-contract ABI version the plugin implements (#94). Empty = unspecified
    #: (older registry entries) — treated as compatible so it is not a breaking change.
    abi_target: str = ""
    #: Declared trust tier (#95): ``"official"`` (org-built/scanned) or ``"community"``
    #: (listed but unverified). Empty defaults to ``"official"``. The EFFECTIVE tier
    #: (:meth:`effective_trust_tier`) additionally demotes to community when the entry lacks
    #: the verification an official plugin must carry.
    trust_tier: str = "official"

    def effective_trust_tier(self) -> str:
        """The trust tier actually earned, mapping onto the existing verification signals (#95).

        An entry is ``"official"`` only when it declares so (or leaves it default), comes from
        the official org (no custom ``repo``), AND carries ``wasm_checksums`` for verification.
        An explicit ``"community"`` tier, a custom repo, or missing checksums all resolve to
        ``"community"`` — i.e. the same "listed but unverified → warn" condition hub.py already
        surfaces, now named.
        """
        declared = self.trust_tier or "official"
        if declared == "community" or self.repo or not self.wasm_checksums:
            return "community"
        return "official"

    def trust_warning(self) -> str | None:
        """A non-blocking warning string when this entry is (effectively) community-tier, else
        ``None``. Community plugins install, but the user is told they are unverified (#95)."""
        if self.effective_trust_tier() == "community":
            return (
                f"plugin {self.name!r} is community-tier (listed but unverified — no official "
                "checksums / custom source). Install only if you trust the source."
            )
        return None

    def abi_incompatibility(self, host_version: str | None = None) -> str | None:
        """A human-readable reason string when this entry's ``abi_target`` is incompatible
        with the host contract, else ``None``. Consumed at RESOLVE time (before install) so an
        incompatible plugin is refused early, not at instantiation (#94). An empty
        ``abi_target`` is treated as compatible (unspecified, not incompatible)."""
        if not self.abi_target:
            return None
        from intentumdiff.plugins.registry_schema import HOST_CONTRACT_VERSION, abi_compatible

        host = host_version or HOST_CONTRACT_VERSION
        if not abi_compatible(self.abi_target, host):
            return (
                f"plugin {self.name!r} targets plugin-contract ABI {self.abi_target}, "
                f"which is incompatible with this IntentumDiff host's contract {host}. "
                "Upgrade IntentumDiff or install a plugin build that targets the host's ABI."
            )
        return None

    def to_spec(self) -> PluginSpec:
        """Convert to a :class:`PluginSpec` ready for installation."""
        return PluginSpec(
            name=self.name,
            source=self.source,
            ref=self.ref,
            repo=self.repo,
            wasm_checksums=dict(self.wasm_checksums),
            dep_hashes=dict(self.dep_hashes),
            allowed_dependencies=list(self.allowed_dependencies),
        )


def fetch_official_registry(
    ref: str = "main",
    *,
    strict: bool = False,
    timeout: int = 15,
) -> dict[str, RegistryEntry]:
    """
    Fetch and parse the official plugin registry manifest.

    Parameters
    ----------
    ref:
        Git ref (branch, tag, or commit SHA) of the registry repo to fetch
        from.  Default ``"main"``.  **Pin to a commit SHA in production for
        reproducible, tamper-evident installs.**
    strict:
        When ``True``, *ref* must be a full 40-character commit SHA.  This
        enforces the production/CI policy: mutable refs (branch names, tags)
        are rejected.  Pass ``strict=True`` via the ``--strict-registry`` CLI
        flag.
    timeout:
        HTTP timeout in seconds.

    Returns
    -------
    dict[str, RegistryEntry]
        Mapping of short plugin name → registry entry.

    Raises
    ------
    RuntimeError
        If the registry cannot be fetched or parsed.
    """
    _validate_registry_ref(ref, strict=strict)
    url = f"{OFFICIAL_REGISTRY_RAW_URL}/{ref}/registry.yaml"
    logger.info("Fetching official registry at ref=%s (strict=%s)", ref, strict)
    ctx = ssl.create_default_context()  # always verify TLS certificates
    if not url.startswith("https://"):  # defense-in-depth: urllib also accepts file:// (#76 semgrep)
        raise RuntimeError(f"registry URL must be https, got: {url.split(':', 1)[0]}")
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- scheme asserted https above; base is a fixed constant + validated ref. noqa: S310  # nosec B310 - https scheme asserted above
            content = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not fetch the official plugin registry from {url}.\n"
            f"Check your internet connection.  Detail: {exc}"
        ) from exc

    return parse_registry_manifest(content)


def parse_registry_manifest(content: str) -> dict[str, RegistryEntry]:
    """Parse a registry manifest (YAML text) into ``{short name -> RegistryEntry}``.

    Split out from :func:`fetch_official_registry` so the parse (including the #94
    ``abi_target``) is testable without the network fetch.
    """
    yaml = _require_yaml()
    data: dict[str, Any] = yaml.safe_load(content) or {}

    entries: dict[str, RegistryEntry] = {}
    for plugin_name, entry_data in data.get("plugins", {}).items():
        if not isinstance(entry_data, dict):
            continue
        entries[plugin_name] = RegistryEntry(
            name=plugin_name,
            source=entry_data.get("source", "git"),
            ref=entry_data.get("ref", "main"),
            repo=entry_data.get("repo", ""),
            description=entry_data.get("description", ""),
            wasm_checksums=dict(entry_data.get("wasm_checksums", {})),
            dep_hashes=dict(entry_data.get("dep_hashes", {})),
            allowed_dependencies=list(entry_data.get("allowed_dependencies") or []),
            abi_target=str(entry_data.get("abi_target", "")),
            trust_tier=str(entry_data.get("trust_tier", "") or "official"),
        )
    return entries


# ---------------------------------------------------------------------------
# YAML file I/O
# ---------------------------------------------------------------------------


def _require_yaml() -> Any:
    """Import pyyaml, raising a friendly error if it is not installed."""
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml
    except ImportError as exc:
        raise ImportError(
            "pyyaml is required to read/write intentumdiff_plugins.yaml. "
            "Install it with: pip install pyyaml"
        ) from exc


def load_plugins_file(path: Path) -> list[PluginSpec]:
    """Parse *path* (``intentumdiff_plugins.yaml``) and return a list of :class:`PluginSpec`."""
    yaml = _require_yaml()
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: list[PluginSpec] = []
    for entry in data.get("plugins", []):
        if isinstance(entry, str):
            specs.append(PluginSpec(name=entry))
        elif isinstance(entry, dict):
            specs.append(
                PluginSpec(
                    name=entry.get("name", ""),
                    source=entry.get("source", "git"),
                    ref=entry.get("ref", "main"),
                    repo=entry.get("repo", ""),
                    wasm_checksums=dict(entry.get("wasm_checksums") or {}),
                    dep_hashes=dict(entry.get("dep_hashes") or {}),
                    allowed_dependencies=list(entry.get("allowed_dependencies") or []),
                )
            )
    return specs


def save_plugins_file(path: Path, specs: list[PluginSpec]) -> None:
    """Write *specs* to *path* as a ``intentumdiff_plugins.yaml`` file."""
    yaml = _require_yaml()
    entries: list[dict[str, Any]] = []
    for s in specs:
        entry: dict[str, Any] = {
            "name": s.name,
            "source": s.source,
            "ref": s.ref,
        }
        if s.repo:
            entry["repo"] = s.repo
        if s.wasm_checksums:
            entry["wasm_checksums"] = dict(s.wasm_checksums)
        if s.dep_hashes:
            entry["dep_hashes"] = dict(s.dep_hashes)
        if s.allowed_dependencies:
            entry["allowed_dependencies"] = list(s.allowed_dependencies)
        entries.append(entry)

    header = (
        "# intentumdiff_plugins.yaml - plugin dependencies for IntentumDiff\n"
        "# Install all plugins:            intentumdiff plugins install\n"
        "# Add/update a plugin:            intentumdiff plugins add <name>\n"
        "# Remove a plugin:                intentumdiff plugins remove <name>\n"
        "# Checksums are verified on every install. Do not edit them manually.\n\n"
    )
    body = yaml.dump(
        {"version": 1, "plugins": entries},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    path.write_text(header + body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Wasm security checks
# ---------------------------------------------------------------------------


def compute_wasm_checksums(package_name: str) -> dict[str, str]:
    """
    Find all ``.wasm`` files installed by *package_name* and return their
    SHA-256 hex digests keyed by filename.

    Returns an empty dict if the package is not installed or has no ``.wasm``
    files.
    """
    try:
        dist = _meta.distribution(package_name)
    except _meta.PackageNotFoundError:
        return {}

    checksums: dict[str, str] = {}
    for record in dist.files or []:
        if not str(record).endswith(".wasm"):
            continue
        full_path = Path(str(dist.locate_file(record)))
        if full_path.exists():
            digest = hashlib.sha256(full_path.read_bytes()).hexdigest()
            checksums[full_path.name] = digest
    return checksums


def verify_wasm_checksums(spec: PluginSpec) -> list[str]:
    """
    Verify the SHA-256 of every installed ``.wasm`` file against the expected
    values stored in *spec.wasm_checksums*.

    Returns a list of human-readable error strings.  An empty list means all
    checks passed.  A non-empty list must be treated as a security failure —
    **do not load the plugin**.

    This function is **post-install defence-in-depth**.  It detects tampering or
    corruption after the package is already on disk.  The primary security
    controls are the pre-install wheel linter and ``--no-deps`` / ``--require-hashes``
    install enforcement in :func:`pip_install`.
    """
    if not spec.wasm_checksums:
        # No stored checksums — caller should warn but not block.
        return []

    actual = compute_wasm_checksums(spec.package_name)
    if not actual:
        return [
            f"Package '{spec.package_name}' is not installed or contains no .wasm files."
        ]

    errors: list[str] = []
    for filename, expected_hex in spec.wasm_checksums.items():
        if filename not in actual:
            errors.append(
                f"Expected .wasm file '{filename}' not found in the installed package."
            )
            continue
        got_hex = actual[filename]
        if got_hex != expected_hex.lower():
            errors.append(
                f"Checksum mismatch for '{filename}':\n"
                f"  expected: {expected_hex}\n"
                f"  actual:   {got_hex}\n"
                "The installed binary does not match the recorded checksum — "
                "possible tampering or corrupted download."
            )

    # Also flag any wasm files that exist but are NOT in the recorded set.
    for filename in actual:
        if filename not in spec.wasm_checksums:
            errors.append(
                f"Unexpected .wasm file '{filename}' found in '{spec.package_name}' "
                "that is not in the recorded checksum set."
            )

    return errors


def check_wasm_capabilities(wasm_path: Path) -> list[str]:
    """
    Scan a ``.wasm`` binary for unexpected host capability imports.

    This is a fast byte-scan heuristic (not a full binary parse) that detects
    WASI import strings embedded in the module's import section.  Any WASI
    capability other than the expected WIT host-utils interface is flagged.

    Returns a list of human-readable warning strings (empty = clean).

    Note: this check catches accidental or malicious inclusion of WASI imports
    at build time.  Runtime enforcement is still provided by the wasmtime
    ``WasiConfig()`` empty sandbox — but pre-flight scanning lets us reject
    suspicious binaries before they are ever loaded.
    """
    try:
        raw = wasm_path.read_bytes()
    except OSError as exc:
        return [f"Could not read '{wasm_path.name}': {exc}"]

    warnings: list[str] = []
    for prefix in _SUSPICIOUS_WASI_PREFIXES:
        if prefix in raw:
            warnings.append(
                f"'{wasm_path.name}' contains a suspicious WASI import: {prefix.decode()!r}. "
                "This plugin may attempt to access host filesystem, network, or I/O resources. "
                "Only install plugins from trusted sources."
            )
    return warnings


def security_check_plugin(
    spec: PluginSpec,
    *,
    check_checksums: bool = True,
    check_capabilities: bool = True,
) -> tuple[list[str], list[str]]:
    """
    Run all post-install security checks for *spec*.

    Returns
    -------
    tuple[list[str], list[str]]
        ``(errors, warnings)`` where errors are checksum failures that must
        block loading, and warnings are capability concerns that should be
        surfaced to the user.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if check_checksums:
        if not spec.wasm_checksums:
            warnings.append(
                f"No wasm_checksums recorded for '{spec.package_name}'. "
                "Integrity cannot be verified. "
                "Use 'intentumdiff plugins add' to populate checksums from the official registry."
            )
        else:
            errors.extend(verify_wasm_checksums(spec))

    if check_capabilities:
        try:
            dist = _meta.distribution(spec.package_name)
        except _meta.PackageNotFoundError:
            errors.append(f"Package '{spec.package_name}' not found after installation.")
            return errors, warnings

        for record in dist.files or []:
            if not str(record).endswith(".wasm"):
                continue
            full_path = Path(str(dist.locate_file(record)))
            if full_path.exists():
                warnings.extend(check_wasm_capabilities(full_path))

    return errors, warnings


# ---------------------------------------------------------------------------
# pip wrappers
# ---------------------------------------------------------------------------


def classify_install_target(install_target: str) -> str:
    """
    Classify *install_target* into one of the following categories.

    Categories
    ----------
    ``"vcs"``
        A VCS URL such as ``git+https://…`` or ``hg+ssh://…``.
    ``"local"``
        A local path (relative ``./…`` / ``../…`` or absolute ``/…`` / ``C:\\…``).
    ``"direct_url"``
        A direct HTTP/HTTPS/FTP URL.
    ``"pypi"``
        A plain package name or PEP 440 version specifier.
    ``"unknown"``
        Anything that does not match the above patterns.
    """
    import re

    t = install_target.strip()
    if re.search(r"\s@\s*(?:https?|ftp|file|git\+)://", t, flags=re.IGNORECASE):
        return "direct_url"
    if t.startswith(("git+", "hg+", "svn+", "bzr+")):
        return "vcs"
    if t.startswith(("./", "../", "/")) or (
        len(t) > 2 and t[1] == ":" and t[2] in ("/", "\\")
    ):
        return "local"
    if t.lower().startswith(("http://", "https://", "ftp://")):
        return "direct_url"
    if re.match(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?([<>=!;\[@ ]|$)", t):
        return "pypi"
    return "unknown"


# Wheel entries that are never permitted in plugin packages.
_BLOCKED_WHEEL_EXTENSIONS: tuple[str, ...] = (
    ".so",   # native extension (Linux / macOS)
    ".pyd",  # native extension (Windows)
    ".dll",  # native extension (Windows)
)


def _lint_wheel_contents(wheel_path: "Path", *, allow_wasm: bool = True) -> list[str]:
    """
    Scan *wheel_path* for entries that are never permitted in plugin packages.

    Returns a list of error strings.  An empty list means the wheel is clean.

    Blocked entries
    ---------------
    - ``.pth`` files — inject into ``sys.path`` at interpreter startup.
    - ``sitecustomize.py`` / ``usercustomize.py`` — startup hook injection.
    - Native extensions (``.so``, ``.pyd``, ``.dll``) — arbitrary native code
      outside the Wasm sandbox.
    """
    import zipfile

    errors: list[str] = []
    try:
        with zipfile.ZipFile(wheel_path, "r") as zf:
            for name in zf.namelist():
                lower = name.lower()
                basename = Path(name).name.lower()
                posix_path = PurePosixPath(name)
                windows_path = PureWindowsPath(name)
                if (
                    posix_path.is_absolute()
                    or windows_path.is_absolute()
                    or windows_path.drive
                    or ".." in posix_path.parts
                    or ".." in windows_path.parts
                ):
                    errors.append(
                        f"Wheel '{wheel_path.name}' contains an unsafe archive "
                        f"path ({name!r}) — rejected."
                    )
                elif lower.endswith(".pth"):
                    errors.append(
                        f"Wheel '{wheel_path.name}' contains a .pth file "
                        f"({name!r}) — rejected. Plugin wheels must be "
                        "self-contained and must not inject sys.path entries."
                    )
                elif basename in ("sitecustomize.py", "usercustomize.py"):
                    errors.append(
                        f"Wheel '{wheel_path.name}' contains a startup hook "
                        f"({name!r}) — rejected. Plugin wheels must not include "
                        "interpreter startup hooks."
                    )
                elif lower.endswith(".wasm") and not allow_wasm:
                    errors.append(
                        f"Wheel '{wheel_path.name}' contains undeclared Wasm "
                        f"({name!r}) — rejected. Dependency wheels must not "
                        "ship Wasm binaries."
                    )
                elif any(lower.endswith(ext) for ext in _BLOCKED_WHEEL_EXTENSIONS):
                    errors.append(
                        f"Wheel '{wheel_path.name}' contains a native extension "
                        f"({name!r}) — rejected. Plugin wheels must not include "
                        "native compiled extensions outside of .wasm binaries."
                    )
    except (zipfile.BadZipFile, OSError) as exc:
        errors.append(f"Could not inspect wheel '{wheel_path.name}': {exc}")
    return errors


def _dep_hash_requirement_lines(spec: "PluginSpec") -> list[str]:
    return [
        f"{pkg_spec} \\\n    --hash={pkg_hash}"
        for pkg_spec, pkg_hash in spec.dep_hashes.items()
    ]


def _write_dep_hash_requirements(spec: "PluginSpec", req_path: "Path") -> None:
    req_path.write_text(
        "\n".join(_dep_hash_requirement_lines(spec)) + "\n",
        encoding="utf-8",
    )


def pip_download_requirements(requirements_file: "Path", dest_dir: "Path") -> int:
    """Download exactly the hash-pinned requirements into *dest_dir*."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--require-hashes",
        "--only-binary=:all:",
        "-r",
        str(requirements_file),
        "-d",
        str(dest_dir),
    ]
    return subprocess.run(cmd).returncode  # noqa: S603 - fixed pip command, no shell


def _lint_wheelhouse(
    wheel_files: list["Path"],
    spec: "PluginSpec",
    install_target: str | None,
) -> list[str]:
    errors: list[str] = []
    main_names = _main_package_names(spec, install_target)
    for wheel in wheel_files:
        errors.extend(
            _lint_wheel_contents(
                wheel,
                allow_wasm=_package_from_wheel(wheel) in main_names,
            )
        )
    return errors


def _find_main_wheel(
    wheel_files: list["Path"],
    spec: "PluginSpec",
    install_target: str | None,
) -> "Path | None":
    main_names = _main_package_names(spec, install_target)
    for wheel in wheel_files:
        if _package_from_wheel(wheel) in main_names:
            return wheel
    return None


def pip_install(
    install_target: str,
    spec: "PluginSpec | None" = None,
    *,
    upgrade: bool = False,
) -> int:
    """
    Install *install_target* in the current environment.

    Security policy
    ---------------
    - When *spec.dep_hashes* is **empty** (default): adds ``--no-deps`` so pip
      cannot resolve or install undeclared transitive dependencies.  The plugin
      wheel must be fully self-contained.
    - When *spec.dep_hashes* is **non-empty**: downloads the complete
      hash-pinned wheelhouse, lints every wheel, then installs from that local
      wheelhouse with ``--no-index`` and ``--require-hashes``.  The main
      package **must** be listed as one of the entries.

    Returns the pip exit code (0 = success).
    """
    import tempfile

    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")

    if spec is not None and spec.dep_hashes:
        # Validate key format before doing anything with the network.
        hash_errors = _validate_dep_hashes(spec, install_target)
        if hash_errors:
            for msg in hash_errors:
                logger.error("dep_hashes validation error: %s", msg)
            raise ValueError(
                f"dep_hashes for {spec.package_name!r} failed validation:\n"
                + "\n".join(f"  - {e}" for e in hash_errors)
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            wheelhouse = tmp_path / "wheelhouse"
            wheelhouse.mkdir()
            tmp_req = tmp_path / "requirements.txt"
            _write_dep_hash_requirements(spec, tmp_req)

            rc = pip_download_requirements(tmp_req, wheelhouse)
            if rc != 0:
                return rc

            wheel_files = sorted(wheelhouse.glob("*.whl"))
            lint_errors = _lint_wheelhouse(wheel_files, spec, install_target)
            if lint_errors:
                raise ValueError(
                    f"dep_hashes for {spec.package_name!r} failed wheel lint:\n"
                    + "\n".join(f"  - {e}" for e in lint_errors)
                )
            if _find_main_wheel(wheel_files, spec, install_target) is None:
                raise ValueError(
                    f"dep_hashes for {spec.package_name!r} did not download "
                    "the main plugin wheel."
                )

            cmd.extend([
                "--no-deps",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-index",
                "--find-links", str(wheelhouse),
                "-r", str(tmp_req),
            ])
            return subprocess.run(cmd).returncode  # noqa: S603 - fixed pip command, no shell
    else:
        # Default: self-contained wheel — no undeclared dependencies.
        cmd.extend(["--no-deps", install_target])
        return subprocess.run(cmd).returncode  # noqa: S603 - fixed pip command, no shell; install target is operator-selected.


def pip_uninstall(package_name: str) -> int:
    """
    Run ``python -m pip uninstall -y <package_name>`` in the current env.

    Returns the pip exit code (0 = success).
    """
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", package_name]
    return subprocess.run(cmd).returncode  # noqa: S603 - fixed pip command, no shell; package name comes from selected plugin spec.


def pip_download(install_target: str, dest_dir: str, *, only_binary: bool = True) -> int:
    """
    Run ``python -m pip download --no-deps [--only-binary=:all:] <install_target> -d <dest_dir>``.

    Downloads the wheel (or sdist) to *dest_dir* without installing anything.
    When *only_binary* is ``True`` (default), ``--only-binary=:all:`` is added
    so pip refuses to build from source.
    Returns the pip exit code (0 = success).
    """
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--no-deps",
    ]
    if only_binary:
        cmd.append("--only-binary=:all:")
    cmd.extend([install_target, "-d", dest_dir])
    return subprocess.run(cmd).returncode  # noqa: S603 - fixed pip command, no shell; destination is caller-controlled temp dir.


def inspect_wasm_in_wheel(
    wheel_path: "Path",
) -> tuple[dict[str, str], list[str]]:
    """
    Open *wheel_path* as a ZIP archive and inspect every ``.wasm`` entry.

    Returns
    -------
    tuple[dict[str, str], list[str]]
        ``(checksums, capability_warnings)`` where *checksums* maps wasm
        filename → SHA-256 hex digest and *capability_warnings* is a list of
        human-readable warnings from :func:`check_wasm_capabilities`.

    The wheel is not installed — no Python code from the plugin is executed.
    """
    import io
    import zipfile

    checksums: dict[str, str] = {}
    cap_warnings: list[str] = []

    try:
        with zipfile.ZipFile(wheel_path, "r") as zf:
            for name in zf.namelist():
                if not name.endswith(".wasm"):
                    continue
                raw = zf.read(name)
                fname = Path(name).name
                checksums[fname] = hashlib.sha256(raw).hexdigest()
                # Run capability scan on the in-memory bytes via a temp Path.
                import tempfile
                with tempfile.NamedTemporaryFile(
                    suffix=".wasm", delete=False
                ) as tmp:
                    tmp.write(raw)
                    tmp_path = Path(tmp.name)
                try:
                    cap_warnings.extend(check_wasm_capabilities(tmp_path))
                finally:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("Could not inspect wheel %s: %s", wheel_path.name, exc)

    return checksums, cap_warnings


def pre_install_security_check(
    install_target: str,
    spec: "PluginSpec",
    *,
    allow_source: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Download the plugin wheel and verify its ``.wasm`` contents **before**
    running ``pip install``.

    Steps
    -----
    1. Classify *install_target*; reject VCS / local / direct-URL sources
       unless *allow_source* is ``True``.
    2. ``pip download --only-binary=:all:`` the wheel to a temporary directory.
    3. Open the wheel as a ZIP; extract and hash every ``.wasm`` file.
    4. Run the capability scan on each ``.wasm``.
    5. If *spec.wasm_checksums* is non-empty, verify the pre-install digests
       against the expected values.

    Returns ``(errors, warnings)``.  A non-empty *errors* list means the
    download should be refused and ``pip install`` must not be called.

    The temporary download directory is cleaned up before returning.
    """
    import tempfile

    errors: list[str] = []
    warnings: list[str] = []
    hash_errors = _validate_dep_hashes(spec, install_target)
    if hash_errors:
        return hash_errors, warnings

    # ── 0. Classify the install target ────────────────────────────────────────
    kind = classify_install_target(install_target)
    if kind in ("vcs", "local", "direct_url"):
        if not allow_source:
            errors.append(
                f"Install target '{install_target}' is a {kind} source, which "
                "cannot be verified before installation.  Pass "
                "--allow-source-plugin to override (not recommended for "
                "production)."
            )
            return errors, warnings
        warnings.append(
            f"Installing from {kind} source '{install_target}' — "
            "pre-install wasm scan is limited for source distributions."
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        # ── 1. Download ────────────────────────────────────────────────────
        # Use --only-binary=:all: for PyPI so pip refuses to build from source.
        tmp_path = Path(tmp_dir)
        if spec.dep_hashes:
            req_path = tmp_path / "requirements.txt"
            _write_dep_hash_requirements(spec, req_path)
            rc = pip_download_requirements(req_path, tmp_path)
        else:
            use_binary_only = kind == "pypi"
            rc = pip_download(install_target, tmp_dir, only_binary=use_binary_only)
        if rc != 0:
            errors.append(
                f"pip download failed (exit {rc}) — refusing to install "
                "without pre-install verification."
            )
            return errors, warnings

        wheel_files = sorted(Path(tmp_dir).glob("*.whl"))
        if not wheel_files:
            errors.append(
                "No wheel found in download output — refusing to install "
                "a source distribution without pre-install verification.  "
                "Use --allow-source-plugin to override."
            )
            return errors, warnings

        wheel = wheel_files[0]
        if spec.dep_hashes:
            lint_errors = _lint_wheelhouse(wheel_files, spec, install_target)
            main_wheel = _find_main_wheel(wheel_files, spec, install_target)
            if main_wheel is None:
                errors.append(
                    "No wheel for the main plugin package was found in the "
                    "hash-pinned wheelhouse."
                )
                return errors, warnings
            wheel = main_wheel
        else:
            lint_errors = _lint_wheel_contents(wheel)

        # ── 1b. Wheel content linter ───────────────────────────────────────
        errors.extend(lint_errors)
        if lint_errors:
            return errors, warnings

        pre_checksums, cap_warnings = inspect_wasm_in_wheel(wheel)
        warnings.extend(cap_warnings)

        if not pre_checksums:
            if spec.wasm_checksums:
                # Registry declared expected .wasm files that are absent — fail closed.
                errors.append(
                    f"No .wasm files found in {wheel.name} but "
                    f"{len(spec.wasm_checksums)} checksum(s) are expected "
                    f"({', '.join(spec.wasm_checksums.keys())}). "
                    "The wheel does not match the registry record — "
                    "possible substitution or wrong version."
                )
            else:
                warnings.append(
                    f"No .wasm files found in {wheel.name} — "
                    "pre-install checksum verification skipped."
                )
        elif spec.wasm_checksums:
            # Verify pre-download digests against expected values.
            for fname, expected_hex in spec.wasm_checksums.items():
                if fname not in pre_checksums:
                    errors.append(
                        f"Expected .wasm file '{fname}' not found in the "
                        f"downloaded wheel '{wheel.name}'."
                    )
                    continue
                if pre_checksums[fname] != expected_hex.lower():
                    errors.append(
                        f"Pre-install checksum mismatch for '{fname}':\n"
                        f"  expected: {expected_hex}\n"
                        f"  in wheel: {pre_checksums[fname]}\n"
                        "The downloaded binary does not match the recorded "
                        "checksum — possible tampering or wrong version."
                    )

    return errors, warnings
