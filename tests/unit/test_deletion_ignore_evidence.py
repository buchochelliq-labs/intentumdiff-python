"""Actual tracked deletion must not become an inferred ignore exclusion."""
import json
import os
import subprocess
from pathlib import Path

from intentumdiff import CommitDiffer


def test_real_deletion_remains_deletion_when_ignore_rules_change(tmp_path):
    case = json.loads((Path(__file__).parents[1] / "fixtures/deletion_ignore_evidence.json").read_text(encoding="utf-8"))
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for name, content in case["old_files"].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "before")
    for name in case["old_files"]:
        if name not in case["new_files"]:
            (tmp_path / name).unlink()
    for name, content in case["new_files"].items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "delete file and edit ignore rules")
    result = CommitDiffer().diff_commit(tmp_path, "HEAD~1", "HEAD")
    deleted = next(diff for diff in result.file_diffs if diff.old_filename == case["deleted_path"])
    assert deleted.gitignore_excluded is case["gitignore_excluded"]
    assert deleted.changes
    assert not (tmp_path / case["deleted_path"]).exists()

    if probe := os.environ.get("INTENTUMDIFF_NATIVE_PROBE"):
        request = {"repo": str(tmp_path), "wasm": str(Path(__file__).parents[2] / "src/intentumdiff/wasm"), "filename": case["deleted_path"], "old": case["old_files"][case["deleted_path"]], "new": ""}
        native = json.loads(subprocess.run([probe], input=json.dumps(request), text=True, encoding="utf-8", capture_output=True, check=True).stdout)
        assert native.get("gitignore_excluded", False) is deleted.gitignore_excluded
        assert [change["change_type"] for change in native["changes"]] == [change.change_type.value for change in deleted.changes]
