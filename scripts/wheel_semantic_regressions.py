"""Source-judged acceptance against the installed wheel; no component overlays."""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from intentumdiff import SemanticDiffer
from intentumdiff.plugins.loader import load_plugin

CASES = {'adf': {'filename': 'pipeline.json',
         'old': '{\n'
                '  "name": "CopyPipeline",\n'
                '  "properties": {\n'
                '    "activities": [\n'
                '      {\n'
                '        "name": "CopyData",\n'
                '        "type": "Copy",\n'
                '        "inputs":  [{"referenceName": "Source", "type": "DatasetReference"}],\n'
                '        "outputs": [{"referenceName": "Sink",   "type": "DatasetReference"}],\n'
                '        "typeProperties": {"source": {"type": "BlobSource"}, "sink": {"type": '
                '"BlobSink"}}\n'
                '      }\n'
                '    ]\n'
                '  }\n'
                '}\n',
         'new': '{\n'
                '  "name": "CopyPipeline",\n'
                '  "properties": {\n'
                '    "parameters": {\n'
                '      "targetFolder": {"type": "string", "defaultValue": "output"}\n'
                '    },\n'
                '    "activities": [\n'
                '      {\n'
                '        "name": "CopyData",\n'
                '        "type": "Copy",\n'
                '        "inputs":  [{"referenceName": "Source", "type": "DatasetReference"}],\n'
                '        "outputs": [{"referenceName": "Sink",   "type": "DatasetReference"}],\n'
                '        "typeProperties": {"source": {"type": "BlobSource"}, "sink": {"type": '
                '"BlobSink"}}\n'
                '      },\n'
                '      {\n'
                '        "name": "LogSuccess",\n'
                '        "type": "WebActivity",\n'
                '        "dependsOn": [{"activity": "CopyData", "dependencyConditions": '
                '["Succeeded"]}],\n'
                '        "typeProperties": {"url": "https://example.com/log", "method": "POST"}\n'
                '      }\n'
                '    ]\n'
                '  }\n'
                '}\n'},
 'css': {'filename': 'style.css',
         'old': '.button {\n'
                '  background: blue;\n'
                '  color: white;\n'
                '  padding: 10px;\n'
                '  border: none;\n'
                '}\n'
                '\n'
                '.button:hover {\n'
                '  background: darkblue;\n'
                '}\n',
         'new': '.button {\n'
                '  background-color: #2563eb;\n'
                '  color: #ffffff;\n'
                '  padding: 8px 16px;\n'
                '  border: none;\n'
                '  border-radius: 6px;\n'
                '  font-size: 14px;\n'
                '  cursor: pointer;\n'
                '  transition: background-color 0.2s ease;\n'
                '}\n'
                '\n'
                '.button:hover {\n'
                '  background-color: #1d4ed8;\n'
                '}\n'
                '\n'
                '.button:focus {\n'
                '  outline: 2px solid #93c5fd;\n'
                '  outline-offset: 2px;\n'
                '}\n'},
 'databricks-workflow': {'filename': 'workflow.yml',
                         'old': 'name: etl_job\n'
                                'tasks:\n'
                                '  - task_key: ingest\n'
                                '    notebook_task:\n'
                                '      notebook_path: /notebooks/ingest\n',
                         'new': 'name: etl_job\n'
                                'parameters:\n'
                                '  - name: env\n'
                                '    default: prod\n'
                                'tasks:\n'
                                '  - task_key: ingest\n'
                                '    notebook_task:\n'
                                '      notebook_path: /notebooks/ingest\n'
                                '  - task_key: transform\n'
                                '    depends_on:\n'
                                '      - task_key: ingest\n'
                                '    notebook_task:\n'
                                '      notebook_path: /notebooks/transform\n'},
 'mdx': {'filename': 'doc.mdx',
         'old': '# Getting Started\n'
                '\n'
                'Welcome to the docs.\n'
                '\n'
                '## Installation\n'
                '\n'
                'Run `npm install` to get started.\n',
         'new': "import { Callout } from './components'\n"
                '\n'
                '# Getting Started\n'
                '\n'
                'Welcome to the docs. This guide helps you get up and running.\n'
                '\n'
                '<Callout type="info">\n'
                '  Make sure you have Node.js 18+ installed.\n'
                '</Callout>\n'
                '\n'
                '## Installation\n'
                '\n'
                '```bash\n'
                'npm install my-package\n'
                '```\n'},
 'scss': {'filename': 'style.scss',
          'old': '$primary: blue;\n'
                 '\n'
                 '.button {\n'
                 '  background: $primary;\n'
                 '  color: white;\n'
                 '  padding: 10px;\n'
                 '}\n',
          'new': '$primary:     #2563eb;\n'
                 '$primary-dark: #1d4ed8;\n'
                 '$white:        #ffffff;\n'
                 '\n'
                 '@mixin button-base {\n'
                 '  border: none;\n'
                 '  border-radius: 6px;\n'
                 '  cursor: pointer;\n'
                 '  transition: background-color 0.2s ease;\n'
                 '}\n'
                 '\n'
                 '.button {\n'
                 '  @include button-base;\n'
                 '  background-color: $primary;\n'
                 '  color: $white;\n'
                 '  padding: 8px 16px;\n'
                 '\n'
                 '  &:hover { background-color: $primary-dark; }\n'
                 '  &:focus { outline: 2px solid #93c5fd; }\n'
                 '}\n'}}
EXPECTED_STARTS = {
    "adf": {(4, 22), (14, 6)},
    "databricks-workflow": {(2, 4), (8, 4)},
    "mdx": {(0, 0), (6, 0), (12, 0)},
    "css": {(3, 11)},
    "scss": {(12, 2), (15, 11), (17, 2), (18, 2)},
}

def meaningful(diff):
    assert diff.changes and not diff.is_style_only and diff.language != "binary", diff
    assert not diff.parse_errors, diff.parse_errors

def main():
    differ = SemanticDiffer()
    for language, case in CASES.items():
        diff = differ.diff_strings(case["old"], case["new"], case["filename"])
        meaningful(diff)
        assert diff.language == language, (language, diff.language)
        nodes = [change.new_node for change in diff.changes if change.new_node]
        starts = {(node.position.start_line, node.position.start_col) for node in nodes if node.position}
        assert EXPECTED_STARTS[language] <= starts, (language, starts)
        if language == "mdx":
            for label, end in [("Callout", (8, 10)), ("bash", (14, 3))]:
                node = next(node for node in nodes if node.label == label)
                assert (node.position.end_line, node.position.end_col) == end, node
        print("Verified source ranges:", language)
    for old, new, kind in [
        ("%let target = World;\n", "%let target = Other;\n", "MODIFICATION"),
        ("", "%let target = World;\n", "ADDITION"),
        ("%let target = World;\n", "", "DELETION"),
    ]:
        diff = differ.diff_strings(old, new, "main.sas")
        meaningful(diff)
        assert len(diff.changes) == 1 and diff.changes[0].change_type == kind, diff
    comment = differ.diff_strings("/* before */\n%let x = 1;", "/* after */\n%let x = 1;", "main.sas")
    assert not comment.changes and comment.is_style_only, comment
    for old in ['<Card\n title="Hello world"\n/>', '<Card title = {hello + world} />']:
        meaningful(differ.diff_strings(old, old.replace("world", "there"), "page.mdx"))
    # The normal .json route is JSON. Exercise the bundled workflow component
    # directly to check its own JSON fallback without claiming different routing.
    root = files("intentumdiff").joinpath("wasm")
    paths = [Path(str(p)) for p in root.iterdir() if p.name.endswith("databricks_parser.wasm")]
    assert len(paths) == 1, paths
    source = r'{"name":"\uD83D\uDE80","tasks":[{"task_key":"launch","notebook_task":{}}]}'
    tree = json.loads(load_plugin(paths[0], trusted=True).call_process(source, "databricks-workflow", "job.json"))
    assert "error" not in tree, tree
    assert tree["label"] == "🚀", tree
    assert tree["children"][0]["position"] == dict(start_line=0, start_col=32, end_line=0, end_col=72), tree
    print("Verified SAS statement/comment semantics, MDX prop edits and workflow JSON fallback")

if __name__ == "__main__":
    main()
