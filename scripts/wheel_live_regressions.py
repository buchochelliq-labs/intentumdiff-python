"""Cold real-stdio acceptance using only the clean installed wheel.

Generic text must not block the following GraphQL request. The 120-second
budget is the existing desktop review deadline, not a relaxed media timeout.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

CASES = {'generic': {'filename': 'code.txt',
             'old': 'function processData(input) {\n'
                    '    result = transform(input)\n'
                    '    return result\n'
                    '}\n'
                    '\n'
                    'config = {\n'
                    '    timeout: 30,\n'
                    '    retries: 3\n'
                    '}\n',
             'new': 'function processData(input, options) {\n'
                    '    validated = validate(input)\n'
                    '    result = transform(validated, options)\n'
                    '    log("Processing complete")\n'
                    '    return result\n'
                    '}\n'
                    '\n'
                    'config = {\n'
                    '    timeout: 60,\n'
                    '    retries: 5,\n'
                    '    verbose: true\n'
                    '}\n'},
 'graphql': {'filename': 'schema.graphql',
             'old': 'type User {\n  id: ID\n}\n',
             'new': 'type User {\n  id: ID\n  name: String\n}\n'}}


def main():
    with tempfile.TemporaryDirectory(prefix="intentumdiff-cold-stdio-") as directory:
        root = Path(directory)
        def git(*args):
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
        git("init")
        for case in CASES.values():
            (root / case["filename"]).write_text(case["old"], encoding="utf-8")
        git("add", ".")
        git("-c", "user.name=Installed wheel acceptance", "-c",
            "user.email=acceptance@example.invalid", "commit", "-m", "Baseline")
        requests = []
        for seq, case in enumerate(CASES.values(), start=1):
            (root / case["filename"]).write_text(case["new"], encoding="utf-8")
            requests.append(dict(op="diff", seq=seq, path=case["filename"], content=case["new"]))
        requests.append(dict(op="review", seq=3, old_ref="HEAD", stream=True))
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "intentumdiff", "live-server", str(root),
             "--stdio", "--ref", "HEAD", "--debounce", "0.05"],
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=root,
        )
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr
        messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert not [m for m in messages if m.get("ok") is False or m.get("error")], messages
        for seq, language in enumerate(CASES, start=1):
            replies = [m for m in messages if m.get("op") == "diff" and m.get("seq") == seq]
            assert len(replies) == 1, (language, "missing or duplicate response", messages)
            diff = replies[0]["diff"]
            assert diff["language"] == language and not diff["is_style_only"], diff
            assert not diff["parse_errors"], diff
            assert len(diff["changes"]) == (7 if language == "generic" else 1), diff
            if language == "graphql":
                change = diff["changes"][0]
                assert change["change_type"] == "ADDITION" and change["new_node"]["label"] == "name", change
        terminal = [m for m in messages if m.get("op") == "review" and m.get("seq") == 3]
        assert len(terminal) == 1, ("missing terminal review", messages)
        diffs = terminal[0]["commit_diff"]["file_diffs"]
        assert {d["language"] for d in diffs} == {"generic", "graphql"}, diffs
        assert all(d["changes"] and not d["is_style_only"] and not d["parse_errors"] for d in diffs), diffs
        print(f"Cold installed stdio: generic 7 changes, GraphQL name addition, terminal Git review; {elapsed:.3f}s")


if __name__ == "__main__":
    main()
