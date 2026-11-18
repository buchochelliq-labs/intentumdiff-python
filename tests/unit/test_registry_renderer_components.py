"""#330/#329: core's validator accepts the published registry contract, renderer bundle included.

`tests/fixtures/registry/registry-f95ceaa.yaml` is intentumdiff-registry's registry.yaml at
f95ceaaf87008ec724fbd4d6866785f20f421be6, the manifest its vendored validator accepts.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from intentumdiff.plugins.registry_schema import load_schema, validate_registry

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "registry" / "registry-f95ceaa.yaml"
_CURRENT = yaml.safe_load(_FIXTURE.read_text(encoding="utf-8"))
_RENDERERS = ["html_renderer.wasm", "llm_renderer.wasm", "patch_renderer.wasm",
              "terminal_renderer.wasm"]


def test_the_current_published_registry_is_valid() -> None:
    assert "renderer_components" in _CURRENT
    assert validate_registry(_CURRENT) == []


def _bundle(**changes):
    manifest = copy.deepcopy(_CURRENT)
    manifest["renderer_components"].update(changes)
    return manifest


_PINS = "/renderer_components/wasm_checksums"


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (_bundle(ref="main"),
         "/renderer_components/ref: must be an immutable 40-character lowercase commit SHA"),
        (_bundle(ref="a" * 40 + "\n"), "/renderer_components/ref: must be an immutable"),
        (_bundle(repository="intentumdiff-python"),
         '/renderer_components/repository: must be "intentumdiff-core"'),
        (_bundle(artifact="x"), '/renderer_components/artifact: must be "renderer-components"'),
        (_bundle(extra=1), "/renderer_components/extra: unknown field"),
        (_bundle(wasm_checksums=[]), f"{_PINS}: must be an object, got array"),
        (_bundle(wasm_checksums={n: "0" * 64 for n in _RENDERERS[:3]}),
         f"{_PINS}/terminal_renderer.wasm: missing renderer pin"),
        (_bundle(wasm_checksums={**{n: "0" * 64 for n in _RENDERERS}, "x.wasm": "0" * 64}),
         f"{_PINS}/x.wasm: unexpected renderer component"),
        (_bundle(wasm_checksums={n: "0" * 63 for n in _RENDERERS}),
         f"{_PINS}/html_renderer.wasm: not a lowercase SHA-256 hex digest"),
    ],
)
def test_bad_renderer_bundles_are_rejected(manifest, expected) -> None:
    errors = validate_registry(manifest)
    assert any(error.startswith(expected) for error in errors), errors


def test_missing_bundle_fields_and_wrong_shape_are_rejected() -> None:
    manifest = copy.deepcopy(_CURRENT)
    del manifest["renderer_components"]["ref"]
    assert validate_registry(manifest) == [
        "/renderer_components/ref: must be an immutable 40-character lowercase commit SHA"
    ]
    manifest["renderer_components"] = ["not", "a", "mapping"]
    assert validate_registry(manifest) == ["/renderer_components: must be an object, got array"]


def test_the_bundle_stays_optional() -> None:
    manifest = copy.deepcopy(_CURRENT)
    del manifest["renderer_components"]
    assert validate_registry(manifest) == []


def test_schema_documents_the_same_bundle_contract() -> None:
    bundle = load_schema()["properties"]["renderer_components"]
    assert sorted(bundle["required"]) == ["artifact", "ref", "repository", "wasm_checksums"]
    assert bundle["additionalProperties"] is False
    assert bundle["properties"]["repository"] == {"const": "intentumdiff-core"}
    assert bundle["properties"]["artifact"] == {"const": "renderer-components"}
    assert bundle["properties"]["ref"]["pattern"] == "^[a-f0-9]{40}$"
    pins = bundle["properties"]["wasm_checksums"]
    assert sorted(pins["required"]) == _RENDERERS and pins["additionalProperties"] is False
    json.dumps(load_schema())  # still plain JSON
