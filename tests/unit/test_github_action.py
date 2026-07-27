"""Tests for the local GitHub Action helper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import git

from intentdiff.core.models import (
    Change,
    ChangeType,
    CommitDiff,
    GuardrailSeverity,
    GuardrailViolation,
    SemanticDiff,
)
from scripts.github_action import (
    COMMENT_MARKER,
    HTML_REPORT,
    ActionOptions,
    _filter_commit_diff,
    _upsert_pr_comment,
    options_from_env,
    parse_bool,
    parse_paths,
    render_static_html_report,
    render_summary_markdown,
    run_action,
    summarize_commit_diff,
)


def _options(tmp_path: Path, **overrides: Any) -> ActionOptions:
    defaults = dict(
        repo=tmp_path,
        base_ref="HEAD~1",
        head_ref="HEAD",
        policy="",
        strict=False,
        fuel=None,
        paths=(),
        comment=False,
        github_token="",
        upload_sarif=True,
        upload_artifact=True,
        artifact_name="semantic-diff-report",
        fail_on_semantic_change=False,
        report_dir=tmp_path / "reports",
    )
    defaults.update(overrides)
    return ActionOptions(**defaults)


def _guardrail_violation() -> GuardrailViolation:
    return GuardrailViolation(
        rule_id="prod-host",
        severity=GuardrailSeverity.IMMUTABLE,
        file="config.yaml",
        language="yaml",
        semantic_path="server.host",
        old_value="localhost",
        new_value="prod.example.com",
        message="Production host changed",
    )


def _semantic_diff(
    filename: str = "app.py",
    *,
    semantic: bool = True,
    style: bool = False,
) -> SemanticDiff:
    changes = []
    if semantic:
        changes.append(
            Change(
                change_type=ChangeType.MODIFICATION,
                description="changed value",
            )
        )
    return SemanticDiff(
        old_filename=filename,
        new_filename=filename,
        language="python",
        has_semantic_changes=semantic and not style,
        is_style_only=style,
        changes=changes,
    )


def _commit_diff() -> CommitDiff:
    return CommitDiff(
        old_ref="old",
        new_ref="new",
        file_diffs=[
            _semantic_diff("app.py"),
            _semantic_diff("formatted.py", semantic=False, style=True),
        ],
        guardrail_violations=[_guardrail_violation()],
    )


class _FakeCommitDiffer:
    def __init__(self, commit_diff: CommitDiff, seen: list[Any], config: Any) -> None:
        self._commit_diff = commit_diff
        self._seen = seen
        self._config = config

    def diff_commit(self, **kwargs: Any) -> CommitDiff:
        self._seen.append((self._config, kwargs))
        return self._commit_diff


def test_options_resolve_pull_request_refs(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "base": {"sha": "base-sha"},
                    "head": {"sha": "head-sha"},
                }
            }
        ),
        encoding="utf-8",
    )

    options = options_from_env(
        {
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_WORKSPACE": str(tmp_path),
        }
    )

    assert options.base_ref == "base-sha"
    assert options.head_ref == "head-sha"
    assert options.repo == tmp_path


def test_parse_bool_and_paths() -> None:
    assert parse_bool("true") is True
    assert parse_bool("YES") is True
    assert parse_bool("0") is False
    assert parse_paths("src/**/*.py, config/*.yaml\nREADME.md") == (
        "src/**/*.py",
        "config/*.yaml",
        "README.md",
    )


def test_summary_counts_semantic_style_guardrail_and_cross_file(tmp_path: Path) -> None:
    commit_diff = _commit_diff().model_copy(
        update={
            "cross_file_changes": [
                {
                    "change_type": ChangeType.MOVE_TO_MODULE,
                    "symbol_name": "helper",
                    "old_file": "a.py",
                    "new_file": "b.py",
                }
            ]
        }
    )

    summary = summarize_commit_diff(
        commit_diff,
        strict=True,
        fail_on_semantic_change=False,
        report_dir=tmp_path,
    )

    assert summary.semantic_changes == 1
    assert summary.style_only_changes == 1
    assert summary.guardrail_violations == 1
    assert summary.immutable_violations == 1
    assert summary.cross_file_changes == 1
    assert summary.exit_code == 2


def test_run_action_writes_reports_outputs_and_summary(tmp_path: Path) -> None:
    output = tmp_path / "github-output.txt"
    step_summary = tmp_path / "step-summary.md"
    seen: list[Any] = []

    def _factory(config: Any) -> _FakeCommitDiffer:
        return _FakeCommitDiffer(_commit_diff(), seen, config)

    code = run_action(
        _options(tmp_path, fuel=1234),
        env={
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(step_summary),
        },
        differ_factory=_factory,
    )

    assert code == 0
    report_dir = tmp_path / "reports"
    assert (report_dir / "semantic-diff.json").exists()
    assert (report_dir / "guardrails.json").exists()
    assert (report_dir / "guardrails.sarif").exists()
    assert (report_dir / "summary.md").exists()
    assert (report_dir / HTML_REPORT).exists()
    assert "semantic-changes=1" in output.read_text(encoding="utf-8")
    assert f"html-path={report_dir / HTML_REPORT}" in output.read_text(encoding="utf-8")
    assert COMMENT_MARKER in step_summary.read_text(encoding="utf-8")
    assert seen[0][0].plugin_fuel == 1234


def test_run_action_strict_immutable_returns_two(tmp_path: Path) -> None:
    def _factory(config: Any) -> _FakeCommitDiffer:
        return _FakeCommitDiffer(_commit_diff(), [], config)

    code = run_action(
        _options(tmp_path, strict=True),
        env={},
        differ_factory=_factory,
    )

    assert code == 2


def test_run_action_can_fail_on_semantic_change(tmp_path: Path) -> None:
    commit_diff = CommitDiff(
        old_ref="old",
        new_ref="new",
        file_diffs=[_semantic_diff("app.py")],
    )

    def _factory(config: Any) -> _FakeCommitDiffer:
        return _FakeCommitDiffer(commit_diff, [], config)

    code = run_action(
        _options(tmp_path, fail_on_semantic_change=True),
        env={},
        differ_factory=_factory,
    )

    assert code == 3


def test_action_path_filters_keep_matching_files_guardrails_and_cross_file() -> None:
    commit_diff = CommitDiff(
        old_ref="old",
        new_ref="new",
        file_diffs=[
            _semantic_diff("src/app.py"),
            _semantic_diff("docs/readme.md"),
        ],
        guardrail_violations=[
            _guardrail_violation().model_copy(update={"file": "src/app.py"}),
            _guardrail_violation().model_copy(update={"file": "docs/readme.md"}),
        ],
        cross_file_changes=[
            {
                "change_type": ChangeType.MOVE_TO_MODULE,
                "symbol_name": "helper",
                "old_file": "src/old.py",
                "new_file": "src/app.py",
            },
            {
                "change_type": ChangeType.MOVE_TO_MODULE,
                "symbol_name": "docs",
                "old_file": "docs/old.md",
                "new_file": "docs/readme.md",
            },
        ],
    )

    filtered = _filter_commit_diff(commit_diff, ("src/*",))

    assert [diff.new_filename for diff in filtered.file_diffs] == ["src/app.py"]
    assert [violation.file for violation in filtered.guardrail_violations] == ["src/app.py"]
    assert [change.symbol_name for change in filtered.cross_file_changes] == ["helper"]


def test_upsert_pr_comment_creates_comment(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42}}), encoding="utf-8")
    calls: list[tuple[str, str, Any]] = []

    def _api(method: str, url: str, token: str, payload: Any) -> Any:
        calls.append((method, url, payload))
        return [] if method == "GET" else {"id": 1}

    _upsert_pr_comment(
        f"{COMMENT_MARKER}\nSummary",
        env={
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_REPOSITORY": "owner/repo",
        },
        token="token",
        api_request=_api,
    )

    assert calls[0][0] == "GET"
    assert calls[1][0] == "POST"
    assert calls[1][2]["body"].startswith(COMMENT_MARKER)


def test_upsert_pr_comment_updates_existing_comment(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42}}), encoding="utf-8")
    calls: list[tuple[str, str, Any]] = []

    def _api(method: str, url: str, token: str, payload: Any) -> Any:
        calls.append((method, url, payload))
        if method == "GET":
            return [{"id": 99, "body": f"{COMMENT_MARKER}\nOld"}]
        return {"id": 99}

    _upsert_pr_comment(
        f"{COMMENT_MARKER}\nNew",
        env={
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_REPOSITORY": "owner/repo",
        },
        token="token",
        api_request=_api,
    )

    assert calls[1][0] == "PATCH"
    assert calls[1][1].endswith("/99")


def test_comment_skips_without_token(tmp_path: Path, capsys) -> None:
    _upsert_pr_comment(
        f"{COMMENT_MARKER}\nSummary",
        env={},
        token="",
        api_request=lambda *_args: None,
    )

    assert "github-token is empty" in capsys.readouterr().out


def test_render_summary_markdown_mentions_guardrails() -> None:
    commit_diff = _commit_diff()
    summary = summarize_commit_diff(
        commit_diff,
        strict=False,
        fail_on_semantic_change=False,
        report_dir=Path("reports"),
    )

    rendered = render_summary_markdown(commit_diff, summary)

    assert COMMENT_MARKER in rendered
    assert "Protected Config Changes" in rendered
    assert "Production host changed" in rendered
    assert "intentdiff-review.html" in rendered


def test_static_html_report_escapes_workspace_controlled_text(tmp_path: Path) -> None:
    commit_diff = CommitDiff(
        old_ref="old",
        new_ref="new",
        file_diffs=[
            SemanticDiff(
                old_filename="<script>alert(1)</script>.py",
                new_filename="<script>alert(1)</script>.py",
                language="python",
                has_semantic_changes=True,
                changes=[
                    Change(
                        change_type=ChangeType.MODIFICATION,
                        description='<img src=x onerror="alert(1)">',
                    )
                ],
            )
        ],
    )
    summary = summarize_commit_diff(
        commit_diff,
        strict=False,
        fail_on_semantic_change=False,
        report_dir=tmp_path,
    )

    rendered = render_static_html_report(commit_diff, summary)

    assert "<script>alert(1)</script>" not in rendered
    assert '<img src=x onerror="alert(1)">' not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;.py" in rendered
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in rendered


def test_integration_temp_git_repo_guardrail_strict(tmp_path: Path) -> None:
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test")
        cfg.set_value("user", "email", "test@example.com")

    (tmp_path / "intentdiff.yaml").write_text(
        """
config:
  plugin_fuel: 10_000_000
guardrails:
  protected:
    - id: server-host
      language: yaml
      path: server.host
      severity: immutable
      message: Server host changed
""",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "server:\n  host: localhost\n",
        encoding="utf-8",
    )
    repo.index.add(["intentdiff.yaml", "config.yaml"])
    old_commit = repo.index.commit("initial")

    (tmp_path / "config.yaml").write_text(
        "server:\n  host: prod.example.com\n",
        encoding="utf-8",
    )
    repo.index.add(["config.yaml"])
    new_commit = repo.index.commit("update")

    code = run_action(
        _options(
            tmp_path,
            base_ref=old_commit.hexsha,
            head_ref=new_commit.hexsha,
            strict=True,
        ),
        env={},
    )

    assert code == 2
    guardrails = json.loads(
        (tmp_path / "reports" / "guardrails.json").read_text(encoding="utf-8")
    )
    sarif = json.loads(
        (tmp_path / "reports" / "guardrails.sarif").read_text(encoding="utf-8")
    )
    assert guardrails["immutable_count"] == 1
    assert sarif["runs"][0]["results"][0]["ruleId"] == "server-host"
