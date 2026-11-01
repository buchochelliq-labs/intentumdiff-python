"""No-network regressions for schema host and redirect fail-closed policy (#255/#256)."""

from __future__ import annotations

import socket
import urllib.error
import urllib.request

import pytest

from intentumdiff.analysis.schema_resolver import (
    _HttpsOnlyRedirectHandler,
    _validate_schema_url,
    resolve_schema,
    FetchResponse,
)


def _resolve_to(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 0))],
    )


def test_schema_dns_failure_is_not_treated_as_public(monkeypatch):
    def failed(host, port):
        raise socket.gaierror("transient lookup failure")

    monkeypatch.setattr(socket, "getaddrinfo", failed)
    with pytest.raises(ValueError, match="could not be resolved"):
        _validate_schema_url("https://example.test/schema.json", {})
    # Explicit opt-in does not depend on DNS and is preserved.
    _validate_schema_url(
        "https://example.test/schema.json",
        {"INTENTUMDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS": "1"},
    )


def test_schema_empty_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [])
    with pytest.raises(ValueError, match="returned no addresses"):
        _validate_schema_url("https://example.test/schema.json", {})


def test_schema_public_and_private_dns_lookups(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    _validate_schema_url("https://example.test/schema.json", {})
    _resolve_to(monkeypatch, "127.0.0.1")
    with pytest.raises(ValueError, match="private or local"):
        _validate_schema_url("https://example.test/schema.json", {})


def test_private_https_redirect_rejected_before_request(monkeypatch):
    _resolve_to(monkeypatch, "127.0.0.1")
    request = urllib.request.Request("https://public.example/schema.json")
    handler = _HttpsOnlyRedirectHandler()
    with pytest.raises(ValueError, match="private or local"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://127.0.0.1/private",
        )


def test_public_https_redirect_remains_allowed(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    request = urllib.request.Request("https://public.example/schema.json")
    redirected = _HttpsOnlyRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "https://another.example/schema.json",
    )
    assert redirected is not None
    assert redirected.full_url == "https://another.example/schema.json"


def test_explicit_private_host_opt_in_applies_to_redirect(monkeypatch):
    _resolve_to(monkeypatch, "127.0.0.1")
    request = urllib.request.Request("https://public.example/schema.json")
    handler = _HttpsOnlyRedirectHandler(
        {"INTENTUMDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS": "1"},
    )
    redirected = handler.redirect_request(
        request, None, 302, "Found", {}, "https://127.0.0.1/private",
    )
    assert redirected is not None
    assert redirected.full_url == "https://127.0.0.1/private"


def test_non_https_redirect_still_fails():
    request = urllib.request.Request("https://public.example/schema.json")
    with pytest.raises(urllib.error.HTTPError, match="must use HTTPS"):
        _HttpsOnlyRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "http://127.0.0.1/private",
        )


def test_default_schema_fetch_preserves_explicit_host_policy(monkeypatch, tmp_path):
    from intentumdiff.analysis import schema_resolver

    seen = []

    def fake_fetch(url, headers, *, env=None):
        seen.append((url, dict(env or {})))
        return FetchResponse(
            body=b'{"type":"object","properties":{"id":{"type":"string"}}}',
            final_url=url,
        )

    monkeypatch.setattr(schema_resolver, "_fetch_https", fake_fetch)
    result = resolve_schema(
        content='{"$schema":"https://example.test/schema.json"}',
        filename="data.json",
        language="json",
        env={
            "INTENTUMDIFF_SCHEMA_FETCH": "auto",
            "INTENTUMDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS": "1",
        },
        cache_dir=tmp_path,
    )
    assert result.status == "fetched"
    assert len(seen) == 1
    assert seen[0][0] == "https://example.test/schema.json"
    assert seen[0][1]["INTENTUMDIFF_SCHEMA_ALLOW_PRIVATE_HOSTS"] == "1"
