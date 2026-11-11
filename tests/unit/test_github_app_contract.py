from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from intentumdiff.github_app import (
    build_check_run_payload,
    build_pull_request_check_response,
    create_app,
    parse_gist_url,
    parse_pull_request_event,
    parse_pull_request_url,
    verify_webhook_signature,
)


def test_github_app_signature_url_event_and_check_payload_contracts() -> None:
    body = b'{"repository":{"full_name":"owner/repo"},"pull_request":{"number":7,"head":{"sha":"abc123"}}}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert verify_webhook_signature("secret", body, signature)
    assert not verify_webhook_signature("secret", body, "sha256=bad")

    pr = parse_pull_request_url("https://github.com/owner/repo/pull/7")
    assert (pr.owner, pr.repo, pr.number) == ("owner", "repo", 7)
    assert parse_pull_request_event(json.loads(body)) == pr

    with pytest.raises(ValueError):
        parse_pull_request_event({"repository": {"full_name": "/repo"}, "pull_request": {"number": 7}})
    with pytest.raises(ValueError):
        parse_pull_request_event({"repository": {"full_name": "owner/repo"}, "pull_request": {"number": 0}})

    payload = build_check_run_payload(
        name="IntentumDiff",
        head_sha="abc123",
        summary_markdown="summary",
        passed=False,
        details_url="https://example.invalid/review",
    )
    assert payload["status"] == "completed"
    assert payload["conclusion"] == "failure"
    assert payload["details_url"] == "https://example.invalid/review"

    with pytest.raises(ValueError, match="evidence is required"):
        build_pull_request_check_response(json.loads(body))
    reviewed_payload = json.loads(body)
    reviewed_payload["intentumdiff_review"] = {
        "checked_files": 0, "semantic_changes": 0,
        "guardrail_violations": 0, "passed": True,
    }
    response = build_pull_request_check_response(
        reviewed_payload,
        details_url="https://example.invalid/review",
    )
    assert response["ok"] is True
    assert response["owner"] == "owner"
    assert response["repo"] == "repo"
    assert response["number"] == 7
    assert response["check_run"]["head_sha"] == "abc123"
    assert response["check_run"]["conclusion"] == "success"
    assert "IntentumDiff GitHub App Review" in response["artifacts"]["html_report"]
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
        b'"intentumdiff_review":{"checked_files":2,"semantic_changes":1,"guardrail_violations":1,"passed":false}}'
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
    assert payload["check_run"]["head_sha"] == "abc123"
    assert payload["check_run"]["conclusion"] == "failure"
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

    missing_review = json.loads(body)
    del missing_review["intentumdiff_review"]
    missing_body = json.dumps(missing_review).encode()
    missing_signature = "sha256=" + hmac.new(b"secret", missing_body, hashlib.sha256).hexdigest()
    missing = client.post(
        "/github/webhook", content=missing_body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": missing_signature},
    )
    assert missing.status_code == 422
    assert "check_run" not in missing.json()


def test_gist_url_parser_accepts_only_github_gist_urls() -> None:
    gist = parse_gist_url("https://gist.github.com/owner/abcdef123456")
    assert gist.gist_id == "abcdef123456"

    with pytest.raises(ValueError):
        parse_gist_url("https://example.com/owner/abcdef123456")



def test_github_app_webhook_rejects_unsigned_or_unconfigured_requests() -> None:
    """#283: absence of the secret or signature is never implicit authentication opt-out."""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    app = create_app()
    client = fastapi_testclient.TestClient(app)
    body = b'{"repository":{"full_name":"owner/repo"},"pull_request":{"number":7}}'
    headers = {"X-GitHub-Event": "pull_request", "Content-Type": "application/json"}

    # Missing key denies all events even when they include a signature header.
    unconfigured = client.post("/github/webhook", content=body, headers=headers)
    assert unconfigured.status_code == 503
    assert unconfigured.json()["detail"] == "webhook signing secret not configured"

    app.state.webhook_secret = ""
    empty = client.post("/github/webhook", content=body, headers=headers)
    assert empty.status_code == 503

    app.state.webhook_secret = "configured-secret"
    unsigned = client.post("/github/webhook", content=body, headers=headers)
    assert unsigned.status_code == 401
    assert unsigned.json()["detail"] == "invalid signature"


_SIGNED_BODY = b"{}"
_VALID_SIGNATURE = "sha256=" + hmac.new(b"audit-secret", _SIGNED_BODY, hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=é",  # non-ASCII (#321): used to raise TypeError in compare_digest
        "sha256=" + "é" * 64,
        "sha256=" + "z" * 64,  # non-hex, right length
        "sha256=" + "a" * 63,  # wrong length
        "sha1=" + "a" * 40,  # wrong prefix
        _VALID_SIGNATURE.upper(),  # wrong-case prefix
        _VALID_SIGNATURE + "\n",
    ],
)
def test_malformed_webhook_signatures_are_rejected_without_raising(header) -> None:
    assert verify_webhook_signature("audit-secret", _SIGNED_BODY, header) is False


def test_valid_and_wrong_webhook_signature_controls() -> None:
    assert verify_webhook_signature("audit-secret", _SIGNED_BODY, _VALID_SIGNATURE) is True
    assert verify_webhook_signature("audit-secret", b"{ }", _VALID_SIGNATURE) is False

