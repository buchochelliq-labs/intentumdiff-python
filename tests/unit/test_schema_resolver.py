from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from intentdiff import SemanticDiffer
from intentdiff.analysis.schema_resolver import (
    ADVISORY_ADF_NO_RAW_SCHEMA_URL,
    AZURE_PIPELINES_SCHEMA_URL,
    DATABRICKS_BUNDLE_SCHEMA_URL,
    DBT_SCHEMA_URLS,
    GITHUB_WORKFLOW_SCHEMA_URL,
    KUBERNETES_SCHEMA_URL,
    OPENAPI_30_SCHEMA_URL,
    OPENAPI_31_SCHEMA_URL,
    FetchResponse,
    discover_declared_schema,
    provider_schema_candidate,
    resolve_schema,
    schema_resolution_metadata,
)


def _env(**overrides: str) -> dict[str, str]:
    return {
        "INTENTDIFF_SCHEMA_FETCH": "auto",
        "INTENTDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS": "1",
        **overrides,
    }


def _schema(*fields: str) -> bytes:
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {field: {"type": "string"} for field in fields},
        }
    ).encode("utf-8")


def test_discovers_json_schema_property() -> None:
    assert discover_declared_schema('{"$schema":"https://example.test/schema.json"}', "json") == (
        "https://example.test/schema.json"
    )


def test_discovers_yaml_language_server_modeline() -> None:
    assert (
        discover_declared_schema(
            "# yaml-language-server: $schema=https://example.test/schema.json\nname: app\n",
            "yaml",
        )
        == "https://example.test/schema.json"
    )


def test_provider_selection_for_dbt_databricks_adf_and_terraform() -> None:
    assert (
        provider_schema_candidate("dbt_project.yml", "yaml").url == DBT_SCHEMA_URLS["dbt_project"]
    )
    assert (
        provider_schema_candidate("models/schema.yml", "yaml", "version: 2\nmodels: []").url
        == (DBT_SCHEMA_URLS["dbt_yml_files"])
    )
    assert (
        provider_schema_candidate("models/schema.yml", "dbt-yaml", "version: 2\nmodels: []").url
        == DBT_SCHEMA_URLS["dbt_yml_files"]
    )
    assert (
        provider_schema_candidate("dbt_project.yml", "dbt-config", "name: example").url
        == DBT_SCHEMA_URLS["dbt_project"]
    )
    databricks = provider_schema_candidate("databricks.yml", "yaml")
    assert databricks is not None
    assert databricks.url == DATABRICKS_BUNDLE_SCHEMA_URL
    assert databricks.command == ("databricks", "bundle", "schema")
    adf = provider_schema_candidate("pipeline.json", "adf")
    assert adf is not None
    assert adf.provider_id == "adf:no_raw_schema"
    assert adf.status == "no_raw_schema"
    assert adf.advisory_url == ADVISORY_ADF_NO_RAW_SCHEMA_URL
    assert provider_schema_candidate("main.tf", "terraform") is None
    assert provider_schema_candidate("macros/utils.jinja", "dbt-jinja") is None
    dbt_project = provider_schema_candidate("dbt_project.yml", "yaml")
    assert dbt_project is not None
    assert dbt_project.identity_fields >= {
        "name",
        "version",
        "models",
        "selectors",
        "sources",
        "tests",
    }


def test_provider_selection_for_openapi_kubernetes_and_ci_workflows() -> None:
    openapi = provider_schema_candidate(
        "docs/openapi.yaml",
        "yaml",
        "openapi: 3.1.0\ninfo:\n  title: API\n  version: 1.0\npaths: {}\n",
    )
    assert openapi is not None
    assert openapi.provider_id == "openapi:3.1"
    assert openapi.url == OPENAPI_31_SCHEMA_URL
    assert "operationId" in openapi.identity_fields

    swagger = provider_schema_candidate(
        "swagger.json",
        "json",
        '{"swagger":"2.0","info":{"title":"API","version":"1.0"},"paths":{}}',
    )
    assert swagger is not None
    assert swagger.provider_id == "openapi:3.0"
    assert swagger.url == OPENAPI_30_SCHEMA_URL

    kubernetes = provider_schema_candidate(
        "deploy/service.yaml",
        "yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec: {}\n",
    )
    assert kubernetes is not None
    assert kubernetes.provider_id == "kubernetes:manifest"
    assert kubernetes.url == KUBERNETES_SCHEMA_URL
    assert "name" in kubernetes.identity_fields

    workflow = provider_schema_candidate(
        ".github/workflows/ci.yml",
        "yaml",
        "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
    )
    assert workflow is not None
    assert workflow.provider_id == "github-actions:workflow"
    assert workflow.url == GITHUB_WORKFLOW_SCHEMA_URL
    assert {"id", "name", "uses"} <= workflow.identity_fields

    azure = provider_schema_candidate(
        "azure-pipelines.yml",
        "yaml",
        "trigger:\n- main\npool:\n  vmImage: ubuntu-latest\nsteps:\n- script: echo hi\n",
    )
    assert azure is not None
    assert azure.provider_id == "azure-pipelines:pipeline"
    assert azure.url == AZURE_PIPELINES_SCHEMA_URL
    assert {"job", "stage", "task", "displayName"} <= azure.identity_fields

    assert provider_schema_candidate("notes.yml", "yaml", "name: docs\nitems: []\n") is None


@pytest.mark.parametrize(
    ("filename", "content", "provider_id", "identity_field"),
    [
        (
            "docs/openapi.yaml",
            "openapi: 3.1.0\ninfo:\n  title: API\n  version: 1.0\npaths: {}\n",
            "openapi:3.1",
            "operationId",
        ),
        (
            "deploy/web.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec: {}\n",
            "kubernetes:manifest",
            "name",
        ),
        (
            ".github/workflows/ci.yml",
            "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
            "github-actions:workflow",
            "runs-on",
        ),
        (
            "azure-pipelines.yml",
            "trigger:\n- main\npool:\n  vmImage: ubuntu-latest\nsteps:\n- script: echo hi\n",
            "azure-pipelines:pipeline",
            "task",
        ),
        (
            "dbt_project.yml",
            "name: demo\nversion: '1.0'\nmodels: []\n",
            "dbt:dbt_project",
            "version",
        ),
    ],
)
def test_semantic_differ_reports_domain_schema_provider_without_fetch(
    filename: str,
    content: str,
    provider_id: str,
    identity_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTENTDIFF_SCHEMA_FETCH", "off")
    changed = content + "\nx-intentdiff-proof: true\n"

    diff = SemanticDiffer().diff_strings(
        content,
        changed,
        filename=filename,
        language_hint="yaml",
    )

    schema = diff.metadata.get("schema")
    assert isinstance(schema, dict)
    assert schema["provider_id"] == provider_id
    assert schema["status"] == "disabled"
    assert schema["detected"] is True
    assert identity_field in schema["identity_fields"]


def test_fetches_and_reuses_fresh_cache(tmp_path: Path) -> None:
    calls: list[dict[str, str]] = []

    def fetcher(url: str, headers: dict[str, str]) -> FetchResponse:
        calls.append(dict(headers))
        return FetchResponse(
            body=_schema("task_key"),
            final_url=url,
            etag='"v1"',
            last_modified="Tue, 26 May 2026 12:00:00 GMT",
        )

    content = '{"$schema":"https://example.test/schema.json","tasks":[]}'
    first = resolve_schema(
        content=content,
        filename="workflow.json",
        language="json",
        env=_env(),
        cache_dir=tmp_path,
        fetcher=fetcher,
        now=1000,
    )
    second = resolve_schema(
        content=content,
        filename="workflow.json",
        language="json",
        env=_env(),
        cache_dir=tmp_path,
        fetcher=fetcher,
        now=1001,
    )

    assert first.status == "fetched"
    assert second.status == "cache_hit"
    assert second.identity_fields == {"task_key"}
    assert len(calls) == 1


def test_stale_cache_uses_conditional_get_and_304(tmp_path: Path) -> None:
    content = '{"$schema":"https://example.test/schema.json"}'

    def first_fetcher(url: str, headers: dict[str, str]) -> FetchResponse:
        return FetchResponse(body=_schema("name"), final_url=url, etag='"v1"')

    resolve_schema(
        content=content,
        filename="data.json",
        language="json",
        env=_env(INTENTDIFF_SCHEMA_CACHE_TTL_SECONDS="1"),
        cache_dir=tmp_path,
        fetcher=first_fetcher,
        now=1000,
    )

    seen_headers: dict[str, str] = {}

    def second_fetcher(url: str, headers: dict[str, str]) -> FetchResponse:
        seen_headers.update(headers)
        return FetchResponse(body=b"", final_url=url, status=304)

    result = resolve_schema(
        content=content,
        filename="data.json",
        language="json",
        env=_env(INTENTDIFF_SCHEMA_CACHE_TTL_SECONDS="1"),
        cache_dir=tmp_path,
        fetcher=second_fetcher,
        now=1002,
    )

    assert seen_headers["If-None-Match"] == '"v1"'
    assert result.status == "not_modified"
    assert result.identity_fields == {"name"}


def test_failed_refresh_uses_stale_cache(tmp_path: Path) -> None:
    content = '{"$schema":"https://example.test/schema.json"}'
    resolve_schema(
        content=content,
        filename="data.json",
        language="json",
        env=_env(INTENTDIFF_SCHEMA_CACHE_TTL_SECONDS="1"),
        cache_dir=tmp_path,
        fetcher=lambda url, headers: FetchResponse(body=_schema("id"), final_url=url),
        now=1000,
    )

    def failing_fetcher(url: str, headers: dict[str, str]) -> FetchResponse:
        raise urllib.error.URLError("offline")

    result = resolve_schema(
        content=content,
        filename="data.json",
        language="json",
        env=_env(INTENTDIFF_SCHEMA_CACHE_TTL_SECONDS="1"),
        cache_dir=tmp_path,
        fetcher=failing_fetcher,
        now=1002,
    )

    assert result.status == "stale_cache"
    assert result.identity_fields == {"id"}


def test_cache_only_and_off_modes_suppress_fetch(tmp_path: Path) -> None:
    content = '{"$schema":"https://example.test/schema.json"}'

    def fail_if_called(url: str, headers: dict[str, str]) -> FetchResponse:
        raise AssertionError("fetcher should not be called")

    cache_only = resolve_schema(
        content=content,
        filename="data.json",
        language="json",
        env=_env(INTENTDIFF_SCHEMA_FETCH="cache-only"),
        cache_dir=tmp_path,
        fetcher=fail_if_called,
    )
    off = resolve_schema(
        content=content,
        filename="data.json",
        language="json",
        env=_env(INTENTDIFF_SCHEMA_FETCH="off"),
        cache_dir=tmp_path,
        fetcher=fail_if_called,
    )

    assert cache_only.status == "cache_miss"
    assert off.status == "disabled"
    assert off.provider_id == "embedded"


def test_schema_metadata_marks_detected_available_and_missing(
    tmp_path: Path,
) -> None:
    resolved = resolve_schema(
        content='{"$schema":"https://example.test/schema.json"}',
        filename="data.json",
        language="json",
        env=_env(INTENTDIFF_SCHEMA_FETCH="cache-only"),
        cache_dir=tmp_path,
    )
    metadata = schema_resolution_metadata(resolved)

    assert metadata == {
        "provider_id": "embedded",
        "status": "cache_miss",
        "source_url": "https://example.test/schema.json",
        "identity_fields": [],
        "detected": True,
        "available": False,
    }

    available = resolve_schema(
        content='{"$schema":"https://example.test/schema.json"}',
        filename="data.json",
        language="json",
        env=_env(),
        cache_dir=tmp_path,
        fetcher=lambda url, headers: FetchResponse(
            body=_schema("task_key"),
            final_url=url,
        ),
    )

    assert schema_resolution_metadata(available) == {
        "provider_id": "embedded",
        "status": "fetched",
        "source_url": "https://example.test/schema.json",
        "identity_fields": ["task_key"],
        "detected": True,
        "available": True,
    }


def test_schema_metadata_marks_adf_as_detected_without_raw_schema() -> None:
    result = resolve_schema(
        content='{"name":"pipeline"}',
        filename="pipeline.json",
        language="json",
        env=_env(),
    )

    assert schema_resolution_metadata(result) == {
        "provider_id": "adf:no_raw_schema",
        "status": "no_raw_schema",
        "source_url": ADVISORY_ADF_NO_RAW_SCHEMA_URL,
        "identity_fields": [],
        "detected": True,
        "available": False,
    }


def test_diff_metadata_includes_detected_schema_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("INTENTDIFF_SCHEMA_FETCH", "cache-only")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    diff = SemanticDiffer().diff_strings(
        '{"$schema":"https://example.test/schema.json","name":"old"}',
        '{"$schema":"https://example.test/schema.json","name":"new"}',
        filename="data.json",
        language_hint="json",
    )

    assert diff.metadata["schema"] == {
        "provider_id": "embedded",
        "status": "cache_miss",
        "source_url": "https://example.test/schema.json",
        "identity_fields": [],
        "detected": True,
        "available": False,
    }


def test_diff_metadata_omits_schema_for_plain_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("INTENTDIFF_SCHEMA_FETCH", "cache-only")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    diff = SemanticDiffer().diff_strings(
        '{"name":"old"}',
        '{"name":"new"}',
        filename="data.json",
        language_hint="json",
    )

    assert "schema" not in diff.metadata


def test_default_fetch_mode_is_cache_only(tmp_path: Path) -> None:
    content = '{"$schema":"https://example.test/schema.json"}'

    def fail_if_called(url: str, headers: dict[str, str]) -> FetchResponse:
        raise AssertionError("fetcher should not be called")

    result = resolve_schema(
        content=content,
        filename="data.json",
        language="json",
        env={"INTENTDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS": "1"},
        cache_dir=tmp_path,
        fetcher=fail_if_called,
    )

    assert result.status == "cache_miss"


def test_rejects_non_https_schema_url(tmp_path: Path) -> None:
    result = resolve_schema(
        content='{"$schema":"http://example.test/schema.json"}',
        filename="data.json",
        language="json",
        env=_env(),
        cache_dir=tmp_path,
        fetcher=lambda url, headers: FetchResponse(body=_schema("id"), final_url=url),
    )

    assert result.status == "error"
    assert "HTTPS" in (result.error or "")


def test_rejects_private_schema_hosts_by_default(tmp_path: Path) -> None:
    result = resolve_schema(
        content='{"$schema":"https://127.0.0.1/schema.json"}',
        filename="data.json",
        language="json",
        env={"INTENTDIFF_SCHEMA_FETCH": "auto"},
        cache_dir=tmp_path,
        fetcher=lambda url, headers: FetchResponse(body=_schema("id"), final_url=url),
    )

    assert result.status == "error"
    assert "private" in (result.error or "")


def test_databricks_prefers_local_bundle_schema_command() -> None:
    result = resolve_schema(
        content="resources: {}\n",
        filename="databricks.yml",
        language="yaml",
        env=_env(INTENTDIFF_SCHEMA_ALLOW_COMMANDS="1"),
        command_runner=lambda command: _schema("job_cluster_key").decode("utf-8"),
    )

    assert result.status == "command"
    assert result.provider_id == "databricks:bundle"
    assert result.identity_fields == {
        "depends_on",
        "job_cluster_key",
        "library",
        "libraries",
        "task_key",
    }


def test_databricks_schema_command_is_disabled_without_explicit_opt_in(
    tmp_path: Path,
) -> None:
    result = resolve_schema(
        content="resources: {}\n",
        filename="databricks.yml",
        language="yaml",
        env=_env(),
        cache_dir=tmp_path,
        command_runner=lambda command: (_ for _ in ()).throw(
            AssertionError("command should not be called")
        ),
        fetcher=lambda url, headers: FetchResponse(
            body=_schema("job_cluster_key"),
            final_url=url,
        ),
    )

    assert result.status == "fetched"
    assert result.provider_id == "databricks:bundle"
