"""Repo-hostable GitHub App helpers for IntentumDiff review checks."""

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

    def __post_init__(self) -> None:
        for name in ("checked_files", "semantic_changes", "guardrail_violations"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise GitHubAppError(f"Review {name} must be a non-negative integer")
        if type(self.passed) is not bool:
            raise GitHubAppError("Review passed must be a boolean")


def verify_webhook_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Return True when *signature_header* matches GitHub's sha256 HMAC."""

    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), signature_header.encode("utf-8"))


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
    pr_number = pull_request.get("number") or payload.get("number")
    # Exact int only: int() coerced True and 1.5 to pull request 1 (#322); bool is excluded.
    if type(pr_number) is not int or pr_number <= 0:
        raise GitHubAppError("Webhook pull request number must be a positive integer")
    owner, repo = full_name.split("/", 1)
    if not owner or not repo:
        raise GitHubAppError("Webhook repository.full_name must be OWNER/REPO")
    return PullRequestRef(owner=owner, repo=repo, number=pr_number)


def build_check_run_payload(
    *,
    name: str,
    head_sha: str,
    summary_markdown: str,
    passed: bool,
    details_url: str | None = None,
) -> dict[str, Any]:
    if type(passed) is not bool:
        raise GitHubAppError("Review passed must be a boolean")
    conclusion = "success" if passed else "failure"
    payload: dict[str, Any] = {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": "IntentumDiff semantic review",
            "summary": summary_markdown,
        },
    }
    if details_url:
        payload["details_url"] = details_url
    return payload


def build_review_result_from_payload(payload: Mapping[str, Any]) -> GitHubAppReviewResult:
    """Render explicit review evidence; never infer a successful engine result."""

    review = payload.get("intentumdiff_review")
    if not isinstance(review, Mapping):
        raise GitHubAppError("Explicit intentumdiff_review evidence is required")
    evidence = GitHubAppReviewResult(
        checked_files=review.get("checked_files"),
        semantic_changes=review.get("semantic_changes"),
        guardrail_violations=review.get("guardrail_violations"),
        passed=review.get("passed"),
        summary_markdown="",
        html_report="",
    )
    checked_files = evidence.checked_files
    semantic_changes = evidence.semantic_changes
    guardrail_violations = evidence.guardrail_violations
    passed = evidence.passed
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
        name="IntentumDiff",
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
            "# IntentumDiff GitHub App Review",
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
        "<title>IntentumDiff GitHub App Review</title></head><body>"
        f"<main><h1>IntentumDiff GitHub App Review</h1><p>Status: <strong>{html.escape(status)}</strong></p>"
        f"<section class=\"metrics\">{rows}</section></main></body></html>"
    )


def decode_webhook_body(body: bytes) -> dict[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise GitHubAppError("Webhook JSON body must be an object")
    return payload


def create_app() -> Any:
    """Create the optional FastAPI app without making FastAPI a core dependency."""

    try:
        from fastapi import FastAPI, Header, HTTPException, Request
    except ImportError as exc:  # pragma: no cover - exercised when optional extra absent.
        raise RuntimeError("Install intentumdiff[serve] to run the GitHub App server.") from exc

    app = FastAPI(title="IntentumDiff GitHub App")

    @app.post("/github/webhook")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, Any]:
        body = await request.body()
        # A missing signing key must never disable authentication by accident.
        secret = getattr(request.app.state, "webhook_secret", None)
        if not isinstance(secret, str) or not secret:
            raise HTTPException(status_code=503, detail="webhook signing secret not configured")
        if not verify_webhook_signature(secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")
        try:
            payload = decode_webhook_body(body)
            if x_github_event == "pull_request":
                return build_pull_request_check_response(payload)
        except (GitHubAppError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "event": x_github_event}

    return app
