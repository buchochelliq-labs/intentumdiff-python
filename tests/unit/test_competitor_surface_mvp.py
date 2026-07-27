from __future__ import annotations

import hashlib
import hmac
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from intentdiff.github_app import (
    build_pull_request_check_response,
    build_check_run_payload,
    create_app,
    parse_gist_url,
    parse_pull_request_event,
    parse_pull_request_url,
    verify_webhook_signature,
)
from intentdiff.plugins.registry import PluginRegistry

pytestmark = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "apps" / "review-shell").exists(),
    reason="monorepo MVP artifact tree not present (#82 split python repo)",
)



def test_github_app_signature_url_event_and_check_payload_contracts() -> None:
    body = b'{"repository":{"full_name":"owner/repo"},"pull_request":{"number":7,"head":{"sha":"abc123"}}}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature("secret", body, signature)
    assert not verify_webhook_signature("secret", body, "sha256=bad")

    pr = parse_pull_request_url("https://github.com/owner/repo/pull/7")
    assert (pr.owner, pr.repo, pr.number) == ("owner", "repo", 7)

    event_pr = parse_pull_request_event(json.loads(body))
    assert event_pr == pr

    with pytest.raises(ValueError):
        parse_pull_request_event({"repository": {"full_name": "/repo"}, "pull_request": {"number": 7}})
    with pytest.raises(ValueError):
        parse_pull_request_event({"repository": {"full_name": "owner/repo"}, "pull_request": {"number": 0}})

    payload = build_check_run_payload(
        name="IntentDiff",
        head_sha="abc123",
        summary_markdown="summary",
        passed=False,
        details_url="https://example.invalid/review",
    )
    assert payload["status"] == "completed"
    assert payload["conclusion"] == "failure"
    assert payload["details_url"] == "https://example.invalid/review"

    response = build_pull_request_check_response(
        json.loads(body),
        details_url="https://example.invalid/review",
    )
    assert response["ok"] is True
    assert response["owner"] == "owner"
    assert response["repo"] == "repo"
    assert response["number"] == 7
    assert response["check_run"] == {
        "name": "IntentDiff",
        "head_sha": "abc123",
        "status": "completed",
        "conclusion": "success",
        "output": {
            "title": "IntentDiff semantic review",
            "summary": (
                "# IntentDiff GitHub App Review\n\n"
                "Status: **passed**\n\n"
                "| Metric | Count |\n"
                "|---|---:|\n"
                "| Files checked | 0 |\n"
                "| Semantic changes | 0 |\n"
                "| Guardrail violations | 0 |\n"
            ),
        },
        "details_url": "https://example.invalid/review",
    }
    assert "IntentDiff GitHub App Review" in response["artifacts"]["html_report"]
    assert response["review"] == {
        "checked_files": 0,
        "semantic_changes": 0,
        "guardrail_violations": 0,
        "passed": True,
    }


def test_github_app_webhook_asgi_contract_validates_signature() -> None:
    fastapi_testclient = pytest.importorskip("fastapi.testclient")

    app = create_app()
    app.state.webhook_secret = "secret"
    client = fastapi_testclient.TestClient(app)
    body = (
        b'{"repository":{"full_name":"owner/repo"},'
        b'"pull_request":{"number":7,"head":{"sha":"abc123"}},'
        b'"intentdiff_review":{"checked_files":2,"semantic_changes":1,"guardrail_violations":1,"passed":false}}'
    )
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    response = client.post(
        "/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["event"] == "pull_request"
    assert payload["owner"] == "owner"
    assert payload["repo"] == "repo"
    assert payload["number"] == 7
    assert payload["check_run"]["head_sha"] == "abc123"
    assert payload["check_run"]["conclusion"] == "failure"
    assert "Semantic changes | 1" in payload["check_run"]["output"]["summary"]
    assert "IntentDiff GitHub App Review" in payload["artifacts"]["html_report"]
    assert payload["review"] == {
        "checked_files": 2,
        "semantic_changes": 1,
        "guardrail_violations": 1,
        "passed": False,
    }

    rejected = client.post(
        "/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=bad",
            "Content-Type": "application/json",
        },
    )
    assert rejected.status_code == 401


def test_gist_url_parser_accepts_only_github_gist_urls() -> None:
    gist = parse_gist_url("https://gist.github.com/owner/abcdef123456")

    assert gist.gist_id == "abcdef123456"

    with pytest.raises(ValueError):
        parse_gist_url("https://example.com/owner/abcdef123456")


def test_requested_parser_depth_languages_resolve_as_named_languages() -> None:
    registry = PluginRegistry()
    cases = [
        ("graphql", "schema.graphql", "graphql_parser.wasm"),
        ("ocaml", "main.ml", "ocaml_parser.wasm"),
        ("reasonml", "component.re", "reasonml_parser.wasm"),
        ("latex", "paper.tex", "latex_parser.wasm"),
        ("asciidoc", "README.adoc", "asciidoc_parser.wasm"),
        ("po", "messages.po", "po_parser.wasm"),
    ]
    cargo_workspace = (REPO_ROOT / "Cargo.toml").read_text(encoding="utf8")
    build_script = (REPO_ROOT / "build.py").read_text(encoding="utf8")

    for language, filename, wasm_name in cases:
        crate_dir = REPO_ROOT / "crates" / "parsers" / f"{language}-parser"
        assert crate_dir.exists(), language
        assert (crate_dir / "src" / "lib.rs").exists(), language
        assert f'"crates/parsers/{language}-parser"' in cargo_workspace
        assert wasm_name in build_script
        entries = registry._candidate_entries(filename, language_hint=language)  # noqa: SLF001
        assert entries, language
        assert any(language in entry.language_guesses for entry in entries)
        assert any(language in entry.entry_names for entry in entries)
        assert language != "generic"


def test_electron_review_shell_and_visual_studio_surface_are_repo_mvp_artifacts() -> None:
    assert (REPO_ROOT / "apps" / "review-shell" / "package.json").exists()
    assert (REPO_ROOT / "apps" / "review-shell" / "src" / "reviewArtifact.ts").exists()

    manifest_path = REPO_ROOT / "plugins" / "visualstudio" / "source.extension.vsixmanifest"
    project_path = REPO_ROOT / "plugins" / "visualstudio" / "IntentDiff.VisualStudio.csproj"
    pkgdef_path = REPO_ROOT / "plugins" / "visualstudio" / "IntentDiff.VisualStudio.pkgdef"
    manifest = ET.parse(manifest_path)
    ns = {"vsix": "http://schemas.microsoft.com/developer/vsx-schema/2011"}
    identity = manifest.find(".//vsix:Identity", ns)
    assert identity is not None
    assert identity.attrib["Id"] == "IntentDiff.VisualStudio2022"
    assert identity.attrib["Publisher"] == "BuchochelliqLabs"
    targets = {
        target.attrib["Id"]
        for target in manifest.findall(".//vsix:InstallationTarget", ns)
    }
    assert {
        "Microsoft.VisualStudio.Community",
        "Microsoft.VisualStudio.Pro",
        "Microsoft.VisualStudio.Enterprise",
    } <= targets
    assets = {
        asset.attrib["Path"]
        for asset in manifest.findall(".//vsix:Asset", ns)
    }
    assert "IntentDiff.VisualStudio.pkgdef" in assets

    assert project_path.exists()
    project_source = project_path.read_text(encoding="utf-8")
    assert "<TargetFramework>net472</TargetFramework>" in project_source
    assert "<AssemblyName>IntentDiff.VisualStudio</AssemblyName>" in project_source
    assert "<EnableDefaultCompileItems>false</EnableDefaultCompileItems>" in project_source
    assert 'Include="src\\IntentDiffCommandService.cs"' in project_source
    assert 'Include="source.extension.vsixmanifest"' in project_source
    assert 'Include="IntentDiff.VisualStudio.pkgdef"' in project_source

    pkgdef_source = pkgdef_path.read_text(encoding="utf-8")
    assert "IntentDiff.VisualStudio.IntentDiffPackage" in pkgdef_source
    assert "IntentDiffReviewCommand" in pkgdef_source
    assert "Open IntentDiff Review" in pkgdef_source

    command_service = REPO_ROOT / "plugins" / "visualstudio" / "src" / "IntentDiffCommandService.cs"
    assert command_service.exists()
    command_source = command_service.read_text(encoding="utf-8")
    assert 'FileName = "intentdiff"' in command_source
    assert 'Arguments = "serve --host 127.0.0.1 --port 8765"' in command_source
    assert 'startInfo.Environment["INTENTDIFF_REVIEW_ARTIFACT"] = outputPath' in command_source
    assert "Path.IsPathRooted(repositoryPath) == false" in command_source
    assert "Path.IsPathRooted(outputPath) == false" in command_source


def test_no_jetbrains_or_intellij_surface_added() -> None:
    forbidden = {"jetbrains", "intellij"}
    surface_roots = [REPO_ROOT / "plugins", REPO_ROOT / "apps"]
    paths = {
        str(path.relative_to(REPO_ROOT)).lower()
        for root in surface_roots
        for path in root.rglob("*")
        if "node_modules" not in path.parts
    }

    assert not any(any(token in path for token in forbidden) for path in paths)
