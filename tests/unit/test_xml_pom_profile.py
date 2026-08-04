"""Maven POM schema profile (issue #63): coordinate identity for dependency elements.

A ``<dependency>`` is identified by groupId+artifactId — ``<dependencies>`` is an
unordered keyed collection. Before this profile, a dependency reorder swallowed a
concurrent version bump entirely (gumtree paired subtrees positionally and the
2.0.9→2.0.10 edit vanished — the issue #46 disease); these contracts pin the fix.
"""

from __future__ import annotations

from intentumdiff import SemanticDiffer
from intentumdiff.core.models import ChangeType

_DEP = "<dependency><groupId>{g}</groupId><artifactId>{a}</artifactId><version>{v}</version></dependency>"


def _pom(*deps: str) -> str:
    return (
        "<project><modelVersion>4.0.0</modelVersion><dependencies>"
        + "".join(deps)
        + "</dependencies></project>\n"
    )


_SLF4J = _DEP.format(g="org.slf4j", a="slf4j-api", v="2.0.9")
_SLF4J_BUMPED = _DEP.format(g="org.slf4j", a="slf4j-api", v="2.0.10")
_GUAVA = _DEP.format(g="com.google.guava", a="guava", v="33.0.0-jre")
_JUPITER = _DEP.format(g="org.junit.jupiter", a="junit-jupiter", v="5.10.2")


def _diff(old: str, new: str):
    return SemanticDiffer().diff_strings(old, new, filename="pom.xml", language_hint="xml")


def test_pom_dependency_reorder_is_not_a_change() -> None:
    diff = _diff(_pom(_SLF4J, _GUAVA), _pom(_GUAVA, _SLF4J))
    assert diff.changes == []


def test_pom_reorder_with_version_bump_surfaces_exactly_the_bump() -> None:
    # THE regression this profile exists for: pre-profile, the reorder swallowed
    # the bump (zero changes) because slf4j's subtree paired with guava's.
    diff = _diff(_pom(_SLF4J, _GUAVA), _pom(_GUAVA, _SLF4J_BUMPED))
    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.change_type == ChangeType.MODIFICATION
    assert change.old_node is not None and change.new_node is not None
    assert change.old_node.label == "2.0.9"
    assert change.new_node.label == "2.0.10"


def test_pom_added_dependency_is_one_compact_addition() -> None:
    diff = _diff(_pom(_SLF4J, _GUAVA), _pom(_SLF4J, _GUAVA, _JUPITER))
    assert len(diff.changes) == 1
    assert diff.changes[0].change_type == ChangeType.ADDITION
    assert diff.changes[0].new_node is not None
    assert diff.changes[0].new_node.label == "dependency"


def test_pom_removed_dependency_is_one_compact_deletion() -> None:
    diff = _diff(_pom(_SLF4J, _GUAVA, _JUPITER), _pom(_SLF4J, _GUAVA))
    assert len(diff.changes) == 1
    assert diff.changes[0].change_type == ChangeType.DELETION
    assert diff.changes[0].old_node is not None
    assert diff.changes[0].old_node.label == "dependency"


def test_non_pom_xml_is_untouched_by_the_profile() -> None:
    # A generic XML doc (no <project> root) keeps the generic behavior: a real
    # edit still surfaces.
    old = "<config><item>alpha</item></config>\n"
    new = "<config><item>beta</item></config>\n"
    diff = SemanticDiffer().diff_strings(old, new, filename="config.xml", language_hint="xml")
    assert any(c.change_type == ChangeType.MODIFICATION for c in diff.changes)


# ── MSBuild profile (issue #63, second bundled dialect): items keyed by Include attribute.

_PKG_A = '<PackageReference Include="Newtonsoft.Json" Version="13.0.3" />'
_PKG_A_BUMPED = '<PackageReference Include="Newtonsoft.Json" Version="13.0.4" />'
_PKG_B = '<PackageReference Include="Serilog" Version="3.1.1" />'


def _csproj(*items: str) -> str:
    return '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>' + "".join(items) + "</ItemGroup></Project>\n"


def _diff_csproj(old: str, new: str):
    return SemanticDiffer().diff_strings(old, new, filename="app.csproj", language_hint="xml")


def test_msbuild_package_reorder_is_not_a_change() -> None:
    diff = _diff_csproj(_csproj(_PKG_A, _PKG_B), _csproj(_PKG_B, _PKG_A))
    assert diff.changes == []


def test_msbuild_reorder_with_version_bump_surfaces_exactly_the_bump() -> None:
    # Pre-profile this degraded to MOVE churn + DELETE/ADD of the version attribute.
    diff = _diff_csproj(_csproj(_PKG_A, _PKG_B), _csproj(_PKG_B, _PKG_A_BUMPED))
    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.change_type == ChangeType.MODIFICATION
    assert change.old_node is not None and change.new_node is not None
    assert change.old_node.label == "version=13.0.3"
    assert change.new_node.label == "version=13.0.4"


def test_msbuild_added_package_is_one_compact_addition() -> None:
    extra = '<PackageReference Include="xunit" Version="2.7.0" />'
    diff = _diff_csproj(_csproj(_PKG_A, _PKG_B), _csproj(_PKG_A, _PKG_B, extra))
    assert len(diff.changes) == 1
    assert diff.changes[0].change_type == ChangeType.ADDITION
    assert diff.changes[0].new_node is not None
    assert diff.changes[0].new_node.label == "PackageReference"
