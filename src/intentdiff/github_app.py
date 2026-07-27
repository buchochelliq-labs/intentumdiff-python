"""Repo-hostable GitHub App helpers for IntentDiff review checks."""

import hashlib
import hmac
import html
import json
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping


class GitHubAppError(ValueError):
    """Raised when GitHub App input cannot be safely accepted."""


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int


@dataclass(frozen=True)
class GistRef:
    gist_id: str
    revision: str | None = None


@dataclass(frozen=True)
class GitHubAppReviewResult:
    checked_files: int
    semantic_changes: int
    guardrail_violations: int
    passed: bool
    summary_markdown: str
    html_report: str


def verify_webhook_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Return True when *signature_header* matches GitHub's sha256 HMAC."""

    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_pull_request_url(url: str) -> PullRequestRef:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise GitHubAppError("Pull request URL must use https://github.com")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 4 or parts[2] != "pull":
        raise GitHubAppError("Pull request URL must look like https://github.com/OWNER/REPO/pull/NUMBER")
    try:
        number = int(parts[3])
    except ValueError as exc:
        raise GitHubAppError("Pull request number must be an integer") from exc
    if number <= 0:
        raise GitHubAppError("Pull request number must be positive")
    return PullRequestRef(owner=parts[0], repo=parts[1], number=number)


def parse_gist_url(url: str) -> GistRef:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "gist.github.com":
        raise GitHubAppError("Gist URL must use https://gist.github.com")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) not in {1, 2}:
        raise GitHubAppError("Gist URL must look like https://gist.github.com/OWNER/GIST_ID")
    gist_id = parts[-1]
    if not gist_id or any(char in gist_id for char in "\\?#"):
        raise GitHubAppError("Gist URL has an invalid gist id")
    revision = None
    fragment = parsed.fragment.strip()
    if fragment.startswith("file-"):
        revision = fragment
    return GistRef(gist_id=gist_id, revision=revision)


def parse_pull_request_event(payload: Mapping[str, Any]) -> PullRequestRef:
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    if not isinstance(repository, Mapping) or not isinstance(pull_request, Mapping):
        raise GitHubAppError("Webhook payload is missing repository or pull_request")
    full_name = str(repository.get("full_name") or "")
    if "/" not in full_name:
        raise GitHubAppError("Webhook repository.full_name must be OWNER/REPO")
    number = pull_request.get("number") or payload.get("number")
    try:
        pr_number = int(number)
    except (TypeError, ValueError) as exc:
        raise GitHubAppError("Webhook pull request number must be an integer") from exc
    owner, repo = full_name.split("/", 1)
    if not owner or not repo:
        raise GitHubAppError("Webhook repository.full_name must be OWNER/REPO")
    if pr_number <= 0:
        raise GitHubAppError("Webhook pull request number must be positive")
    return PullRequestRef(owner=owner, repo=repo, number=pr_number)


def build_check_run_payload(
    *,
    name: str,
    head_sha: str,
    summary_markdown: str,
    passed: bool,
    details_url: str | None = None,
) -> dict[str, Any]:
    conclusion = "success" if passed else "failure"
    payload: dict[str, Any] = {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": "IntentDiff semantic review",
            "summary": summary_markdown,
        },
    }
    if details_url:
        payload["details_url"] = details_url
    return payload


def build_review_result_from_payload(payload: Mapping[str, Any]) -> GitHubAppReviewResult:
    """Build deterministic local review artifacts from injectable webhook payload data."""

    review = payload.get("intentdiff_review")
    if not isinstance(review, Mapping):
        review = {}
    checked_files = _non_negative_int(review.get("checked_files"), default=0)
    semantic_changes = _non_negative_int(review.get("semantic_changes"), default=0)
    guardrail_violations = _non_negative_int(review.get("guardrail_violations"), default=0)
    passed = bool(review.get("passed", semantic_changes == 0 and guardrail_violations == 0))
    summary_markdown = render_github_app_summary(
        checked_files=checked_files,
        semantic_changes=semantic_changes,
        guardrail_violations=guardrail_violations,
        passed=passed,
    )
    html_report = render_github_app_html_report(
        checked_files=checked_files,
        semantic_changes=semantic_changes,
        guardrail_violations=guardrail_violations,
        passed=passed,
    )
    return GitHubAppReviewResult(
        checked_files=checked_files,
        semantic_changes=semantic_changes,
        guardrail_violations=guardrail_violations,
        passed=passed,
        summary_markdown=summary_markdown,
        html_report=html_report,
    )


def build_pull_request_check_response(
    payload: Mapping[str, Any],
    *,
    details_url: str | None = None,
    review_result: GitHubAppReviewResult | None = None,
) -> dict[str, Any]:
    pr = parse_pull_request_event(payload)
    pull_request = payload.get("pull_request")
    head = pull_request.get("head") if isinstance(pull_request, Mapping) else None
    head_sha = str(head.get("sha") if isinstance(head, Mapping) else payload.get("after") or "")
    if not head_sha:
        raise GitHubAppError("Webhook pull request head.sha is required for check-run creation")
    result = review_result or build_review_result_from_payload(payload)
    check_run = build_check_run_payload(
        name="IntentDiff",
        head_sha=head_sha,
        summary_markdown=result.summary_markdown,
        passed=result.passed,
        details_url=details_url,
    )
    return {
        "ok": True,
        "event": "pull_request",
        "owner": pr.owner,
        "repo": pr.repo,
        "number": pr.number,
        "check_run": check_run,
        "artifacts": {
            "summary_markdown": result.summary_markdown,
            "html_report": result.html_report,
        },
        "review": {
            "checked_files": result.checked_files,
            "semantic_changes": result.semantic_changes,
            "guardrail_violations": result.guardrail_violations,
            "passed": result.passed,
        },
    }


def render_github_app_summary(
    *,
    checked_files: int,
    semantic_changes: int,
    guardrail_violations: int,
    passed: bool,
) -> str:
    status = "passed" if passed else "needs attention"
    return "\n".join(
        [
            "# IntentDiff GitHub App Review",
            "",
            f"Status: **{status}**",
            "",
            "| Metric | Count |",
            "|---|---:|",
            f"| Files checked | {checked_files} |",
            f"| Semantic changes | {semantic_changes} |",
            f"| Guardrail violations | {guardrail_violations} |",
            "",
        ],
    )


def render_github_app_html_report(
    *,
    checked_files: int,
    semantic_changes: int,
    guardrail_violations: int,
    passed: bool,
) -> str:
    status = "passed" if passed else "needs attention"
    metrics = {
        "Files checked": checked_files,
        "Semantic changes": semantic_changes,
        "Guardrail violations": guardrail_violations,
    }
    rows = "".join(
        f"<div class=\"metric\"><strong>{value}</strong><span>{html.escape(label)}</span></div>"
        for label, value in metrics.items()
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>IntentDiff GitHub App Review</title></head><body>"
        f"<main><h1>IntentDiff GitHub App Review</h1><p>Status: <strong>{html.escape(status)}</strong></p>"
        f"<section class=\"metrics\">{rows}</section></main></body></html>"
    )


def decode_webhook_body(body: bytes) -> dict[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise GitHubAppError("Webhook JSON body must be an object")
    return payload


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def create_app() -> Any:
    """Create the optional FastAPI app without making FastAPI a core dependency."""

    try:
        from fastapi import FastAPI, Header, HTTPException, Request
    except ImportError as exc:  # pragma: no cover - exercised when optional extra absent.
        raise RuntimeError("Install intentdiff[serve] to run the GitHub App server.") from exc

    app = FastAPI(title="IntentDiff GitHub App")

    @app.post("/github/webhook")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, Any]:
        body = await request.body()
        secret = request.app.state.webhook_secret if hasattr(request.app.state, "webhook_secret") else ""
        if secret and not verify_webhook_signature(secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")
        payload = decode_webhook_body(body)
        if x_github_event == "pull_request":
            return build_pull_request_check_response(payload)
        return {"ok": True, "event": x_github_event}

    return app
