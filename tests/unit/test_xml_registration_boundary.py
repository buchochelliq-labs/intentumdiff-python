"""XML registration failures must not become successful unconfigured reviews."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from intentumdiff import rust_core


@pytest.fixture(autouse=True)
def reset_registration(monkeypatch):
    monkeypatch.setattr(rust_core, "_registered_xml_dialects_fingerprint", None)


@pytest.mark.parametrize("failure", [RuntimeError("native registration failed"), ValueError("invalid dialect")])
def test_registration_failure_propagates_and_can_be_retried(monkeypatch, failure):
    register = Mock(side_effect=failure)
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(register_user_xml_dialects_json=register))
    with pytest.raises(type(failure), match=str(failure)):
        rust_core.try_register_user_xml_dialects([])
    assert rust_core._registered_xml_dialects_fingerprint is None
    register.side_effect = None
    assert rust_core.try_register_user_xml_dialects([]) is True
    assert register.call_count == 2


def test_missing_registration_handler_fails(monkeypatch):
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace())
    with pytest.raises(RuntimeError, match="register_user_xml_dialects_json"):
        rust_core.try_register_user_xml_dialects([])


def test_missing_backend_fails(monkeypatch):
    monkeypatch.setattr(rust_core, "_load_backend", Mock(side_effect=RuntimeError("native backend unavailable")))
    with pytest.raises(RuntimeError, match="native backend unavailable"):
        rust_core.try_register_user_xml_dialects([])


def test_successful_registration_is_idempotent_and_empty_payload_clears(monkeypatch):
    register = Mock()
    monkeypatch.setattr(rust_core, "_load_backend", lambda: SimpleNamespace(register_user_xml_dialects_json=register))
    dialects = [{"name": "example"}]
    assert rust_core.try_register_user_xml_dialects(dialects) is True
    assert rust_core.try_register_user_xml_dialects(dialects) is True
    assert register.call_count == 1
    assert rust_core.try_register_user_xml_dialects([]) is True
    assert register.call_count == 2
    assert register.call_args.args == ("[]",)


def test_public_xml_diff_propagates_registration_failure_even_for_empty_config(monkeypatch):
    from intentumdiff import SemanticDiffer
    import intentumdiff.differ as differ_module

    registry = Mock()
    registry.detect_parser.return_value = (SimpleNamespace(
        grammar_id="xml", parser_mode="full-parse"
    ), "xml")
    monkeypatch.setattr(differ_module, "_infer_file_lifecycle", lambda *args: "modified")
    monkeypatch.setattr(differ_module, "resolve_schema", lambda **kwargs: None)
    monkeypatch.setattr(differ_module, "load_user_schema_profiles", lambda: ((), ()))
    register = Mock(side_effect=RuntimeError("native XML registration sentinel"))
    monkeypatch.setattr(
        rust_core, "_load_backend",
        lambda: SimpleNamespace(register_user_xml_dialects_json=register),
    )
    with pytest.raises(RuntimeError, match="native XML registration sentinel"):
        SemanticDiffer(registry=registry).diff_strings(
            "<x>1</x>", "<x>2</x>", "x.xml", "xml"
        )
    register.assert_called_once_with("[]")
