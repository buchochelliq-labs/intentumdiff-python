from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_namespace_check():
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_release_namespaces.py"
    spec = importlib.util.spec_from_file_location("check_release_namespaces", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_probes_use_intentdiff_identity() -> None:
    helper = _load_namespace_check()

    probes = {probe.id: probe for probe in helper.default_probes()}

    assert probes["github-owner"].url == "https://github.com/buchochelliq-labs"
    assert probes["github-owner"].visible_statuses == (200,)
    assert probes["pypi-project"].url == "https://pypi.org/pypi/intentdiff/json"
    assert probes["github-repo"].url == "https://github.com/buchochelliq-labs/intentdiff"
    assert probes["github-repo"].visible_statuses == (200,)
    assert (
        probes["github-plugin-repo"].url
        == "https://github.com/buchochelliq-labs/intentdiff-registry"
    )
    assert probes["github-plugin-repo"].visible_statuses == (200,)
    assert (
        probes["vs-marketplace-extension"].url
        == "https://marketplace.visualstudio.com/items?itemName=buchochelliq-labs.intentdiff"
    )
    assert probes["open-vsx-extension"].url == "https://open-vsx.org/api/buchochelliq-labs/intentdiff"


def test_collect_results_classifies_available_taken_and_unknown() -> None:
    helper = _load_namespace_check()
    probes = [
        helper.NamespaceProbe(
            "owner",
            "Visible owner",
            "https://example.test/owner",
            visible_statuses=(200,),
            taken_statuses=(),
        ),
        helper.NamespaceProbe("missing", "Missing name", "https://example.test/missing"),
        helper.NamespaceProbe("existing", "Existing name", "https://example.test/existing"),
        helper.NamespaceProbe("odd", "Odd response", "https://example.test/odd"),
    ]

    def fake_status(url: str, timeout: float) -> int:
        assert timeout == 0.25
        return {
            "https://example.test/owner": 200,
            "https://example.test/missing": 404,
            "https://example.test/existing": 200,
            "https://example.test/odd": 403,
        }[url]

    results = {result.id: result for result in helper.collect_results(probes, timeout=0.25, get_status=fake_status)}

    assert results["owner"].status == "visible"
    assert "confirm ownership" in results["owner"].detail
    assert results["missing"].status == "available"
    assert "no public listing" in results["missing"].detail
    assert results["existing"].status == "taken"
    assert "already visible" in results["existing"].detail
    assert results["odd"].status == "unknown"
    assert "manual review" in results["odd"].detail


def test_collect_results_marks_network_failures_unknown() -> None:
    helper = _load_namespace_check()
    probes = [helper.NamespaceProbe("offline", "Offline name", "https://example.test/offline")]

    def failing_status(url: str, timeout: float) -> int:
        raise OSError("offline")

    result = helper.collect_results(probes, get_status=failing_status)[0]

    assert result.status == "unknown"
    assert "offline" in result.detail


def test_main_json_reports_results_and_nonzero_for_taken(monkeypatch, capsys) -> None:
    helper = _load_namespace_check()

    def fake_collect_results(probes, *, timeout=10.0, get_status=helper.http_status):
        return [
            helper.NamespaceResult(
                id="pypi-project",
                label="PyPI project intentdiff",
                status="taken",
                detail="HTTP 200: public listing or namespace is already visible.",
                url="https://pypi.org/pypi/intentdiff/json",
                note="",
            )
        ]

    monkeypatch.setattr(helper, "collect_results", fake_collect_results)

    exit_code = helper.main(["--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload[0]["id"] == "pypi-project"
    assert payload[0]["status"] == "taken"


def test_publish_workflow_has_testpypi_trusted_publisher_lane() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "publish.yml"
    ).read_text()

    assert "publish_testpypi:" in workflow
    assert "testpypi_version:" in workflow
    assert 'default: "0.0.1b1"' in workflow
    assert "python scripts/set_release_version.py" in workflow
    # The version reaches the verifier through the ENVIRONMENT, not through a template
    # expansion pasted into the shell command. It originates in a workflow_dispatch
    # input, so the `${{ }}` form was a code-injection vector in the workflow that
    # publishes to PyPI (zizmor template-injection). Assert both halves: the safe form
    # is present, and the unsafe one has not crept back.
    assert '--expected-version "$INTENTDIFF_EXPECTED_VERSION"' in workflow
    assert "--expected-version ${{ env.INTENTDIFF_EXPECTED_VERSION }}" not in workflow
    assert "name: Publish to TestPyPI" in workflow
    assert "name: testpypi" in workflow
    assert "repository-url: https://test.pypi.org/legacy/" in workflow
    assert "if: github.event_name == 'workflow_dispatch' && inputs.publish_testpypi" in workflow
    assert "artifact: Windows-arm64" in workflow
    # The arm64 wheel is built on a native arm64 runner, not cross-compiled from x64:
    # the cross build could not produce a working cffi cdylib. So the load-bearing fact
    # is the runner label, and the '--target aarch64-pc-windows-msvc' flag is gone by
    # design. Assert both halves so a silent revert to cross-compiling is caught.
    assert "windows-11-arm" in workflow
    assert "aarch64-pc-windows-msvc" not in workflow
    assert '--expected-platform-pattern "win_arm64"' in workflow
    assert "dist-${{ matrix.artifact }}" in workflow
    # Same reasoning as --expected-version above: the manifest path is built from an
    # env var inside the shell, not from a template expansion spliced into the command.
    # (`dist-${{ matrix.artifact }}` on the line above is fine - that is an action
    # input, not a shell command, so there is nothing to inject into.)
    assert 'artifacts-${ARTIFACT_NAME}.sha256' in workflow
    assert "artifacts-${{ matrix.artifact }}.sha256" not in workflow
    assert "attest-build-provenance" not in workflow
    assert "attestations: write" not in workflow
    assert "attestations: true" not in workflow
    assert workflow.count("attestations: false") == 2
