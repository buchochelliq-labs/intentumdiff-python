"""Actual diffs: both public consumers must receive configured identities."""
import json
from pathlib import Path
from intentumdiff.rust_core import _c_abi_call


def test_native_schema_identity_metadata_and_real_value_edit(tmp_path, monkeypatch):
    schemas=tmp_path/'.intentumdiff'/'schemas'
    schemas.mkdir(parents=True)
    (schemas/'acme.yaml').write_text('language_id: acme\nmatch:\n  filename_patterns: ["tasks.json"]\nidentity_fields: [task_key]\n')
    monkeypatch.chdir(tmp_path)
    wasm=Path(__file__).resolve().parents[2]/'src'/'intentumdiff'/'wasm'
    before='{"tasks":[{"task_key":"a","value":1},{"task_key":"b","value":2}]}'
    after='{"tasks":[{"task_key":"b","value":2},{"task_key":"a","value":3}]}'
    result=_c_abi_call('live_diff_contents',str(tmp_path),'tasks.json',before,after,'{}',str(wasm))
    assert 'diff' in result, result
    diff=result['diff']
    assert diff['metadata']['schema']['identity_fields']==['task_key']
    assert diff['has_semantic_changes'] and not diff['is_style_only']
    assert any(c['change_type']=='MODIFICATION' for c in diff['changes'])


def test_native_registered_xml_preserves_only_actual_price_edit(tmp_path, monkeypatch):
    schemas=tmp_path/'.intentumdiff'/'schemas'
    schemas.mkdir(parents=True)
    (schemas/'catalog.yaml').write_text('language_id: acme-catalog\nmatch:\n  filename_patterns: ["*.catalog.xml"]\n  root_element: catalog\nkeyed_elements:\n  book: [isbn]\n')
    monkeypatch.chdir(tmp_path)
    _c_abi_call('register_user_xml_dialects', [])
    wasm=Path(__file__).resolve().parents[2]/'src'/'intentumdiff'/'wasm'
    before='<catalog><book><isbn>978-0</isbn><price>10.99</price></book><book><isbn>978-1</isbn><price>9.99</price></book></catalog>\n'
    after='<catalog><book><isbn>978-1</isbn><price>9.99</price></book><book><isbn>978-0</isbn><price>12.99</price></book></catalog>\n'
    result=_c_abi_call('live_diff_contents',str(tmp_path),'books.catalog.xml',before,after,'{}',str(wasm))
    assert 'diff' in result, result
    changes=result['diff']['changes']
    assert len(changes)==1, changes
    assert (changes[0]['old_node']['label'],changes[0]['new_node']['label'])==('10.99','12.99')


def test_native_retains_rejected_descriptor_diagnostics(tmp_path, monkeypatch):
    schemas=tmp_path/'.intentumdiff'/'schemas'
    schemas.mkdir(parents=True)
    (schemas/'bad.yaml').write_text('language_id: broken\n')
    monkeypatch.chdir(tmp_path)
    wasm=Path(__file__).resolve().parents[2]/'src'/'intentumdiff'/'wasm'
    result=_c_abi_call('live_diff_contents',str(tmp_path),'plain.json','{"x":1}','{"x":2}','{}',str(wasm))
    assert result['diff']['metadata']['schema']['errors']
