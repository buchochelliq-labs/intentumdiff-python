"""
User-registerable schema/diff profiles (issue #63, external-config path).

Teams can teach the engine their own JSON/YAML dialects without a code change by
dropping declarative descriptor files into a schemas directory:

- ``~/.intentdiff/schemas/*.yml`` (or ``INTENTDIFF_USER_SCHEMA_DIR``)
- ``<cwd>/.intentdiff/schemas/*.yml`` (project-level, wins over the home dir)

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

The registry is thin shell + config: descriptors are validated here and their
identity fields are marshaled through the existing ``SchemaResolution`` channel
into the Rust core's keyed matching. Malformed descriptors fail closed — the
whole file is rejected with a clear error and built-in resolution proceeds.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

USER_SCHEMA_DIR_ENV = "INTENTDIFF_USER_SCHEMA_DIR"
USER_SCHEMAS_TOGGLE_ENV = "INTENTDIFF_USER_SCHEMAS"

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
    dirs: list[Path] = [Path.cwd() / ".intentdiff" / "schemas"]
    override = values.get(USER_SCHEMA_DIR_ENV)
    if override:
        dirs.append(Path(override))
    else:
        dirs.append(Path.home() / ".intentdiff" / "schemas")
    return tuple(dirs)


def user_schemas_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = env or os.environ
    return (values.get(USER_SCHEMAS_TOGGLE_ENV) or "").strip().lower() not in {
        "off",
        "0",
        "false",
    }


# Cache keyed by a stat snapshot of every descriptor file, so edits (and test
# tmp dirs) invalidate naturally without a process restart.
_CACHE: dict[tuple[Any, ...], tuple[tuple[UserSchemaProfile, ...], tuple[str, ...]]] = {}


def load_user_schema_profiles(
    env: Mapping[str, str] | None = None,
) -> tuple[tuple[UserSchemaProfile, ...], tuple[str, ...]]:
    """Load, validate, and cache every registered descriptor.

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
    key = tuple(
        (str(path), stat.st_mtime_ns, stat.st_size)
        for path in files
        for stat in (path.stat(),)
    )
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    profiles: list[UserSchemaProfile] = []
    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    for path in files:
        loaded, file_errors = _load_descriptor_file(path)
        if file_errors:
            errors.extend(file_errors)
            continue
        for profile in loaded:
            if profile.language_id in seen_ids:
                # First registration wins (project dir precedes home dir).
                if seen_ids[profile.language_id] != str(path):
                    logger.warning(
                        "user schema %r in %s shadowed by earlier registration in %s",
                        profile.language_id,
                        path,
                        seen_ids[profile.language_id],
                    )
                continue
            seen_ids[profile.language_id] = str(path)
            profiles.append(profile)
    for message in errors:
        logger.warning("user schema descriptor rejected: %s", message)
    result = (tuple(profiles), tuple(errors))
    _CACHE[key] = result
    return result


def match_user_profile(
    profiles: tuple[UserSchemaProfile, ...],
    *,
    filename: str,
    content: str,
    declared_url: str | None = None,
) -> UserSchemaProfile | None:
    """First profile claiming the document, by declared ``$schema`` URL first
    (mode 3), then filename patterns / root markers (modes 1 and 2)."""
    if declared_url:
        for profile in profiles:
            if declared_url in profile.schema_urls:
                return profile
    normalized_path = filename.replace("\\", "/").lower()
    basename = normalized_path.rsplit("/", 1)[-1]
    for profile in profiles:
        if profile.filename_patterns:
            if not any(
                fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(normalized_path, pattern)
                for pattern in profile.filename_patterns
            ):
                continue
            if _root_markers_match(profile.root_markers, content):
                return profile
        elif profile.root_markers and _root_markers_match(profile.root_markers, content):
            return profile
    return None


def _root_markers_match(markers: tuple[str, ...], content: str) -> bool:
    """Heuristic top-level-key check, consistent with the built-in provider
    detectors (regex over raw text — no full parse on the hot path)."""
    if not markers:
        return True
    return all(
        re.search(rf'^\s*"?{re.escape(marker)}"?\s*:', content, re.MULTILINE)
        for marker in markers
    )


def _load_descriptor_file(
    path: Path,
) -> tuple[tuple[UserSchemaProfile, ...], tuple[str, ...]]:
    try:
        raw_text = path.read_text(encoding="utf-8")
        documents = [doc for doc in yaml.safe_load_all(raw_text) if doc is not None]
    except Exception as exc:  # noqa: BLE001 - any parse failure fails the file closed
        return ((), (f"{path}: unreadable descriptor ({exc})",))
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
    document: Any,
    path: Path,
    index: int,
    raw_text: str,
) -> tuple[UserSchemaProfile | None, tuple[str, ...]]:
    where = f"{path}#{index}"
    if not isinstance(document, dict):
        return (None, (f"{where}: descriptor must be a mapping",))
    errors: list[str] = []

    language_id = document.get("language_id")
    if not isinstance(language_id, str) or not language_id.strip():
        errors.append(f"{where}: 'language_id' is required and must be a string")
        language_id = ""
    else:
        language_id = language_id.strip()
        root = language_id.split(":", 1)[0].lower()
        if root in BUILTIN_PROVIDER_ROOTS:
            errors.append(
                f"{where}: language_id {language_id!r} collides with the "
                f"built-in provider root {root!r}"
            )

    match = document.get("match") or {}
    if not isinstance(match, dict):
        errors.append(f"{where}: 'match' must be a mapping")
        match = {}
    filename_patterns = _string_tuple(
        match.get("filename_patterns"), f"{where}: match.filename_patterns", errors
    )
    root_markers = _string_tuple(match.get("root_markers"), f"{where}: match.root_markers", errors)
    schema_urls = _string_tuple(match.get("schema_urls"), f"{where}: match.schema_urls", errors)
    namespace = match.get("namespace")
    if namespace is not None and (not isinstance(namespace, str) or not namespace.strip()):
        errors.append(f"{where}: match.namespace must be a non-empty string")
        namespace = None
    root_element = match.get("root_element")
    if root_element is not None and (not isinstance(root_element, str) or not root_element.strip()):
        errors.append(f"{where}: match.root_element must be a non-empty string")
        root_element = None
    has_match = (
        filename_patterns or root_markers or schema_urls or namespace or root_element
    )
    if not has_match:
        errors.append(f"{where}: 'match' needs filename_patterns, root_markers, or schema_urls")

    identity: set[str] = set(
        _string_tuple(document.get("identity_fields"), f"{where}: identity_fields", errors)
    )
    keyed_arrays = document.get("keyed_arrays") or {}
    if not isinstance(keyed_arrays, dict):
        errors.append(f"{where}: 'keyed_arrays' must be a mapping of path -> identity fields")
        keyed_arrays = {}
    for array_path, key_fields in keyed_arrays.items():
        fields = key_fields if isinstance(key_fields, list) else [key_fields]
        for key_field in fields:
            if not isinstance(key_field, str) or not key_field.strip():
                errors.append(f"{where}: keyed_arrays[{array_path!r}] entries must be strings")
            else:
                identity.add(key_field.strip())

    keyed_elements: dict[str, tuple[str, ...]] = {}
    raw_keyed_elements = document.get("keyed_elements") or {}
    if not isinstance(raw_keyed_elements, dict):
        errors.append(f"{where}: 'keyed_elements' must be a mapping of element tag -> key fields")
        raw_keyed_elements = {}
    for tag, key_fields in raw_keyed_elements.items():
        fields = key_fields if isinstance(key_fields, list) else [key_fields]
        clean: list[str] = []
        for key_field in fields:
            if not isinstance(key_field, str) or not key_field.strip():
                errors.append(f"{where}: keyed_elements[{tag!r}] entries must be strings")
            else:
                clean.append(key_field.strip())
        if not isinstance(tag, str) or not tag.strip():
            errors.append(f"{where}: keyed_elements tags must be strings")
        elif clean:
            keyed_elements[tag.strip()] = tuple(clean)
    if keyed_elements and not namespace and not root_element:
        # The engine-side dialect predicate matches by namespace/root element -
        # filename alone cannot reach the Rust matcher. Fail closed.
        errors.append(
            f"{where}: an XML dialect (keyed_elements) needs match.namespace "
            "or match.root_element"
        )

    important_paths = _string_tuple(
        document.get("important_paths"), f"{where}: important_paths", errors
    )
    scaffold_paths = _string_tuple(
        document.get("scaffold_paths"), f"{where}: scaffold_paths", errors
    )

    schema_dict: dict[str, Any] | None = None
    schema_path: str | None = None
    schema_ref = document.get("schema")
    if schema_ref is not None:
        if not isinstance(schema_ref, str) or not schema_ref.strip():
            errors.append(f"{where}: 'schema' must be a local file path")
        else:
            resolved = (path.parent / schema_ref).resolve()
            try:
                loaded = json.loads(resolved.read_text(encoding="utf-8"))
            except FileNotFoundError:
                errors.append(f"{where}: schema file not found: {resolved}")
                loaded = None
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{where}: schema file unreadable ({exc})")
                loaded = None
            if loaded is not None:
                if not isinstance(loaded, dict):
                    errors.append(f"{where}: schema file must contain a JSON object")
                else:
                    schema_dict = loaded
                    schema_path = str(resolved)
                    # Same conservative hint extraction the resolver applies to
                    # remote provider schemas — local, no fetch.
                    from intentdiff.analysis.schema_resolver import derive_identity_fields

                    identity |= derive_identity_fields(loaded)

    if not identity and not keyed_elements:
        errors.append(
            f"{where}: no identity source — provide identity_fields, keyed_arrays, "
            "or a local schema that yields identity hints"
        )
    if errors:
        return (None, tuple(errors))

    digest = hashlib.sha256()
    digest.update(raw_text.encode("utf-8"))
    if schema_path:
        digest.update(Path(schema_path).read_bytes())
    return (
        UserSchemaProfile(
            language_id=language_id,
            source_path=str(path),
            filename_patterns=filename_patterns,
            root_markers=root_markers,
            schema_urls=schema_urls,
            identity_fields=frozenset(identity),
            important_paths=important_paths,
            scaffold_paths=scaffold_paths,
            schema=schema_dict,
            schema_path=schema_path,
            keyed_elements=keyed_elements,
            namespace=namespace.strip() if isinstance(namespace, str) else None,
            root_element=root_element.strip() if isinstance(root_element, str) else None,
            fingerprint=f"user:{language_id}:{digest.hexdigest()[:16]}",
        ),
        (),
    )


def _string_tuple(value: Any, where: str, errors: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        errors.append(f"{where} must be a list of strings")
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{where} entries must be non-empty strings")
        else:
            result.append(item.strip())
    return tuple(result)


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
