from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_security_prereq_check():
    script = Path(__file__).resolve().parents[2] / "scripts" / "security_prereq_check.py"
    spec = importlib.util.spec_from_file_location("security_prereq_check", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)


def test_find_osv_scanner_prefers_path(tmp_path: Path) -> None:
    helper = _load_security_prereq_check()
    scanner = tmp_path / "bin" / "osv-scanner.exe"
    _make_executable(scanner)

    found = helper.find_osv_scanner(
        {"PATH": str(scanner.parent), "LOCALAPPDATA": str(tmp_path / "local")},
        system="Windows",
    )

    assert found == scanner


def test_find_osv_scanner_uses_winget_cache(tmp_path: Path) -> None:
    helper = _load_security_prereq_check()
    scanner = (
        tmp_path
        / "local"
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Google.OSVScanner_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "osv-scanner.exe"
    )
    _make_executable(scanner)

    found = helper.find_osv_scanner(
        {"PATH": "", "LOCALAPPDATA": str(tmp_path / "local")},
        system="Windows",
    )

    assert found == scanner


def test_collect_checks_reports_missing_tools_with_remediation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    helper = _load_security_prereq_check()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env = {
        "PATH": "",
        "LOCALAPPDATA": str(tmp_path / "local"),
        "CARGO_HOME": str(tmp_path / "cargo"),
        "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "browsers"),
    }

    checks = {check.id: check for check in helper.collect_checks(env, system="Windows")}

    assert checks["osv-scanner"].status == "missing"
    assert "winget install Google.OSVScanner" in checks["osv-scanner"].remediation
    assert checks["cargo-advisory-cache"].status == "warn"
    assert "normal developer shell or CI" in checks["cargo-advisory-cache"].remediation
    assert checks["pip-audit"].status == "missing"
    assert "uv tool install pip-audit" in checks["pip-audit"].remediation
    assert checks["playwright-python"].status == "warn"
    assert checks["playwright-chromium"].status == "warn"
    assert "playwright install chromium" in checks["playwright-chromium"].remediation


def test_main_json_exits_nonzero_when_required_tools_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    helper = _load_security_prereq_check()
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "cargo"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))

    exit_code = helper.main(["--json"])

    assert exit_code == 1
    assert '"id": "osv-scanner"' in capsys.readouterr().out
