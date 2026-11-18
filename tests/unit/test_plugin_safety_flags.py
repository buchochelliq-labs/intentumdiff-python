"""#314: every plugin safety option parses, reaches its handler and has its documented effect.

Installation is stubbed; no package installer runs.
"""

from __future__ import annotations

import pytest

from intentumdiff.cli import _build_parser
from intentumdiff.plugins import hub


def _parse(*argv: str):
    return _build_parser().parse_args(list(argv))


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INTENTUMDIFF_ALLOW_SKIP_VERIFY", raising=False)
    calls: dict[str, list] = {"pip": [], "registry": []}
    monkeypatch.setattr(hub, "pip_install", lambda *a, **_kw: calls["pip"].append(a) or 0)

    def registry(*, strict=False, **_kw):
        calls["registry"].append(strict)
        return {}

    monkeypatch.setattr(hub, "fetch_official_registry", registry)
    (tmp_path / "intentumdiff_plugins.yaml").write_text(
        "version: 1\nplugins:\n  - name: intentumdiff-x\n    source: pypi\n    ref: 1.0.0\n",
        encoding="utf-8",
    )
    return calls


@pytest.mark.parametrize("command", [("plugins", "add", "x"), ("plugins", "install")])
def test_skip_verify_without_confirmation_refuses_before_install(stubbed, command) -> None:
    args = _parse(*command, "--skip-verify", *(("--source", "pypi") if "add" in command else ()))
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1
    assert stubbed["pip"] == []


@pytest.mark.parametrize("command", [("plugins", "add", "x"), ("plugins", "install")])
def test_the_advertised_yes_confirmation_parses_and_proceeds(stubbed, command) -> None:
    # The refusal message tells users to add --yes; that invocation must exist and work.
    extra = ("--source", "pypi", "--no-save") if "add" in command else ()
    args = _parse(*command, "--skip-verify", "--yes", *extra)
    assert args.yes is True
    args.func(args)
    assert len(stubbed["pip"]) == 1


def test_yes_alone_is_not_a_safety_bypass(stubbed) -> None:
    args = _parse("plugins", "add", "x", "--yes", "--source", "pypi", "--no-save")
    assert (args.yes, args.skip_verify) == (True, False)


def test_add_strict_registry_reaches_the_registry_fetch(stubbed) -> None:
    args = _parse("plugins", "add", "x", "--strict-registry", "--source", "pypi", "--no-save")
    try:
        args.func(args)
    except (SystemExit, RuntimeError, ValueError):
        pass  # the empty stub registry may refuse the install; only the flag's effect matters
    assert stubbed["registry"] == [True]


def test_install_rejects_strict_registry_it_cannot_honour(capsys) -> None:
    # `plugins install` installs manifest pins and never consults the registry, so accepting
    # --strict-registry there was a safety assertion with no effect: it is a usage error now.
    with pytest.raises(SystemExit) as exc_info:
        _parse("plugins", "install", "--strict-registry")
    assert exc_info.value.code == 2
    assert "--strict-registry" in capsys.readouterr().err
