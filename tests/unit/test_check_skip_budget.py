from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[2] / "scripts" / "check_skip_budget.py"
    spec = importlib.util.spec_from_file_location("check_skip_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skipped_count_reads_testsuites_total(tmp_path: Path) -> None:
    module = _module()
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites tests="10" failures="0" skipped="3"><testsuite tests="10" skipped="3"/></testsuites>',
        encoding="utf-8",
    )
    assert module.skipped_count(report) == 3


def test_skipped_count_sums_child_suites_when_total_missing(tmp_path: Path) -> None:
    module = _module()
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites><testsuite tests="4" skipped="1"/><testsuite tests="6" skipped="2"/></testsuites>',
        encoding="utf-8",
    )
    assert module.skipped_count(report) == 3


def test_skipped_count_rejects_unknown_root(tmp_path: Path) -> None:
    module = _module()
    report = tmp_path / "report.xml"
    report.write_text("<report/>", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported JUnit root element"):
        module.skipped_count(report)
