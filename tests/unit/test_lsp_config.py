"""Unit tests for :mod:`intentumdiff.lsp.config`."""

from __future__ import annotations

import pytest

from intentumdiff.lsp.config import LspServerConfig


class TestLspServerConfigFromString:
    def test_localhost_port(self) -> None:
        cfg = LspServerConfig.from_string("localhost:2087")
        assert cfg.host == "localhost"
        assert cfg.port == 2087

    def test_ip_address(self) -> None:
        cfg = LspServerConfig.from_string("127.0.0.1:9999")
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9999

    def test_whitespace_stripped(self) -> None:
        cfg = LspServerConfig.from_string("  localhost:2088  ")
        assert cfg.host == "localhost"
        assert cfg.port == 2088

    def test_missing_port_raises(self) -> None:
        with pytest.raises(ValueError, match="Expected 'host:port'"):
            LspServerConfig.from_string("localhost")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            LspServerConfig.from_string("")

    def test_non_numeric_port_raises(self) -> None:
        with pytest.raises(ValueError):
            LspServerConfig.from_string("localhost:abc")

    def test_double_colon_raises(self) -> None:
        with pytest.raises(ValueError):
            LspServerConfig.from_string("localhost:2087:extra")

    def test_str_roundtrip(self) -> None:
        cfg = LspServerConfig.from_string("localhost:2087")
        assert str(cfg) == "localhost:2087"


class TestLspServerConfigDirect:
    def test_defaults_host(self) -> None:
        cfg = LspServerConfig(port=2087)
        assert cfg.host == "localhost"

    def test_port_zero_raises(self) -> None:
        with pytest.raises(Exception):
            LspServerConfig(port=0)

    def test_port_too_large_raises(self) -> None:
        with pytest.raises(Exception):
            LspServerConfig(port=65536)

    def test_frozen(self) -> None:
        cfg = LspServerConfig(port=2087)
        with pytest.raises(Exception):
            cfg.port = 9999  # type: ignore[misc]

    def test_equality(self) -> None:
        a = LspServerConfig(host="localhost", port=2087)
        b = LspServerConfig.from_string("localhost:2087")
        assert a == b

    def test_str_custom_host(self) -> None:
        cfg = LspServerConfig(host="myserver", port=1234)
        assert str(cfg) == "myserver:1234"
