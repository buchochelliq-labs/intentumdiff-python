"""The published registry schema + its dependency-free validator (#95)."""

from __future__ import annotations

import json
import re as _re
from pathlib import Path

from intentumdiff.plugins.hub import RegistryEntry, parse_registry_manifest
from intentumdiff.plugins.registry_schema import (
    HOST_CONTRACT_VERSION,
    abi_compatible,
    load_schema,
    render_catalog_markdown,
    validate_registry,
)
from intentumdiff.plugins.registry_schema import (
    main as _registry_cli,
)

_SHA = "a" * 64


def _valid_registry() -> dict:
    return {
        "version": 1,
        "plugins": {
            "dbt": {
                "source": "pypi",
                "ref": "0.3.1",
                "description": "dbt SQL + schema semantics",
                "trust_tier": "official",
                "wasm_checksums": {"dbt_sql_parser.wasm": _SHA},
                "capabilities": ["intentdiff:plugin/parser"],
                "abi_target": "1.0.0",
                "provenance_manifest_ref": "wasm_provenance.json",
            },
            "terraform": {
                "source": "git",
                "ref": "v0.2.0",
                "wasm_checksums": {"terraform_parser.wasm": _SHA},
                "dep_hashes": {"intentumdiff-terraform==0.2.0": f"sha256:{_SHA}"},
                "allowed_dependencies": ["some-reviewed-dep"],
            },
            "community-thing": {
                "source": "git",
                "repo": "https://github.com/someone/intentumdiff-community-thing",
                "trust_tier": "community",
            },
        },
    }


def test_schema_file_is_valid_json_schema_with_the_95_fields() -> None:
    schema = load_schema()
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["required"] == ["version", "plugins"]
    entry_props = schema["$defs"]["entry"]["properties"]
    # The known RegistryEntry fields plus the #95/#94/#89 additions are all documented.
    for field in (
        "source",
        "ref",
        "wasm_checksums",
        "dep_hashes",
        "trust_tier",
        "capabilities",
        "abi_target",
        "provenance_manifest_ref",
    ):
        assert field in entry_props, field
    assert schema["$defs"]["entry"]["additionalProperties"] is False
    assert set(entry_props["trust_tier"]["enum"]) == {"official", "community"}


def test_valid_registry_passes() -> None:
    assert validate_registry(_valid_registry()) == []


def test_missing_version_and_plugins_are_reported() -> None:
    errors = validate_registry({})
    assert any("version" in e for e in errors)
    assert any("plugins" in e for e in errors)


def test_unknown_top_level_and_entry_fields_are_rejected() -> None:
    reg = _valid_registry()
    reg["extra_top"] = 1
    reg["plugins"]["dbt"]["typo_field"] = "x"
    errors = validate_registry(reg)
    assert any("unknown top-level" in e for e in errors)
    assert any("unknown field" in e and "typo_field" in e for e in errors)


def test_bad_source_and_trust_tier_enums_are_rejected() -> None:
    reg = _valid_registry()
    reg["plugins"]["dbt"]["source"] = "svn"
    reg["plugins"]["terraform"]["trust_tier"] = "gold"
    errors = validate_registry(reg)
    assert any(".source" in e and "svn" in e for e in errors)
    assert any(".trust_tier" in e and "gold" in e for e in errors)


def test_bad_checksum_and_dep_hash_shapes_are_rejected() -> None:
    reg = _valid_registry()
    reg["plugins"]["dbt"]["wasm_checksums"]["dbt_sql_parser.wasm"] = "NOTAHASH"
    reg["plugins"]["dbt"]["wasm_checksums"]["not-a-wasm.txt"] = _SHA
    reg["plugins"]["terraform"]["dep_hashes"]["bad key"] = f"sha256:{_SHA}"
    reg["plugins"]["terraform"]["dep_hashes"]["intentumdiff-terraform==0.2.0"] = "md5:xyz"
    errors = validate_registry(reg)
    assert any("not a lowercase SHA-256" in e for e in errors)
    assert any("not a .wasm filename" in e for e in errors)
    assert any("package==version" in e for e in errors)
    assert any("sha256:<hex>" in e for e in errors)


def test_invalid_plugin_name_is_rejected() -> None:
    reg = _valid_registry()
    reg["plugins"]["../../etc/passwd"] = {"source": "git"}
    errors = validate_registry(reg)
    assert any("not a package-safe plugin name" in e for e in errors)


def test_schema_ships_in_the_package() -> None:
    # The schema is package data (like invariances/rules.schema.json), so downstream
    # consumers and the vetting CI can load it from the installed wheel.
    plugins_dir = Path(__file__).resolve().parents[2] / "src" / "intentumdiff" / "plugins"
    pkg_schema = plugins_dir / "registry.schema.json"
    assert pkg_schema.is_file()
    assert json.loads(pkg_schema.read_text(encoding="utf-8"))["title"].startswith("IntentumDiff")


# --- CLI (the #95 vetting entry point) ----------------------------------------------------


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    import yaml

    p = tmp_path / "registry.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_cli_accepts_a_valid_registry(tmp_path: Path, capsys) -> None:
    path = _write_yaml(tmp_path, _valid_registry())
    assert _registry_cli([str(path)]) == 0
    assert "is valid" in capsys.readouterr().out


def test_cli_rejects_an_invalid_registry(tmp_path: Path, capsys) -> None:
    bad = _valid_registry()
    bad["plugins"]["dbt"]["source"] = "svn"
    path = _write_yaml(tmp_path, bad)
    assert _registry_cli([str(path)]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out and "svn" in out


def test_cli_reports_unreadable_manifest(tmp_path: Path, capsys) -> None:
    assert _registry_cli([str(tmp_path / "does-not-exist.yaml")]) == 2
    assert "could not read" in capsys.readouterr().out


# --- #94 ABI-version gating -----------------------------------------------------------------


def test_host_contract_version_matches_the_wit() -> None:
    """Drift guard: HOST_CONTRACT_VERSION must equal `package intentdiff:plugin@X` in the WIT,
    so a contract bump forces a conscious host bump (#94)."""
    wit = (
        Path(__file__).resolve().parents[2]
        / "src" / "intentumdiff" / "plugins" / "wit" / "plugin.wit"
    ).read_text(encoding="utf-8")
    match = _re.search(r"package\s+intentdiff:plugin@([0-9]+\.[0-9]+\.[0-9]+)", wit)
    assert match is not None, "could not find the plugin-contract package version in the WIT"
    assert match.group(1) == HOST_CONTRACT_VERSION


def test_abi_compatible_semver_rules() -> None:
    # Same major, host minor >= target minor -> compatible.
    assert abi_compatible("1.0.0", "1.0.0")
    assert abi_compatible("1.0.5", "1.0.0")  # patch is interface-irrelevant
    assert abi_compatible("1.1.0", "1.3.0")  # host newer minor serves older plugin
    # Host older than the plugin's minor -> the host lacks features -> incompatible.
    assert not abi_compatible("1.4.0", "1.2.0")
    # Different major -> breaking -> incompatible either direction.
    assert not abi_compatible("2.0.0", "1.0.0")
    assert not abi_compatible("0.9.0", "1.0.0")
    # Malformed -> fail closed.
    assert not abi_compatible("", "1.0.0")
    assert not abi_compatible("1.0", "1.0.0")
    assert not abi_compatible("latest", "1.0.0")


def test_registry_entry_abi_incompatibility() -> None:
    # Unspecified abi_target -> compatible (not a breaking change for older entries).
    assert RegistryEntry(name="x").abi_incompatibility() is None
    # Matching the host -> compatible.
    assert RegistryEntry(name="x", abi_target=HOST_CONTRACT_VERSION).abi_incompatibility() is None
    # A future-major plugin -> refused with a clear reason.
    reason = RegistryEntry(name="future-plugin", abi_target="2.0.0").abi_incompatibility()
    assert reason is not None
    assert "2.0.0" in reason and "future-plugin" in reason
    # Explicit host override (for testing a host at a higher minor).
    assert RegistryEntry(name="x", abi_target="1.1.0").abi_incompatibility("1.5.0") is None


def test_fetch_official_registry_parses_abi_target() -> None:
    import yaml

    content = yaml.safe_dump(
        {
            "version": 1,
            "plugins": {
                "dbt": {"source": "pypi", "ref": "1.0.0", "abi_target": "1.0.0"},
                "legacy": {"source": "git", "ref": "main"},  # no abi_target
            },
        }
    )
    entries = parse_registry_manifest(content)
    assert entries["dbt"].abi_target == "1.0.0"
    assert entries["legacy"].abi_target == ""


# --- #95 trust tiers consumed by hub.py -----------------------------------------------------


def test_effective_trust_tier_official_requires_org_and_checksums() -> None:
    # Declared official + org source + checksums -> official.
    official = RegistryEntry(name="dbt", wasm_checksums={"a.wasm": _SHA})
    assert official.effective_trust_tier() == "official"
    assert official.trust_warning() is None


def test_effective_trust_tier_demotes_unverified_to_community() -> None:
    # No checksums -> unverified -> community (even if not explicitly declared).
    no_checksums = RegistryEntry(name="x")
    assert no_checksums.effective_trust_tier() == "community"
    assert "unverified" in (no_checksums.trust_warning() or "")
    # Custom repo -> community.
    custom = RegistryEntry(
        name="y", repo="https://github.com/me/x", wasm_checksums={"a.wasm": _SHA}
    )
    assert custom.effective_trust_tier() == "community"
    # Explicit community tier -> community even with checksums.
    declared = RegistryEntry(name="z", trust_tier="community", wasm_checksums={"a.wasm": _SHA})
    assert declared.effective_trust_tier() == "community"
    assert declared.trust_warning() is not None


def test_fetch_official_registry_parses_trust_tier() -> None:
    import yaml

    content = yaml.safe_dump(
        {
            "version": 1,
            "plugins": {
                "official-one": {"source": "pypi", "wasm_checksums": {"a.wasm": _SHA}},
                "community-one": {"source": "git", "trust_tier": "community"},
            },
        }
    )
    entries = parse_registry_manifest(content)
    assert entries["official-one"].trust_tier == "official"  # default when unspecified
    assert entries["official-one"].effective_trust_tier() == "official"
    assert entries["community-one"].trust_tier == "community"
    assert entries["community-one"].effective_trust_tier() == "community"


# --- #95 discovery catalog ------------------------------------------------------------------



def test_render_catalog_groups_official_and_community() -> None:
    md = render_catalog_markdown(_valid_registry())
    assert md.startswith("# IntentumDiff plugin catalog")
    # dbt has org source + checksums -> official; terraform has checksums + git (no repo) ->
    # official; community-thing has a custom repo -> community.
    assert "## Official (2)" in md
    assert "## Community (1)" in md
    # Per-plugin fields render.
    assert "### dbt" in md
    assert "dbt SQL + schema semantics" in md
    assert "**ABI target**: `1.0.0`" in md
    assert "intentumdiff plugins add intentumdiff-dbt==0.3.1" in md
    assert "### community-thing" in md


def test_render_catalog_handles_empty_and_missing_fields() -> None:
    md = render_catalog_markdown({"version": 1, "plugins": {}})
    assert "## Official (0)" in md and "_None._" in md
    # An entry with only a name still renders an install line.
    md2 = render_catalog_markdown({"version": 1, "plugins": {"bare": {}}})
    assert "### bare" in md2
    assert "intentumdiff plugins add bare" in md2


def test_cli_catalog_flag_emits_markdown(tmp_path: Path, capsys) -> None:
    path = _write_yaml(tmp_path, _valid_registry())
    assert _registry_cli([str(path), "--catalog"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# IntentumDiff plugin catalog")
    assert "## Official" in out


def test_cli_catalog_still_validates_first(tmp_path: Path, capsys) -> None:
    bad = _valid_registry()
    bad["plugins"]["dbt"]["source"] = "svn"
    path = _write_yaml(tmp_path, bad)
    # --catalog does not bypass validation.
    assert _registry_cli([str(path), "--catalog"]) == 1
    assert "INVALID" in capsys.readouterr().out
