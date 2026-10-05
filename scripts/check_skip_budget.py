"""Fail CI when pytest skip coverage regresses beyond the reviewed platform budget."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def skipped_count(junit_xml: Path) -> int:
    root = ET.parse(junit_xml).getroot()
    if root.tag == "testsuite":
        return int(root.attrib.get("skipped", "0"))
    if root.tag == "testsuites":
        if "skipped" in root.attrib:
            return int(root.attrib["skipped"])
        return sum(int(suite.attrib.get("skipped", "0")) for suite in root.findall("testsuite"))
    raise ValueError(f"unsupported JUnit root element: {root.tag}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junitxml", type=Path, required=True)
    parser.add_argument("--max-skipped", type=int, required=True)
    args = parser.parse_args()

    skipped = skipped_count(args.junitxml)
    print(f"pytest skipped tests: {skipped}; reviewed budget: {args.max_skipped}")
    if skipped > args.max_skipped:
        raise SystemExit(
            f"skip budget exceeded: {skipped} skipped tests > reviewed maximum {args.max_skipped}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
