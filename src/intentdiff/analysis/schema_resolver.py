"""Runtime JSON/YAML schema discovery, safe fetch, and cached hints."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from intentdiff.analysis.user_schemas import (
    load_user_schema_profiles,
    match_user_profile,
)

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA_TTL_SECONDS = 86_400
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
IDENTITY_FIELD_CANDIDATES = frozenset(
    {
        "git",
        "id",
        "job_cluster_key",
        "key",
        "local",
        "name",
        "operationid",
        "package",
        "task",
        "task_key",
    }
)

DBT_SCHEMA_BASE = "https://raw.githubusercontent.com/dbt-labs/dbt-jsonschema/main/schemas/latest"
DBT_SCHEMA_URLS = {
    "dbt_cloud": f"{DBT_SCHEMA_BASE}/dbt_cloud-latest.json",
    "dbt_project": f"{DBT_SCHEMA_BASE}/dbt_project-latest.json",
    "dbt_yml_files": f"{DBT_SCHEMA_BASE}/dbt_yml_files-latest.json",
    "dependencies": f"{DBT_SCHEMA_BASE}/dependencies-latest.json",
    "packages": f"{DBT_SCHEMA_BASE}/packages-latest.json",
    "selectors": f"{DBT_SCHEMA_BASE}/selectors-latest.json",
}
ADVISORY_ADF_NO_RAW_SCHEMA_URL = (
    "https://learn.microsoft.com/en-sg/answers/questions/2125264/"
    "are-there-json-schemas-for-adf-source-files"
)
DATABRICKS_BUNDLE_SCHEMA_URL = (
    "https://raw.githubusercontent.com/databricks/cli/main/bundle/schema/jsonschema.json"
)
OPENAPI_31_SCHEMA_URL = "https://spec.openapis.org/oas/3.1/schema/2022-10-07"
OPENAPI_30_SCHEMA_URL = "https://spec.openapis.org/oas/3.0/schema/2021-09-28"
KUBERNETES_SCHEMA_URL = "https://json.schemastore.org/kubernetes.json"
GITHUB_WORKFLOW_SCHEMA_URL = "https://json.schemastore.org/github-workflow.json"
AZURE_PIPELINES_SCHEMA_URL = "https://json.schemastore.org/azure-pipelines.json"
DBT_IDENTITY_FIELDS = frozenset(
    {
        "exposure",
        "macro",
        "metric",
        "model",
        "models",
        "name",
        "package_name",
        "packages",
        "project_name",
        "resource_type",
        "selector",
        "selectors",
        "seed",
        "snapshot",
        "snapshots",
        "source",
        "sources",
        "test",
        "tests",
        "version",
    }
)


@dataclass(frozen=True)
class FetchResponse:
    body: bytes
    final_url: str
    etag: str | None = None
    last_modified: str | None = None
    status: int = 200


@dataclass(frozen=True)
class SchemaResolution:
    provider_id: str
    status: str
    source_url: str | None = None
    schema: dict[str, Any] | None = None
    identity_fields: frozenset[str] = frozenset()
    cache_fingerprint: str = "schema:none"
    error: str | None = None

    @property
    def found(self) -> bool:
        return self.schema is not None


@dataclass(frozen=True)
class SchemaCandidate:
    provider_id: str
    url: str | None
    command: tuple[str, ...] = ()
    status: str | None = None
    advisory_url: str | None = None
    identity_fields: frozenset[str] = frozenset()


FetchFunc = Callable[[str, Mapping[str, str]], FetchResponse]
CommandRunner = Callable[[tuple[str, ...]], str]


def schema_cache_dir(env: Mapping[str, str] | None = None) -> Path:
    values = env or os.environ
    if os.name == "nt":
        base = Path(values.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(values.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "intentdiff" / "schemas"


def resolve_schema(
    *,
    content: str,
    filename: str,
    language: str | None,
    env: Mapping[str, str] | None = None,
    cache_dir: Path | None = None,
    fetcher: FetchFunc | None = None,
    command_runner: CommandRunner | None = None,
    now: float | None = None,
) -> SchemaResolution:
    values = env or os.environ
    mode = _schema_fetch_mode(values)
    declared = discover_declared_schema(content, language)
    # User-registered profiles (issue #63) outrank the embedded fetch and the
    # built-in provider registry: a claimed $schema URL or a filename/marker
    # match resolves OFFLINE from the local descriptor — no fetch, no logging
    # of proprietary schema content.
    user_profiles, _user_errors = load_user_schema_profiles(values)
    if user_profiles:
        profile = match_user_profile(
            user_profiles,
            filename=filename,
            content=content,
            declared_url=declared,
        )
        if profile is not None:
            return SchemaResolution(
                provider_id=profile.provider_id,
                status="user-profile",
                source_url=profile.schema_path,
                schema=profile.schema,
                identity_fields=profile.identity_fields,
                cache_fingerprint=profile.fingerprint,
            )
    candidate = (
        SchemaCandidate("embedded", declared)
        if declared
        else provider_schema_candidate(filename, language, content)
    )
    if candidate is None:
        return SchemaResolution(provider_id="none", status="not_found")
    if candidate.status:
        return SchemaResolution(
            provider_id=candidate.provider_id,
            status=candidate.status,
            source_url=candidate.advisory_url or candidate.url,
            identity_fields=candidate.identity_fields,
        )
    if mode == "off":
        return SchemaResolution(
            provider_id=candidate.provider_id,
            source_url=candidate.advisory_url or candidate.url,
            status="disabled",
            identity_fields=candidate.identity_fields,
        )

    ttl = _schema_ttl_seconds(values)
    root = cache_dir or schema_cache_dir(values)
    current_time = time.time() if now is None else now

    if candidate.command:
        if mode == "cache-only":
            return _resolve_from_cache(candidate, root, ttl, current_time, allow_stale=True)
        if _schema_commands_allowed(values):
            try:
                text = (command_runner or _run_command)(candidate.command)
                schema = _parse_schema_text(text)
                fingerprint = _fingerprint_text(candidate.provider_id, text)
                return SchemaResolution(
                    provider_id=candidate.provider_id,
                    status="command",
                    schema=schema,
                    identity_fields=_identity_fields_for(candidate, schema),
                    cache_fingerprint=fingerprint,
                )
            except Exception as exc:
                logger.debug("Schema command failed for %s", candidate.provider_id, exc_info=True)
                cached = _resolve_from_cache(candidate, root, ttl, current_time, allow_stale=True)
                if cached.found:
                    return cached
                if candidate.url:
                    candidate = SchemaCandidate(
                        candidate.provider_id,
                        candidate.url,
                        identity_fields=candidate.identity_fields,
                    )
                else:
                    return SchemaResolution(
                        provider_id=candidate.provider_id,
                        status="error",
                        error=str(exc),
                    )
        elif candidate.url:
            candidate = SchemaCandidate(
                candidate.provider_id,
                candidate.url,
                identity_fields=candidate.identity_fields,
            )
        else:
            cached = _resolve_from_cache(candidate, root, ttl, current_time, allow_stale=True)
            if cached.found:
                return cached
            return SchemaResolution(
                provider_id=candidate.provider_id,
                status="command_disabled",
                error=(
                    "schema provider command disabled; set "
                    "INTENTDIFF_SCHEMA_ALLOW_COMMANDS=1 to enable trusted "
                    "local schema helper commands"
                ),
            )

    if not candidate.url:
        return SchemaResolution(provider_id=candidate.provider_id, status="not_found")
    if mode == "cache-only":
        return _resolve_from_cache(candidate, root, ttl, current_time, allow_stale=True)
    try:
        return _fetch_or_cache(
            candidate,
            root,
            ttl,
            current_time,
            values,
            fetcher or _fetch_https,
        )
    except Exception as exc:
        logger.debug("Schema fetch failed for %s", candidate.url, exc_info=True)
        cached = _resolve_from_cache(candidate, root, ttl, current_time, allow_stale=True)
        if cached.found:
            return cached
        return SchemaResolution(
            provider_id=candidate.provider_id,
            source_url=candidate.url,
            status="error",
            error=str(exc),
        )


def discover_declared_schema(content: str, language: str | None = None) -> str | None:
    language_id = (language or "").lower()
    if language_id in {"json", "adf"} or _looks_like_json(content):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get("$schema")
            if isinstance(value, str) and value.strip():
                return value.strip()

    for line in content.splitlines()[:5]:
        match = re.match(r"\s*#\s*yaml-language-server:\s*\$schema=(\S+)\s*$", line)
        if match:
            return match.group(1).strip()
    return None


def provider_schema_candidate(
    filename: str,
    language: str | None,
    content: str = "",
) -> SchemaCandidate | None:
    normalized_path = filename.replace("\\", "/").lower()
    basename = normalized_path.rsplit("/", 1)[-1]
    language_id = (language or "").lower()

    if url := _openapi_schema_url(basename, content):
        version = "3.1" if url == OPENAPI_31_SCHEMA_URL else "3.0"
        return SchemaCandidate(
            f"openapi:{version}",
            url,
            identity_fields=frozenset({"operationId", "name"}),
        )
    if _looks_like_kubernetes_manifest(basename, content):
        return SchemaCandidate(
            "kubernetes:manifest",
            KUBERNETES_SCHEMA_URL,
            identity_fields=frozenset({"name"}),
        )
    if _looks_like_github_workflow(normalized_path, content):
        return SchemaCandidate(
            "github-actions:workflow",
            GITHUB_WORKFLOW_SCHEMA_URL,
            identity_fields=frozenset({"id", "name", "run", "runs-on", "uses"}),
        )
    if _looks_like_azure_pipeline(normalized_path, content):
        return SchemaCandidate(
            "azure-pipelines:pipeline",
            AZURE_PIPELINES_SCHEMA_URL,
            identity_fields=frozenset({"job", "stage", "task", "script", "displayName"}),
        )

    dbt_url = _dbt_schema_url(basename, language_id, content)
    if dbt_url:
        key = dbt_url.rsplit("/", 1)[-1].replace("-latest.json", "")
        return SchemaCandidate(
            f"dbt:{key}",
            dbt_url,
            identity_fields=DBT_IDENTITY_FIELDS,
        )

    if language_id in {"databricks", "databricks-workflow"} or basename in {
        "databricks.yml",
        "databricks.yaml",
    }:
        return SchemaCandidate(
            "databricks:bundle",
            DATABRICKS_BUNDLE_SCHEMA_URL,
            command=("databricks", "bundle", "schema"),
            identity_fields=frozenset(
                {
                    "depends_on",
                    "job_cluster_key",
                    "library",
                    "libraries",
                    "task_key",
                }
            ),
        )

    if language_id in {"adf"} or _looks_like_adf_path(basename):
        return SchemaCandidate(
            "adf:no_raw_schema",
            None,
            status="no_raw_schema",
            advisory_url=ADVISORY_ADF_NO_RAW_SCHEMA_URL,
        )
    if language_id in {"hcl", "terraform"} or basename.endswith((".tf", ".hcl")):
        return None
    return None


def derive_identity_fields(schema: dict[str, Any] | None) -> frozenset[str]:
    if not schema:
        return frozenset()
    found: set[str] = set()
    stack: list[Any] = [schema]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        if isinstance(current, dict):
            properties = current.get("properties")
            if isinstance(properties, dict):
                for key, value in properties.items():
                    normalized = _normalize_identity_field(key)
                    if normalized in IDENTITY_FIELD_CANDIDATES:
                        found.add(normalized)
                    stack.append(value)
            for key in (
                "items",
                "anyOf",
                "oneOf",
                "allOf",
                "$defs",
                "definitions",
                "patternProperties",
            ):
                value = current.get(key)
                if value is not None:
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return frozenset(found)


def _identity_fields_for(
    candidate: SchemaCandidate,
    schema: dict[str, Any] | None,
) -> frozenset[str]:
    return frozenset({*candidate.identity_fields, *derive_identity_fields(schema)})


def schema_cache_fingerprint(resolution: SchemaResolution | None) -> str:
    if resolution is None:
        return "schema:none"
    return resolution.cache_fingerprint


def schema_resolution_metadata(
    resolution: SchemaResolution | None,
) -> dict[str, Any] | None:
    """Return public diff metadata for a detected schema/provider."""
    if resolution is None or resolution.provider_id == "none":
        return None
    identity_fields = sorted(resolution.identity_fields)
    metadata: dict[str, Any] = {
        "provider_id": resolution.provider_id,
        "status": resolution.status,
        "source_url": resolution.source_url,
        "identity_fields": identity_fields,
        "detected": True,
        "available": bool(resolution.found and identity_fields),
    }
    if resolution.error:
        metadata["error"] = resolution.error
    return metadata


def _fetch_or_cache(
    candidate: SchemaCandidate,
    root: Path,
    ttl: int,
    now: float,
    env: Mapping[str, str],
    fetcher: FetchFunc,
) -> SchemaResolution:
    url = candidate.url
    if url is None:
        raise ValueError("schema candidate has no URL to fetch")
    cached = _read_cache(candidate, root)
    if cached and now - cached["fetched_at"] < ttl:
        return _resolution_from_cache(candidate, cached, "cache_hit")

    _validate_schema_url(url, env)
    headers: dict[str, str] = {
        "Accept": "application/schema+json, application/json;q=0.9, */*;q=0.1"
    }
    if cached:
        if isinstance(cached.get("etag"), str):
            headers["If-None-Match"] = cached["etag"]
        if isinstance(cached.get("last_modified"), str):
            headers["If-Modified-Since"] = cached["last_modified"]

    try:
        response = fetcher(url, headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cached:
            return _resolution_from_cache(candidate, cached, "not_modified")
        raise

    _validate_schema_url(response.final_url, env)
    if response.status == 304 and cached:
        return _resolution_from_cache(candidate, cached, "not_modified")
    if len(response.body) > DEFAULT_MAX_BYTES:
        raise ValueError(f"schema exceeds {DEFAULT_MAX_BYTES} bytes")
    text = response.body.decode("utf-8")
    schema = _parse_schema_text(text)
    content_hash = hashlib.sha256(response.body).hexdigest()
    metadata = {
        "provider_id": candidate.provider_id,
        "source_url": url,
        "final_url": response.final_url,
        "fetched_at": now,
        "etag": response.etag,
        "last_modified": response.last_modified,
        "content_hash": content_hash,
        "byte_size": len(response.body),
        "schema": schema,
    }
    _write_cache(candidate, root, metadata)
    return _resolution_from_cache(candidate, metadata, "fetched")


def _resolve_from_cache(
    candidate: SchemaCandidate,
    root: Path,
    ttl: int,
    now: float,
    *,
    allow_stale: bool,
) -> SchemaResolution:
    cached = _read_cache(candidate, root)
    if not cached:
        return SchemaResolution(
            provider_id=candidate.provider_id,
            source_url=candidate.url,
            status="cache_miss",
            identity_fields=candidate.identity_fields,
        )
    if not allow_stale and now - cached["fetched_at"] >= ttl:
        return SchemaResolution(
            provider_id=candidate.provider_id,
            source_url=candidate.url,
            status="cache_stale",
            identity_fields=candidate.identity_fields,
        )
    status = "cache_hit" if now - cached["fetched_at"] < ttl else "stale_cache"
    return _resolution_from_cache(candidate, cached, status)


def _resolution_from_cache(
    candidate: SchemaCandidate,
    cached: Mapping[str, Any],
    status: str,
) -> SchemaResolution:
    schema = cached.get("schema")
    if not isinstance(schema, dict):
        schema = None
    content_hash = str(cached.get("content_hash") or "")
    fingerprint = (
        f"schema:{candidate.provider_id}:{content_hash}"
        if content_hash
        else f"schema:{candidate.provider_id}:unknown"
    )
    return SchemaResolution(
        provider_id=candidate.provider_id,
        source_url=str(cached.get("source_url") or candidate.url or ""),
        status=status,
        schema=schema,
        identity_fields=_identity_fields_for(candidate, schema),
        cache_fingerprint=fingerprint,
    )


def _read_cache(candidate: SchemaCandidate, root: Path) -> dict[str, Any] | None:
    path = _cache_file(candidate, root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _write_cache(candidate: SchemaCandidate, root: Path, data: Mapping[str, Any]) -> None:
    path = _cache_file(candidate, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")


def _cache_file(candidate: SchemaCandidate, root: Path) -> Path:
    key = candidate.url or " ".join(candidate.command) or candidate.provider_id
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return root / f"{digest}.json"


def _fetch_https(url: str, headers: Mapping[str, str]) -> FetchResponse:
    request = urllib.request.Request(url, headers=dict(headers))  # noqa: S310
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _HttpsOnlyRedirectHandler,
        urllib.request.HTTPSHandler(context=context),
    )
    with opener.open(  # noqa: S310 - URL and redirects are validated before fetch.
        request,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    ) as response:
        body = response.read(DEFAULT_MAX_BYTES + 1)
        return FetchResponse(
            body=body,
            final_url=response.geturl(),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            status=getattr(response, "status", 200),
        )


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        if urllib.parse.urlparse(newurl).scheme.lower() != "https":
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "schema redirect target must use HTTPS",
                dict(headers),
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _run_command(command: tuple[str, ...]) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv from trusted provider registry.
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    if len(result.stdout.encode("utf-8")) > DEFAULT_MAX_BYTES:
        raise ValueError(f"schema exceeds {DEFAULT_MAX_BYTES} bytes")
    return result.stdout


def _schema_fetch_mode(env: Mapping[str, str]) -> str:
    value = env.get("INTENTDIFF_SCHEMA_FETCH", "cache-only").strip().lower()
    return value if value in {"auto", "cache-only", "off"} else "cache-only"


def _schema_commands_allowed(env: Mapping[str, str]) -> bool:
    return env.get("INTENTDIFF_SCHEMA_ALLOW_COMMANDS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _schema_ttl_seconds(env: Mapping[str, str]) -> int:
    raw = env.get("INTENTDIFF_SCHEMA_CACHE_TTL_SECONDS", "")
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return DEFAULT_SCHEMA_TTL_SECONDS


def _parse_schema_text(text: str) -> dict[str, Any]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("schema root is not an object")
    return parsed


def _fingerprint_text(provider_id: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"schema:{provider_id}:{digest}"


def _validate_schema_url(url: str, env: Mapping[str, str]) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("schema URL must use HTTPS")
    if not parsed.hostname:
        raise ValueError("schema URL must include a host")
    if env.get("INTENTDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS", "").strip().lower() in {"1", "true", "yes"}:
        return
    if _is_private_host(parsed.hostname):
        raise ValueError("schema URL host resolves to a private or local address")


def _is_private_host(host: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        addresses = {host}
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def _dbt_schema_url(basename: str, language_id: str, content: str) -> str | None:
    if basename in {"dbt_project.yml", "dbt_project.yaml"}:
        return DBT_SCHEMA_URLS["dbt_project"]
    if basename in {"packages.yml", "packages.yaml"}:
        return DBT_SCHEMA_URLS["packages"]
    if basename in {"dependencies.yml", "dependencies.yaml"}:
        return DBT_SCHEMA_URLS["dependencies"]
    if basename in {"selectors.yml", "selectors.yaml"}:
        return DBT_SCHEMA_URLS["selectors"]
    if basename in {"dbt_cloud.yml", "dbt_cloud.yaml"}:
        return DBT_SCHEMA_URLS["dbt_cloud"]
    if language_id in {"dbt-yaml", "dbt-config", "dbt-packages"}:
        if language_id == "dbt-config":
            return DBT_SCHEMA_URLS["dbt_project"]
        if language_id == "dbt-packages":
            return DBT_SCHEMA_URLS["packages"]
        return DBT_SCHEMA_URLS["dbt_yml_files"]
    markers = ("version", "models", "sources", "exposures", "seeds", "snapshots")
    if basename.endswith((".yml", ".yaml")) and any(
        re.search(rf"(?m)^\s*{re.escape(marker)}\s*:", content) for marker in markers
    ):
        return DBT_SCHEMA_URLS["dbt_yml_files"]
    return None


def _openapi_schema_url(basename: str, content: str) -> str | None:
    if not basename.endswith((".json", ".yaml", ".yml")):
        return None
    lowered = basename.lower()
    named_like_openapi = lowered in {
        "openapi.json",
        "openapi.yaml",
        "openapi.yml",
        "swagger.json",
        "swagger.yaml",
        "swagger.yml",
    }
    version = _top_level_scalar(content, "openapi")
    if version:
        if version.startswith("3.1"):
            return OPENAPI_31_SCHEMA_URL
        return OPENAPI_30_SCHEMA_URL
    if _top_level_scalar(content, "swagger") or named_like_openapi:
        return OPENAPI_30_SCHEMA_URL
    return None


def _looks_like_kubernetes_manifest(basename: str, content: str) -> bool:
    if not basename.endswith((".json", ".yaml", ".yml")):
        return False
    return bool(
        _top_level_scalar(content, "apiVersion")
        and _top_level_scalar(content, "kind")
        and re.search(r"(?m)^\s*metadata\s*:", content)
    )


def _looks_like_github_workflow(path: str, content: str) -> bool:
    if not path.endswith((".yaml", ".yml")):
        return False
    if "/.github/workflows/" in f"/{path}":
        return True
    return bool(
        re.search(r"(?m)^\s*on\s*:", content)
        and re.search(r"(?m)^\s*jobs\s*:", content)
        and (
            re.search(r"(?m)^\s*uses\s*:", content)
            or re.search(r"(?m)^\s*runs-on\s*:", content)
        )
    )


def _looks_like_azure_pipeline(path: str, content: str) -> bool:
    basename = path.rsplit("/", 1)[-1]
    if not path.endswith((".yaml", ".yml")):
        return False
    if basename in {"azure-pipelines.yml", "azure-pipelines.yaml"}:
        return True
    return bool(
        re.search(r"(?m)^\s*(trigger|pr)\s*:", content)
        and re.search(r"(?m)^\s*(stages|jobs|steps)\s*:", content)
        and (
            re.search(r"(?m)^\s*pool\s*:", content)
            or re.search(r"(?m)^\s*vmImage\s*:", content)
            or re.search(r"(?m)^\s*task\s*:", content)
        )
    )


def _top_level_scalar(content: str, key: str) -> str:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        value = parsed.get(key)
        return str(value).strip() if value is not None else ""
    match = re.search(rf"(?m)^{re.escape(key)}\s*:\s*['\"]?([^'\"\n#]+)", content)
    return match.group(1).strip() if match else ""


def _looks_like_adf_path(basename: str) -> bool:
    direct_names = {"pipeline.json", "dataset.json", "linkedservice.json", "factory.json"}
    return basename in direct_names or any(
        basename.endswith(suffix)
        for suffix in (
            ".pipeline.json",
            ".dataset.json",
            ".linkedservice.json",
            ".trigger.json",
        )
    )


def _normalize_identity_field(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _looks_like_json(content: str) -> bool:
    stripped = content.lstrip()
    return stripped.startswith("{")
