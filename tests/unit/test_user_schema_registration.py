"""User-registerable schema/diff profiles (issue #63, external-config path).

Covers the three registration modes — profile descriptor, local JSON Schema
file, and provider entry — plus the fail-closed validation, the built-in
collision guard, and the privacy assertion (registration is offline: a local
schema is never fetched). No test performs network I/O.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intentumdiff import SemanticDiffer
from intentumdiff.analysis.schema_resolver import resolve_schema
from intentumdiff.analysis.user_schemas import (
    load_user_schema_profiles,
    match_user_profile,
)

_ACME_DESCRIPTOR = """
language_id: acme-service-config
match:
  filename_patterns: ["service.acme.json"]
  root_markers: ["acmeApiVersion"]
keyed_arrays:
  /routes: [route]
important_paths: ["/routes/*/handler"]
"""


def _acme_doc(routes: list[dict[str, str]]) -> str:
    return json.dumps({"acmeApiVersion": 1, "routes": routes}, indent=1)


_OLD = _acme_doc(
    [
        {"route": "checkout", "handler": "pay_v1"},
        {"route": "search", "handler": "search_v1"},
    ]
)
# Reordered AND one handler edited: keyed identity must absorb the reorder.
_NEW = _acme_doc(
    [
        {"route": "search", "handler": "search_v1"},
        {"route": "checkout", "handler": "pay_v2"},
    ]
)


def _register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    registry = tmp_path / "schemas"
    registry.mkdir(exist_ok=True)
    descriptor = registry / "acme.yml"
    descriptor.write_text(text, encoding="utf-8")
    monkeypatch.setenv("INTENTUMDIFF_USER_SCHEMA_DIR", str(registry))
    monkeypatch.setenv("INTENTUMDIFF_SCHEMA_FETCH", "off")
    return descriptor


def _raising_fetcher(url: str, headers: dict[str, str]):
    raise AssertionError(f"network fetch attempted: {url}")


def test_descriptor_registration_changes_matching_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mode 1 round-trip: the registered identity key compacts a keyed-array
    reorder + edit to exactly one MODIFICATION (without it: reorder churn)."""
    _register(tmp_path, monkeypatch, _ACME_DESCRIPTOR)

    diff = SemanticDiffer().diff_strings(
        _OLD, _NEW, filename="service.acme.json", language_hint="json"
    )

    schema = diff.metadata.get("schema")
    assert isinstance(schema, dict)
    assert schema["provider_id"] == "user:acme-service-config"
    assert schema["status"] == "user-profile"
    assert schema["identity_fields"] == ["route"]
    assert [change.change_type.name for change in diff.changes] == ["MODIFICATION"]


def test_without_registration_the_same_edit_is_reorder_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behavioral contrast that proves registration changed matching."""
    monkeypatch.setenv("INTENTUMDIFF_USER_SCHEMAS", "off")
    monkeypatch.setenv("INTENTUMDIFF_SCHEMA_FETCH", "off")

    diff = SemanticDiffer().diff_strings(
        _OLD, _NEW, filename="service.acme.json", language_hint="json"
    )

    assert diff.metadata.get("schema") is None
    assert len(diff.changes) > 1  # identity churn: modification + delete/add pair


def test_local_schema_file_derives_identity_without_network(tmp_path: Path) -> None:
    """Mode 2: a local JSON Schema yields identity hints via the same
    extraction the resolver uses for providers — and is never fetched."""
    registry = tmp_path / "schemas"
    registry.mkdir()
    (registry / "acme.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "routes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (registry / "acme.yml").write_text(
        """
language_id: acme-routes
match:
  filename_patterns: ["routes.acme.json"]
schema: ./acme.schema.json
""",
        encoding="utf-8",
    )
    env = {
        "INTENTUMDIFF_USER_SCHEMA_DIR": str(registry),
        "INTENTUMDIFF_SCHEMA_FETCH": "on",
    }

    resolution = resolve_schema(
        content='{"routes": []}',
        filename="routes.acme.json",
        language="json",
        env=env,
        cache_dir=tmp_path / "cache",
        fetcher=_raising_fetcher,
    )

    assert resolution.provider_id == "user:acme-routes"
    assert resolution.status == "user-profile"
    assert "name" in resolution.identity_fields
    assert resolution.schema is not None
    assert resolution.source_url == str((registry / "acme.schema.json").resolve())


def test_provider_entry_claims_declared_schema_offline(tmp_path: Path) -> None:
    """Mode 3: a registered schema_urls entry outranks the embedded fetch —
    the declared $schema resolves offline (the raising fetcher proves no
    fetch was attempted even with fetching enabled)."""
    registry = tmp_path / "schemas"
    registry.mkdir()
    (registry / "internal.yml").write_text(
        """
language_id: acme-internal
match:
  schema_urls: ["https://schemas.internal.example/service.json"]
identity_fields: [service_name]
""",
        encoding="utf-8",
    )
    env = {
        "INTENTUMDIFF_USER_SCHEMA_DIR": str(registry),
        "INTENTUMDIFF_SCHEMA_FETCH": "on",
    }
    content = json.dumps(
        {"$schema": "https://schemas.internal.example/service.json", "services": []}
    )

    resolution = resolve_schema(
        content=content,
        filename="anything.json",
        language="json",
        env=env,
        cache_dir=tmp_path / "cache",
        fetcher=_raising_fetcher,
    )

    assert resolution.provider_id == "user:acme-internal"
    assert resolution.status == "user-profile"
    assert resolution.identity_fields == {"service_name"}


def test_malformed_descriptor_fails_closed(tmp_path: Path) -> None:
    registry = tmp_path / "schemas"
    registry.mkdir()
    (registry / "broken.yml").write_text(
        "match:\n  filename_patterns: ['*.json']\nidentity_fields: [name]\n",
        encoding="utf-8",
    )
    env = {"INTENTUMDIFF_USER_SCHEMA_DIR": str(registry)}

    profiles, errors = load_user_schema_profiles(env)

    assert profiles == ()
    assert len(errors) == 1
    assert "language_id" in errors[0]


def test_builtin_collision_is_rejected(tmp_path: Path) -> None:
    registry = tmp_path / "schemas"
    registry.mkdir()
    (registry / "collide.yml").write_text(
        """
language_id: dbt:custom
match:
  filename_patterns: ["*.yml"]
identity_fields: [name]
""",
        encoding="utf-8",
    )
    env = {"INTENTUMDIFF_USER_SCHEMA_DIR": str(registry)}

    profiles, errors = load_user_schema_profiles(env)

    assert profiles == ()
    assert any("collides with the built-in provider root 'dbt'" in error for error in errors)


def test_invalid_document_rejects_the_whole_file(tmp_path: Path) -> None:
    """A file mixing one valid and one invalid descriptor applies neither."""
    registry = tmp_path / "schemas"
    registry.mkdir()
    (registry / "mixed.yml").write_text(
        """
language_id: acme-good
match:
  filename_patterns: ["good.json"]
identity_fields: [name]
---
language_id: acme-bad
match: {}
""",
        encoding="utf-8",
    )
    env = {"INTENTUMDIFF_USER_SCHEMA_DIR": str(registry)}

    profiles, errors = load_user_schema_profiles(env)

    assert profiles == ()
    assert errors


def test_declared_schema_claim_beats_filename_match(tmp_path: Path) -> None:
    registry = tmp_path / "schemas"
    registry.mkdir()
    (registry / "both.yml").write_text(
        """
language_id: acme-by-url
match:
  schema_urls: ["https://schemas.internal.example/one.json"]
identity_fields: [id]
---
language_id: acme-by-name
match:
  filename_patterns: ["one.json"]
identity_fields: [name]
""",
        encoding="utf-8",
    )
    env = {"INTENTUMDIFF_USER_SCHEMA_DIR": str(registry)}
    profiles, errors = load_user_schema_profiles(env)
    assert errors == ()

    claimed = match_user_profile(
        profiles,
        filename="one.json",
        content='{"$schema": "https://schemas.internal.example/one.json"}',
        declared_url="https://schemas.internal.example/one.json",
    )
    by_name = match_user_profile(
        profiles,
        filename="one.json",
        content="{}",
        declared_url=None,
    )

    assert claimed is not None and claimed.language_id == "acme-by-url"
    assert by_name is not None and by_name.language_id == "acme-by-name"


# ── XML dialects (issue #86): the data-driven channel ─────────────────────────

_CATALOG_DESCRIPTOR = """
language_id: acme-catalog
match:
  filename_patterns: ["*.catalog.xml"]
  root_element: catalog
keyed_elements:
  book: [isbn]
"""

_BOOK = "<book><isbn>{i}</isbn><price>{p}</price></book>"


def _catalog(*books: str) -> str:
    return "<catalog>" + "".join(books) + "</catalog>\n"


_HOBBIT = _BOOK.format(i="978-0", p="10.99")
_HOBBIT_REPRICED = _BOOK.format(i="978-0", p="12.99")
_DUNE = _BOOK.format(i="978-1", p="9.99")


def test_registered_xml_dialect_compacts_reorder_and_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirror of the bundled-POM acceptance: a registered dialect keys <book>
    by <isbn>, so reorder + one price edit compacts to one MODIFICATION."""
    _register(tmp_path, monkeypatch, _CATALOG_DESCRIPTOR)

    diff = SemanticDiffer().diff_strings(
        _catalog(_HOBBIT, _DUNE),
        _catalog(_DUNE, _HOBBIT_REPRICED),
        filename="shop.catalog.xml",
        language_hint="xml",
    )

    assert [change.change_type.name for change in diff.changes] == ["MODIFICATION"]
    change = diff.changes[0]
    assert change.old_node is not None and change.old_node.label == "10.99"
    assert change.new_node is not None and change.new_node.label == "12.99"


def test_registered_xml_dialect_reorder_only_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path, monkeypatch, _CATALOG_DESCRIPTOR)

    diff = SemanticDiffer().diff_strings(
        _catalog(_HOBBIT, _DUNE),
        _catalog(_DUNE, _HOBBIT),
        filename="shop.catalog.xml",
        language_hint="xml",
    )

    assert diff.changes == []


def test_dialect_key_changes_matching_for_identity_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Arm-reachability pin: an ISBN (identity) edit produces a different
    matching shape with the dialect registered, because the coordinate key
    forbids pairing books across identities. (On small reorder fixtures the
    generic #57 tier happens to converge with the dialect result, so THIS is
    the discriminating contract, not reorder compaction.)"""
    from intentumdiff.rust_core import try_register_user_xml_dialects

    old = _catalog(_BOOK.format(i="978-0", p="10.99"))
    new = _catalog(_BOOK.format(i="978-9", p="10.99"))

    monkeypatch.setenv("INTENTUMDIFF_USER_SCHEMAS", "off")
    monkeypatch.setenv("INTENTUMDIFF_SCHEMA_FETCH", "off")
    try_register_user_xml_dialects([])  # clear the process-level registry
    unregistered = SemanticDiffer().diff_strings(
        old, new, filename="shop.catalog.xml", language_hint="xml"
    )

    monkeypatch.setenv("INTENTUMDIFF_USER_SCHEMAS", "1")
    _register(tmp_path, monkeypatch, _CATALOG_DESCRIPTOR)
    registered = SemanticDiffer().diff_strings(
        old, new, filename="shop.catalog.xml", language_hint="xml"
    )

    unregistered_shape = [change.change_type.name for change in unregistered.changes]
    registered_shape = [change.change_type.name for change in registered.changes]
    assert unregistered_shape == ["MODIFICATION"]
    assert registered_shape != unregistered_shape


def test_xml_dialect_without_engine_predicate_fails_closed(tmp_path: Path) -> None:
    """keyed_elements without match.namespace/root_element is rejected whole -
    filename matching alone cannot reach the Rust-side dialect predicate."""
    registry = tmp_path / "schemas"
    registry.mkdir()
    (registry / "bad.yml").write_text(
        """
language_id: acme-nopredicate
match:
  filename_patterns: ["*.x.xml"]
keyed_elements:
  item: [id]
""",
        encoding="utf-8",
    )
    env = {"INTENTUMDIFF_USER_SCHEMA_DIR": str(registry)}

    profiles, errors = load_user_schema_profiles(env)

    assert profiles == ()
    assert any("match.namespace" in error for error in errors)


def test_xml_dialect_attribute_keys_are_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """attr:NAME key fields mirror the MSBuild Include-attribute pattern."""
    _register(
        tmp_path,
        monkeypatch,
        """
language_id: acme-parts
match:
  root_element: parts
keyed_elements:
  part: ["attr:sku"]
""",
    )
    old = '<parts><part sku="A1"><qty>1</qty></part><part sku="B2"><qty>5</qty></part></parts>\n'
    new = '<parts><part sku="B2"><qty>5</qty></part><part sku="A1"><qty>2</qty></part></parts>\n'

    diff = SemanticDiffer().diff_strings(
        old, new, filename="inventory.xml", language_hint="xml"
    )

    assert [change.change_type.name for change in diff.changes] == ["MODIFICATION"]
