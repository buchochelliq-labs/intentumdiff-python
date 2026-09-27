"""
User-registerable schema/diff profiles (issue #63, external-config path).

Teams can teach the engine their own JSON/YAML dialects without a code change by
dropping declarative descriptor files into a schemas directory:

- ``~/.intentumdiff/schemas/*.yml`` (or ``INTENTUMDIFF_USER_SCHEMA_DIR``)
- ``<cwd>/.intentumdiff/schemas/*.yml`` (project-level, wins over the home dir)

A descriptor registers one of three modes (all offline-first):

1. **Profile descriptor** — identity/keying written directly::

       language_id: acme-service-config
       match:
         filename_patterns: ["service.acme.json"]
         root_markers: ["acmeApiVersion"]
       identity_fields: [name]
       keyed_arrays: { /routes: [path, method] }

2. **Local JSON Schema file** — ``schema: ./service.schema.json`` (relative to
   the descriptor). Identity hints are derived with the same extraction the
   resolver applies to remote provider schemas. The file is read locally and is
   **never fetched or logged** — the privacy-preferred path for proprietary
   schemas.

3. **Provider entry** — ``match.schema_urls`` claims a document's declared
   ``$schema`` URL so internal dialects resolve **offline** instead of via the
   guarded fetch path.

The registry is thin shell + config: descriptors are validated in the shared Rust engine and their
identity fields are marshaled through the existing ``SchemaResolution`` channel
into the Rust core's keyed matching. Malformed descriptors fail closed — the
whole file is rejected with a clear error and built-in resolution proceeds.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from intentumdiff.rust_core import _c_abi_call

logger = logging.getLogger(__name__)

USER_SCHEMA_DIR_ENV = "INTENTUMDIFF_USER_SCHEMA_DIR"
USER_SCHEMAS_TOGGLE_ENV = "INTENTUMDIFF_USER_SCHEMAS"

# Provider-id roots owned by built-in detection; a registered language_id must
# not collide with these (the invariance guard from issue #63's acceptance).
BUILTIN_PROVIDER_ROOTS = frozenset(
    {
        "adf",
        "azure-pipelines",
        "databricks",
        "dbt",
        "embedded",
        "github-actions",
        "kubernetes",
        "none",
        "openapi",
        "user",
    }
)

_DESCRIPTOR_SUFFIXES = (".yml", ".yaml", ".json")


@dataclass(frozen=True)
class UserSchemaProfile:
    language_id: str
    source_path: str
    filename_patterns: tuple[str, ...] = ()
    root_markers: tuple[str, ...] = ()
    schema_urls: tuple[str, ...] = ()
    identity_fields: frozenset[str] = frozenset()
    important_paths: tuple[str, ...] = ()
    scaffold_paths: tuple[str, ...] = ()
    schema: dict[str, Any] | None = None
    schema_path: str | None = None
    # XML dialect fields (issue #86): element tag -> key fields (child-element
    # text, or attr:NAME attribute values), plus the Rust-side match predicate.
    keyed_elements: dict[str, tuple[str, ...]] = field(default_factory=dict)
    namespace: str | None = None
    root_element: str | None = None
    fingerprint: str = ""

    @property
    def provider_id(self) -> str:
        return f"user:{self.language_id}"


def user_schema_dirs(env: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Descriptor directories in precedence order (first match wins)."""
    values = env or os.environ
    dirs: list[Path] = [Path.cwd() / ".intentumdiff" / "schemas"]
    override = values.get(USER_SCHEMA_DIR_ENV)
    if override:
        dirs.append(Path(override))
    else:
        dirs.append(Path.home() / ".intentumdiff" / "schemas")
    return tuple(dirs)


def user_schemas_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = env or os.environ
    return (values.get(USER_SCHEMAS_TOGGLE_ENV) or "").strip().lower() not in {
        "off",
        "0",
        "false",
    }


def load_user_schema_profiles(
    env: Mapping[str, str] | None = None,
) -> tuple[tuple[UserSchemaProfile, ...], tuple[str, ...]]:
    """Load and validate every registered descriptor.

    Returns ``(profiles, errors)``. A descriptor file that fails validation
    contributes an error message and NO profiles (fail closed, never partially
    applied).
    """
    if not user_schemas_enabled(env):
        return ((), ())
    files: list[Path] = []
    for directory in user_schema_dirs(env):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.suffix.lower() in _DESCRIPTOR_SUFFIXES and entry.is_file():
                files.append(entry)
    profiles: list[UserSchemaProfile] = []
    errors: list[str] = []
    for path in files:
        loaded, file_errors = _load_descriptor_file(path)
        if file_errors:
            errors.extend(file_errors)
            continue
        profiles.extend(loaded)
    selected = _schema_operation("select_unique", profiles=[_profile_payload(p) for p in profiles])
    profiles = [profiles[index] for index in selected]
    for message in errors:
        logger.warning("user schema descriptor rejected: %s", message)
    result = (tuple(profiles), tuple(errors))
    return result


def _profile_payload(profile: UserSchemaProfile) -> dict[str, Any]:
    value = asdict(profile)
    value["identity_fields"] = sorted(profile.identity_fields)
    return value


def _schema_operation(operation: str, **payload: Any) -> Any:
    return _c_abi_call("schema_profiles", json.dumps({"operation": operation, **payload}))


def match_user_profile(
    profiles: tuple[UserSchemaProfile, ...], *, filename: str, content: str,
    declared_url: str | None = None,
) -> UserSchemaProfile | None:
    index = _schema_operation(
        "match", profiles=[_profile_payload(p) for p in profiles], filename=filename,
        content=content, declared_url=declared_url,
    )
    return profiles[index] if index is not None else None


def _load_descriptor_file(
    path: Path,
) -> tuple[tuple[UserSchemaProfile, ...], tuple[str, ...]]:
    try:
        raw_text = path.read_bytes().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - any parse failure fails the file closed
        return ((), (f"{path}: unreadable descriptor ({exc})",))
    parsed = _schema_operation("parse_documents", raw_text=raw_text)
    if parsed["error"]:
        return (), (f"{path}: unreadable descriptor ({parsed['error']})",)
    documents = parsed["documents"]
    profiles: list[UserSchemaProfile] = []
    errors: list[str] = []
    for index, document in enumerate(documents):
        profile, document_errors = _validate_descriptor(document, path, index, raw_text)
        if document_errors:
            errors.extend(document_errors)
        elif profile is not None:
            profiles.append(profile)
    if errors:
        # Fail the whole file closed — never partially apply a descriptor file.
        return ((), tuple(errors))
    if not profiles:
        return ((), (f"{path}: descriptor file contains no profiles",))
    return (tuple(profiles), ())


def _validate_descriptor(
    document: Any, path: Path, index: int, raw_text: str,
) -> tuple[UserSchemaProfile | None, tuple[str, ...]]:
    # Filesystem access stays in the host. The engine validates the descriptor
    # and supplied schema contents and derives all profile/identity fields.
    schema = None
    schema_path = None
    schema_raw = None
    schema_ref = document.get("schema") if isinstance(document, dict) else None
    if isinstance(schema_ref, str) and schema_ref.strip():
        resolved = (path.parent / schema_ref).resolve()
        try:
            schema_raw = resolved.read_bytes().decode("utf-8")
            schema = json.loads(schema_raw)
            schema_path = str(resolved)
        except FileNotFoundError:
            return None, (f"{path}#{index}: schema file not found: {resolved}",)
        except Exception as exc:  # noqa: BLE001 - host read/decode error
            return None, (f"{path}#{index}: schema file unreadable ({exc})",)
    result = _schema_operation(
        "validate", document=document, path=str(path), index=index, raw_text=raw_text,
        schema=schema, schema_path=schema_path, schema_raw=schema_raw,
    )
    if result["errors"]:
        return None, tuple(result["errors"])
    value = result["profile"]
    for key in ("filename_patterns", "root_markers", "schema_urls", "important_paths", "scaffold_paths"):
        value[key] = tuple(value[key])
    value["identity_fields"] = frozenset(value["identity_fields"])
    value["keyed_elements"] = {tag: tuple(fields) for tag, fields in value["keyed_elements"].items()}
    return UserSchemaProfile(**value), ()


def user_xml_dialects_payload(
    profiles: tuple[UserSchemaProfile, ...],
) -> list[dict[str, Any]]:
    """The engine-side dialect specs (issue #86) for profiles that declare
    ``keyed_elements`` — marshaled as-is into the Rust registry."""
    return [
        {
            "language_id": profile.language_id,
            "root_element": profile.root_element,
            "namespace": profile.namespace,
            "keyed_elements": {tag: list(fields) for tag, fields in profile.keyed_elements.items()},
        }
        for profile in profiles
        if profile.keyed_elements
    ]
