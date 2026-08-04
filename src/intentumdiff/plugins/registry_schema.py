"""Validate the plugin registry manifest against its published schema (#95).

The JSON Schema (``registry.schema.json``, packaged alongside this module) is the documented
contract. This module is its dependency-free ENFORCEMENT: :func:`validate_registry` checks the
security-load-bearing subset (required top-level shape, name/checksum/dep-hash patterns, the
trust-tier and source enums, no unknown fields) so the #95 CI vetting pipeline can block a
malformed or untrusted registry entry BEFORE it is published — the registry is the root of
trust. Stdlib-only (no ``jsonschema`` dependency), matching the thin-binding rule.

Returns a list of human-readable error strings (empty == valid) rather than raising, so a
vetting run can report every problem in one pass.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

_NAME_RE = re.compile(r"^(?:intentumdiff-)?[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WASM_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.wasm$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_DEP_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_DEP_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[^=\s]+$")

_SOURCE_VALUES = frozenset({"git", "pypi"})
_TRUST_TIERS = frozenset({"official", "community"})

#: The plugin-contract ABI version this host implements. Mirrors ``package
#: intentdiff:plugin@X`` in ``plugins/wit/plugin.wit``; the drift-guard test
#: ``test_host_contract_version_matches_the_wit`` pins them together so a WIT bump forces a
#: conscious host bump (#94: the host must know which contract version it speaks).
HOST_CONTRACT_VERSION = "1.0.0"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def abi_compatible(target: str, host: str = HOST_CONTRACT_VERSION) -> bool:
    """Whether a plugin built against contract *target* can run on *host* (#94).

    Semver contract compatibility: same MAJOR (a major bump is a breaking WIT change) and the
    host's MINOR is at least the target's — minor bumps are additive, so a newer host still
    serves an older plugin, but a host older than the plugin's minor lacks the WIT features the
    plugin was built against. PATCH is interface-irrelevant. A malformed version (either side)
    is treated as INCOMPATIBLE — fail closed rather than load an unknowable ABI.
    """
    parsed_target = _parse_version(target)
    parsed_host = _parse_version(host)
    if parsed_target is None or parsed_host is None:
        return False
    return parsed_target[0] == parsed_host[0] and parsed_target[1] <= parsed_host[1]

_ENTRY_FIELDS = frozenset(
    {
        "source",
        "ref",
        "repo",
        "description",
        "wasm_checksums",
        "dep_hashes",
        "allowed_dependencies",
        "trust_tier",
        "capabilities",
        "abi_target",
        "provenance_manifest_ref",
    }
)
_TOP_LEVEL_FIELDS = frozenset({"version", "plugins"})


def load_schema() -> dict[str, Any]:
    """Return the packaged registry JSON Schema."""
    text = resources.files("intentumdiff.plugins").joinpath("registry.schema.json").read_text(
        encoding="utf-8"
    )
    return json.loads(text)


def _validate_entry(name: str, entry: Any, errors: list[str]) -> None:
    where = f"plugins[{name!r}]"
    if not isinstance(entry, dict):
        errors.append(f"{where}: entry must be a mapping")
        return

    unknown = set(entry) - _ENTRY_FIELDS
    if unknown:
        errors.append(f"{where}: unknown field(s): {', '.join(sorted(unknown))}")

    source = entry.get("source", "git")
    if source not in _SOURCE_VALUES:
        errors.append(f"{where}.source: {source!r} not in {sorted(_SOURCE_VALUES)}")

    trust = entry.get("trust_tier", "official")
    if trust not in _TRUST_TIERS:
        errors.append(f"{where}.trust_tier: {trust!r} not in {sorted(_TRUST_TIERS)}")

    for str_field in ("ref", "repo", "description", "abi_target", "provenance_manifest_ref"):
        if str_field in entry and not isinstance(entry[str_field], str):
            errors.append(f"{where}.{str_field}: must be a string")

    checksums = entry.get("wasm_checksums", {})
    if not isinstance(checksums, dict):
        errors.append(f"{where}.wasm_checksums: must be a mapping")
    else:
        for fname, digest in checksums.items():
            if not _WASM_FILE_RE.match(str(fname)):
                errors.append(f"{where}.wasm_checksums: {fname!r} is not a .wasm filename")
            if not (isinstance(digest, str) and _SHA256_RE.match(digest)):
                errors.append(f"{where}.wasm_checksums[{fname!r}]: not a lowercase SHA-256 hex")

    dep_hashes = entry.get("dep_hashes", {})
    if not isinstance(dep_hashes, dict):
        errors.append(f"{where}.dep_hashes: must be a mapping")
    else:
        for key, value in dep_hashes.items():
            if not _DEP_KEY_RE.match(str(key)):
                errors.append(f"{where}.dep_hashes: {key!r} is not 'package==version'")
            if not (isinstance(value, str) and _DEP_HASH_RE.match(value)):
                errors.append(f"{where}.dep_hashes[{key!r}]: not 'sha256:<hex>'")

    for list_field in ("allowed_dependencies", "capabilities"):
        if list_field in entry:
            value = entry[list_field]
            if not (isinstance(value, list) and all(isinstance(i, str) for i in value)):
                errors.append(f"{where}.{list_field}: must be a list of strings")


def validate_registry(data: Any) -> list[str]:
    """Return every schema violation in *data* (empty list == a valid registry manifest)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["registry: top level must be a mapping"]

    unknown = set(data) - _TOP_LEVEL_FIELDS
    if unknown:
        errors.append(f"registry: unknown top-level field(s): {', '.join(sorted(unknown))}")

    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("registry.version: required integer >= 1")

    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        errors.append("registry.plugins: required mapping of name -> entry")
        return errors

    for name, entry in plugins.items():
        if not _NAME_RE.match(str(name)):
            errors.append(f"plugins: {name!r} is not a package-safe plugin name")
        _validate_entry(str(name), entry, errors)

    return errors


def _effective_tier(entry: dict) -> str:
    """The trust tier a catalog should display — mirrors ``RegistryEntry.effective_trust_tier``
    without importing hub.py (this module is the lower-level contract layer)."""
    declared = entry.get("trust_tier") or "official"
    if declared == "community" or entry.get("repo") or not entry.get("wasm_checksums"):
        return "community"
    return "official"


def _install_target(name: str, entry: dict) -> str:
    ref = entry.get("ref", "")
    if entry.get("source") == "pypi":
        return f"intentumdiff-{name}=={ref}" if ref else f"intentumdiff-{name}"
    return name


def render_catalog_markdown(registry: Any, *, title: str = "IntentumDiff plugin catalog") -> str:
    """Render a browsable Markdown discovery catalog from a registry manifest (#95).

    The manifest stays the source of truth; this is a generated VIEW — official plugins first,
    then community, each with its description, source, ABI target, capabilities, and install
    command. Robust to missing optional fields (older entries).
    """
    plugins: dict = registry.get("plugins", {}) if isinstance(registry, dict) else {}
    official = sorted(n for n, e in plugins.items() if _effective_tier(e) == "official")
    community = sorted(n for n, e in plugins.items() if _effective_tier(e) == "community")

    lines: list[str] = [f"# {title}", ""]
    lines.append(f"_{len(plugins)} plugins — generated from the registry manifest._")
    lines.append("")

    def _section(heading: str, names: list[str], note: str) -> None:
        lines.append(f"## {heading} ({len(names)})")
        lines.append("")
        if note:
            lines.append(f"> {note}")
            lines.append("")
        if not names:
            lines.append("_None._")
            lines.append("")
            return
        for name in names:
            entry = plugins[name]
            lines.append(f"### {name}")
            desc = entry.get("description")
            if desc:
                lines.append("")
                lines.append(str(desc))
            lines.append("")
            lines.append(f"- **Source**: {entry.get('source', 'git')} `{entry.get('ref', 'main')}`")
            if entry.get("abi_target"):
                lines.append(f"- **ABI target**: `{entry['abi_target']}`")
            caps = entry.get("capabilities") or []
            if caps:
                lines.append(f"- **Capabilities**: {', '.join(f'`{c}`' for c in caps)}")
            lines.append(f"- **Install**: `intentumdiff plugins add {_install_target(name, entry)}`")
            lines.append("")

    _section("Official", official, "Org-built and scanned; checksums verified on install.")
    _section(
        "Community",
        community,
        "Listed but unverified (custom source or no checksums). "
        "Install only if you trust the source.",
    )
    return "\n".join(lines).rstrip() + "\n"


def _load_registry_document(path: str) -> Any:
    """Parse a registry manifest from a ``.yaml``/``.json`` file."""
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml  # pyyaml is already a runtime dep (hub.py)

        return yaml.safe_load(text)
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    """CLI for the #95 vetting pipeline: validate a registry manifest, exit non-zero on any
    violation. The plugins-repo PR gate runs ``python -m intentumdiff.plugins.registry_schema
    registry.yaml`` (alongside the checksum/capability/provenance checks) to block a bad entry
    before merge."""
    import argparse

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("manifest", help="path to registry.yaml (or .json)")
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="after validating, print the Markdown discovery catalog to stdout (#95)",
    )
    args = parser.parse_args(argv)

    try:
        data = _load_registry_document(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read {args.manifest}: {exc}")
        return 2

    errors = validate_registry(data)
    if errors:
        print(f"Registry manifest {args.manifest} is INVALID ({len(errors)} problem(s)):")
        for err in errors:
            print(f"  - {err}")
        return 1
    if args.catalog:
        print(render_catalog_markdown(data), end="")
        return 0
    count = len(data.get("plugins", {})) if isinstance(data, dict) else 0
    print(f"Registry manifest {args.manifest} is valid ({count} plugin(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
