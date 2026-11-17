"""Validate the plugin registry manifest (#95) by transporting core's validator (#329).

The JSON Schema (``registry.schema.json``, packaged alongside this module) is the documented
contract. Its enforcement is core's ``validate_registry_manifest`` operation: field types,
enums, patterns, the official-tier policy (immutable refs, checksums, capabilities and
``abi_target`` required) and the capability vocabulary. This module only marshals the parsed
manifest to core and formats core's ``{"path", "message"}`` errors; it returns every error
(empty == valid) rather than raising, so a vetting run can report every problem in one pass.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from intentumdiff.plugins.exceptions import RegistryManifestError

#: The plugin-contract ABI version this host implements. Mirrors ``package
#: intentdiff:plugin@X`` in ``plugins/wit/plugin.wit``; the drift-guard test
#: ``test_host_contract_version_matches_the_wit`` pins them together so a WIT bump forces a
#: conscious host bump (#94: the host must know which contract version it speaks).
HOST_CONTRACT_VERSION = "1.0.0"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(value.strip())
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


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


def load_schema() -> dict[str, Any]:
    """Return the packaged registry JSON Schema."""
    schema = resources.files("intentumdiff.plugins").joinpath("registry.schema.json")
    return json.loads(schema.read_text(encoding="utf-8"))


def registry_manifest_errors(data: Any) -> list[dict[str, str]]:
    """Core's errors for the parsed manifest *data*, unchanged (``[]`` == valid).

    Raises :class:`RegistryManifestError` when *data* has no JSON form (for example a YAML
    date or a NaN), since core validates the JSON document.
    """
    from intentumdiff.rust_core import validate_registry_manifest

    try:
        manifest_json = json.dumps(data, allow_nan=False)
    except (TypeError, ValueError) as exc:
        message = f"plugin registry manifest is not JSON-representable: {exc}"
        raise RegistryManifestError(message) from exc
    return validate_registry_manifest(manifest_json)


def format_manifest_error(error: dict[str, str]) -> str:
    """``"<json pointer>: <message>"``; the document root is shown as ``/``."""
    return f"{error['path'] or '/'}: {error['message']}"


def check_registry_manifest(data: Any) -> None:
    """Raise :class:`RegistryManifestError` carrying core's errors when *data* is invalid."""
    errors = registry_manifest_errors(data)
    if errors:
        text = "; ".join(map(format_manifest_error, errors))
        raise RegistryManifestError(f"invalid plugin registry manifest: {text}", errors)


def validate_registry(data: Any) -> list[str]:
    """Return every violation core reports for *data* (empty list == a valid manifest)."""
    return [format_manifest_error(error) for error in registry_manifest_errors(data)]


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

    count = f"_{len(plugins)} plugins — generated from the registry manifest._"
    lines = [f"# {title}", "", count, ""]

    def _section(heading: str, names: list[str], note: str) -> None:
        lines.extend([f"## {heading} ({len(names)})", ""] + ([f"> {note}", ""] if note else []))
        if not names:
            return lines.extend(["_None._", ""])
        for name in names:
            entry = plugins[name]
            lines.append(f"### {name}")
            desc = entry.get("description")
            lines.extend((["", str(desc)] if desc else []) + [""])
            lines.append(f"- **Source**: {entry.get('source', 'git')} `{entry.get('ref', 'main')}`")
            if entry.get("abi_target"):
                lines.append(f"- **ABI target**: `{entry['abi_target']}`")
            caps = entry.get("capabilities") or []
            if caps:
                lines.append(f"- **Capabilities**: {', '.join(f'`{c}`' for c in caps)}")
            lines.append(f"- **Install**: `intentumdiff plugins add {_install_target(name, entry)}`")
            lines.append("")

    _section("Official", official, "Org-built and scanned; checksums verified on install.")
    _section("Community", community, "Listed but unverified (custom source or no checksums). "
             "Install only if you trust the source.")
    return "\n".join(lines).rstrip() + "\n"


def _load_registry_document(path: str) -> Any:
    """Parse a registry manifest from a ``.yaml``/``.json`` file."""
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml  # pyyaml is already a runtime dep (hub.py)
        return yaml.safe_load(text)
    return json.loads(text)


def main(argv: list[str] | None = None) -> int:
    """CLI for the #95 vetting pipeline: validate a registry manifest, exit non-zero on any
    violation. The registry PR gate runs ``python -m intentumdiff.plugins.registry_schema
    registry.yaml`` (with the checksum/capability/provenance checks) before merge."""
    import argparse

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("manifest", help="path to registry.yaml (or .json)")
    parser.add_argument("--catalog", action="store_true",
                        help="print the Markdown discovery catalog after validating (#95)")
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
