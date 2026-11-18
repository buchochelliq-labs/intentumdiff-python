"""#309/#329: core's validator judges the registry manifest, but an invalid manifest does not
block loading. Entries core rejects (errors under ``/plugins/<name>``) are skipped, an unusable
top level loads nothing, and both emit a visible ``RegistryManifestWarning``. Strict tooling
keeps ``check_registry_manifest``, which raises with core's errors unchanged."""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import pytest
import yaml

from intentumdiff.cli import _build_parser
from intentumdiff.plugins import hub
from intentumdiff.plugins.exceptions import RegistryManifestError, RegistryManifestWarning
from intentumdiff.plugins.hub import parse_registry_manifest
from intentumdiff.plugins.registry_schema import check_registry_manifest, validate_registry

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "registry" / "registry-f95ceaa.yaml"
_SHA = "a" * 64


def _official(name: str) -> dict:
    return {"source": "pypi", "ref": "1.0.0", "wasm_checksums": {f"{name}.wasm": _SHA},
            "capabilities": ["parser"], "abi_target": "1.0.0"}


def _manifest(**plugins: object) -> str:
    return yaml.safe_dump({"version": 1, "plugins": plugins})


def _load_with_warning(content: str) -> tuple[dict, str]:
    with pytest.warns(RegistryManifestWarning) as caught:
        entries = parse_registry_manifest(content)
    assert len(caught) == 1
    return entries, str(caught[0].message)


@pytest.mark.parametrize(
    ("content", "where"),
    [
        ("[]", "/: must be an object, got array"),
        ("foo", "/: must be an object, got string"),
        ("version: 1\nplugins: []\n", "/plugins: must be an object of name -> entry, got array"),
        ("", "/: must be an object, got null"),
    ],
    ids=["list", "scalar", "plugins-list", "empty"],
)
def test_an_unusable_top_level_loads_nothing_with_a_warning(content, where) -> None:
    entries, message = _load_with_warning(content)
    assert entries == {}
    assert where in message
    assert message.endswith("no entries were loaded")


@pytest.mark.parametrize(
    ("content", "where"),
    [
        ("version: 1\nplugins:\n  foo: null\n", "/plugins/foo: must be an object, got null"),
        ("version: 1\nplugins:\n  foo: {source: svn}\n",
         '/plugins/foo/source: "svn" is not one of'),
        ("version: 1\nplugins:\n  foo: {color: red}\n", "/plugins/foo/color: unknown field"),
    ],
    ids=["null-entry", "unknown-source", "unknown-field"],
)
def test_a_rejected_entry_is_skipped_with_a_warning(content, where) -> None:
    entries, message = _load_with_warning(content)
    assert entries == {}
    assert where in message
    assert message.endswith("skipped entries: foo")


def test_one_bad_entry_among_good_ones_skips_only_the_bad_one() -> None:
    content = _manifest(alpha=_official("alpha"), bad={"source": "svn"}, zeta=_official("zeta"))
    entries, message = _load_with_warning(content)
    assert sorted(entries) == ["alpha", "zeta"]
    assert entries["alpha"].wasm_checksums == {"alpha.wasm": _SHA}
    assert '/plugins/bad/source: "svn" is not one of' in message
    assert message.endswith("skipped entries: bad")


def test_escaped_pointer_tokens_name_the_entry() -> None:
    # Core reports "a~b/c" as /plugins/a~0b~1c (RFC 6901); only that entry is skipped.
    entries, message = _load_with_warning(_manifest(**{"a~b/c": {}, "good": _official("good")}))
    assert list(entries) == ["good"]
    assert "/plugins/a~0b~1c" in message
    assert message.endswith("skipped entries: a~b/c")


def test_an_error_outside_plugins_still_loads_every_entry() -> None:
    entries, message = _load_with_warning(yaml.safe_dump({"plugins": {"dbt": _official("dbt")}}))
    assert list(entries) == ["dbt"]
    assert "/version: required" in message
    assert message.endswith("skipped entries: none")


def test_the_warning_is_also_logged(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=hub.__name__), \
            pytest.warns(RegistryManifestWarning):
        parse_registry_manifest("version: 1\nplugins:\n  foo: {color: red}\n")
    assert any("/plugins/foo/color: unknown field" in r.getMessage() for r in caplog.records)


def test_a_canned_core_rejection_surfaces_verbatim(monkeypatch) -> None:
    import intentumdiff.rust_core as rust_core

    canned = [{"path": "/plugins/x/ref", "message": "canned core message"}]
    monkeypatch.setattr(rust_core, "validate_registry_manifest", lambda _json: canned)
    entries, message = _load_with_warning("version: 1\nplugins: {x: {}, y: {}}\n")
    assert list(entries) == ["y"]  # core is the only judge: y has no Python-side rule
    assert "/plugins/x/ref: canned core message" in message


def test_a_valid_manifest_loads_without_a_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        entry = parse_registry_manifest(_manifest(dbt=_official("dbt")))["dbt"]
        assert parse_registry_manifest("version: 1\nplugins: {}\n") == {}
    assert (entry.source, entry.ref, entry.wasm_checksums) == ("pypi", "1.0.0", {"dbt.wasm": _SHA})


def test_non_yaml_and_non_json_manifests_still_raise_a_typed_error() -> None:
    # Unchanged: the only caller, `plugins add`, reports these and proceeds unverified.
    with pytest.raises(RegistryManifestError, match="not YAML"):
        parse_registry_manifest("version: [1\n")
    with pytest.raises(RegistryManifestError, match="not JSON-representable"):
        parse_registry_manifest("version: 1\nplugins: {}\nwhen: 2026-01-01\n")


def test_strict_check_still_raises_with_cores_errors_unchanged() -> None:
    with pytest.raises(RegistryManifestError, match="invalid plugin registry manifest") as caught:
        check_registry_manifest(yaml.safe_load("version: 1\nplugins:\n  foo: {color: red}\n"))
    assert caught.value.errors[0] == {"path": "/plugins/foo/color", "message": "unknown field"}
    assert all(set(error) == {"path", "message"} for error in caught.value.errors)
    assert isinstance(caught.value, RuntimeError)


def test_loader_and_ci_validator_accept_the_same_published_manifest() -> None:
    content = _FIXTURE.read_text(encoding="utf-8")
    assert validate_registry(yaml.safe_load(content)) == []
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        entries = parse_registry_manifest(content)
    assert len(entries) == len(yaml.safe_load(content)["plugins"])


def test_plugins_add_shows_the_warning_once_on_stderr(monkeypatch, capsys) -> None:
    content = _manifest(bad={"source": "svn"}, good=_official("good"))
    monkeypatch.setattr(hub, "fetch_official_registry",
                        lambda **_kw: parse_registry_manifest(content))
    monkeypatch.setattr(hub, "pre_install_security_check", lambda *_a, **_kw: (["stop"], []))
    args = _build_parser().parse_args(["plugins", "add", "good", "--no-save"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the CLI shows it whatever the warning filters say
        with pytest.raises(SystemExit):
            args.func(args)
    captured = capsys.readouterr()
    err = " ".join(captured.err.split())
    assert err.count("Registry warning:") == 1
    assert '/plugins/bad/source: "svn" is not one of ["git", "pypi"]' in err
    assert "skipped entries: bad" in err
    assert "Found 'good' in official registry" in captured.out
