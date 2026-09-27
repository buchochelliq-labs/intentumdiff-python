"""The parity harness must preserve UTF-8 on Windows locale defaults."""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

from intentumdiff import review_text


def test_parity_harness_uses_utf8_for_files_and_native_transport(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / 'scripts/check_migration_parity.py'
    harness = runpy.run_path(str(script))
    core = tmp_path / 'core'
    fixtures = core / 'tests/fixtures/migration'
    fixtures.mkdir(parents=True)
    corpus = core / 'tests/corpus'
    corpus.mkdir()
    case = {'id': 'unicode', 'old': 'été 😀\n', 'new': 'hiver\n',
            'required_labels': ['été 😀', 'hiver'], 'exact_changes': 1}
    (fixtures / 'markdown.json').write_text(json.dumps([case], ensure_ascii=False), encoding='utf-8')
    (fixtures / 'utilities.json').write_text('[]', encoding='utf-8')
    (corpus / 'schema_custom_identity.json').write_text(
        json.dumps(dict(case, filename='doc.txt', descriptor={}), ensure_ascii=False), encoding='utf-8')
    evidence = tmp_path / 'evidence.json'
    original_read = Path.read_text
    original_write = Path.write_text

    def windows_locale_read(self, encoding=None, **kwargs):
        return original_read(self, encoding=encoding or 'cp1252', **kwargs)

    def windows_locale_write(self, data, encoding=None, **kwargs):
        return original_write(self, data, encoding=encoding or 'cp1252', **kwargs)

    def native_transport(command, *, input, encoding=None, **kwargs):
        assert encoding == 'utf-8'
        request = json.loads(input)
        assert request['old'] == case['old']
        diff = review_text(request['old'], request['new'], filename=request['filename'])
        return SimpleNamespace(stdout=diff.model_dump_json())

    class Differ:
        def diff_strings(self, old, new, filename):
            return review_text(old, new, filename=filename)

        def close(self):
            pass

    monkeypatch.setattr(Path, 'read_text', windows_locale_read)
    monkeypatch.setattr(Path, 'write_text', windows_locale_write)
    monkeypatch.setattr(harness['subprocess'], 'run', native_transport)
    harness['main'].__globals__['SemanticDiffer'] = Differ
    monkeypatch.setattr('sys.argv', [str(script), '--core-dir', str(core), '--native', 'probe',
                                    '--wasm-dir', str(tmp_path), '--evidence', str(evidence)])
    harness['main']()
    records = json.loads(evidence.read_text(encoding='utf-8'))
    assert len(records) == 2
    assert all(record['old'] == case['old'] for record in records)
