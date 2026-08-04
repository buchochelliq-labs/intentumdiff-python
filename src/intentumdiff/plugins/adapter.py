"""
intentumdiff.plugins.adapter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Adapts a ``LoadedPlugin`` to a typed Python interface, handling:

- JSON (de)serialisation of inputs and outputs
- Pydantic validation of plugin output (``SemanticNode`` trees)
- Fuel-exhaustion and sandbox-violation error mapping

Two concrete adapters are provided:
  ``ParserAdapter``   — wraps a parser plugin
  ``RendererAdapter`` — wraps a renderer plugin
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from intentumdiff.core.models import LanguagePluginInfo, SemanticNode
from intentumdiff.plugins.exceptions import PluginOutputError
from intentumdiff.plugins.language_metadata import fallback_language_info
from intentumdiff.plugins.loader import LoadedPlugin

logger = logging.getLogger(__name__)

_MAX_DISPLAY_CHARS = 128
_MAX_SHORT_NAME_CHARS = 64
_MAX_MONACO_CHARS = 64
_MAX_FILENAME_CHARS = 128
_MAX_EXTENSION_CHARS = 32
_MAX_EXTENSIONS = 32
_MAX_AUTHOR_CHARS = 128
_MAX_VERSION_CHARS = 64
_MAX_LAST_UPDATED_CHARS = 32

_MONACO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+.-]{0,63}$")
_EXTENSION_RE = re.compile(
    r"^(?:\.[A-Za-z0-9][A-Za-z0-9_+.-]{0,31}|[A-Za-z0-9][A-Za-z0-9_+.-]{0,31})$"
)
_LAST_UPDATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _as_clean_text(value: object, fallback: str, *, max_chars: int) -> str:
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if not text:
        return fallback
    if len(text) > max_chars:
        return fallback
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return fallback
    return text


def _as_clean_monaco(value: object, fallback: str) -> str:
    text = _as_clean_text(value, fallback, max_chars=_MAX_MONACO_CHARS)
    if not _MONACO_RE.fullmatch(text):
        return fallback
    return text


def _as_clean_filename(value: object, fallback: str) -> str:
    text = _as_clean_text(value, fallback, max_chars=_MAX_FILENAME_CHARS)
    if (
        text in {".", ".."}
        or ".." in text
        or "/" in text
        or "\\" in text
        or ":" in text
    ):
        return fallback
    return text


def _as_clean_extensions(value: object, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:_MAX_EXTENSIONS]:
        text = _as_clean_text(item, "", max_chars=_MAX_EXTENSION_CHARS)
        if not text:
            continue
        if ".." in text or "/" in text or "\\" in text or ":" in text:
            continue
        if not _EXTENSION_RE.fullmatch(text):
            continue
        if text not in seen:
            result.append(text)
            seen.add(text)
    return result or fallback


def _as_clean_last_updated(value: object, fallback: str) -> str:
    text = _as_clean_text(value, fallback, max_chars=_MAX_LAST_UPDATED_CHARS)
    if text != fallback and not _LAST_UPDATED_RE.fullmatch(text):
        return fallback
    return text


class ParserAdapter:
    """Typed wrapper around a loaded parser plugin."""

    def __init__(self, plugin: LoadedPlugin) -> None:
        self._plugin = plugin
        self._grammar_id: str | None = None
        self._language_ids: list[str] | None = None
        self._language_info: list[LanguagePluginInfo] | None = None
        self._trivia_types: list[str] | None = None
        self.plugin_id: str = ""
        self.package_name: str = ""
        self.package_version: str = ""
        self.author: str = ""
        self.provenance: str = ""  # set by registry: "<package> <version>"

    @property
    def grammar_id(self) -> str:
        if self._grammar_id is None:
            self._grammar_id = self._plugin.call_grammar_id()
        return self._grammar_id

    @property
    def language_ids(self) -> list[str]:
        if self._language_ids is None:
            self._language_ids = self._plugin.call_language_ids()
        return self._language_ids

    @property
    def trivia_node_types(self) -> list[str]:
        if self._trivia_types is None:
            self._trivia_types = self._plugin.call_trivia_node_types()
        return self._trivia_types

    @property
    def wasm_path(self) -> str:
        """Filesystem path of the backing ``.wasm`` file."""
        return self._plugin.wasm_path

    @property
    def is_trusted(self) -> bool:
        return self._plugin.trusted

    @property
    def priority(self) -> int:
        return self._plugin.call_priority("parser")

    @property
    def parser_mode(self) -> str:
        return self._plugin.call_parser_mode()

    @property
    def language_info(self) -> list[LanguagePluginInfo]:
        """Return plugin-owned language metadata with host-owned fields attached."""
        if self._language_info is None:
            self._language_info = self._load_language_info()
        return self._language_info

    def _fallback_language_info(self, language_id: str) -> LanguagePluginInfo:
        return fallback_language_info(
            language_id,
            plugin_id=self.plugin_id or self.grammar_id,
            grammar_id=self.grammar_id,
            priority=self.priority,
            is_trusted=self.is_trusted,
            provenance=self.provenance,
            author=self.author,
            plugin_version=self.package_version,
        )

    def _load_language_info(self) -> list[LanguagePluginInfo]:
        by_language = {
            lang: self._fallback_language_info(lang)
            for lang in self.language_ids
            if isinstance(lang, str) and lang
        }
        try:
            records = self._plugin.call_language_info()
        except Exception as exc:
            logger.debug(
                "Parser plugin %r language-info unavailable: %s",
                self.plugin_id or self.grammar_id,
                exc,
            )
            return list(by_language.values())

        declared_languages = set(by_language)
        for raw in records:
            if not isinstance(raw, dict):
                continue
            lang = str(raw.get("language_id") or "")
            if not lang or lang not in declared_languages:
                logger.debug(
                    "Parser plugin %r ignored unclaimed language-info record %r",
                    self.plugin_id or self.grammar_id,
                    lang,
                )
                continue
            base = by_language.get(lang) or self._fallback_language_info(lang)
            by_language[lang] = LanguagePluginInfo(
                language_id=lang,
                language_name=_as_clean_text(
                    raw.get("language_name"),
                    base.language_name,
                    max_chars=_MAX_DISPLAY_CHARS,
                ),
                language_short_name=_as_clean_text(
                    raw.get("language_short_name"),
                    base.language_short_name,
                    max_chars=_MAX_SHORT_NAME_CHARS,
                ),
                monaco_language=_as_clean_monaco(
                    raw.get("monaco_language"),
                    base.monaco_language,
                ),
                default_filename=_as_clean_filename(
                    raw.get("default_filename"),
                    base.default_filename,
                ),
                language_file_extensions=_as_clean_extensions(
                    raw.get("language_file_extensions", []),
                    base.language_file_extensions,
                ),
                author=_as_clean_text(
                    raw.get("author"),
                    base.author,
                    max_chars=_MAX_AUTHOR_CHARS,
                ),
                plugin_version=_as_clean_text(
                    raw.get("plugin_version"),
                    base.plugin_version,
                    max_chars=_MAX_VERSION_CHARS,
                ),
                last_updated=_as_clean_last_updated(
                    raw.get("last_updated"),
                    base.last_updated,
                ),
                plugin_id=self.plugin_id or self.grammar_id,
                grammar_id=self.grammar_id,
                priority=self.priority,
                is_trusted=self.is_trusted,
                provenance=self.provenance,
            )
        return sorted(by_language.values(), key=lambda info: info.language_id)

    def can_parse(self, content: str) -> bool:
        """Return ``True`` if this parser claims to handle ``content``.

        Passes an empty filename so the plugin must rely on content-sniffing
        rather than file-extension heuristics.  Only the first 4096 bytes are
        inspected.
        """
        return bool(self._plugin.call_detect_language("", content[:4096]))

    def detect_language(self, filename: str, content: str) -> str:
        return self._plugin.call_detect_language(filename, content)

    def preprocess_source(self, source: str) -> str:
        return self._plugin.call_preprocess_source(source)

    def playground_example(self, language: str) -> dict[str, str] | None:
        """Return the ``{"old": ..., "new": ...}`` example pair for *language*.

        Returns ``None`` if the plugin raises or returns empty strings.
        """
        try:
            ex = self._plugin.call_example(language)
        except Exception:
            return None
        if ex.get("old") and ex.get("new"):
            return ex
        return None

    def process(
        self, input_: str, language: str, filename: str, fuel: int | None = None
    ) -> SemanticNode:
        """
        Call the plugin's ``process`` function and validate the returned JSON.

        Raises
        ------
        PluginOutputError
            If the plugin returns malformed JSON or a JSON object with an
            ``"error"`` key, or if Pydantic validation fails.
        """
        raw = self._plugin.call_process(input_, language, filename, fuel=fuel)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PluginOutputError(self.grammar_id, f"invalid JSON: {exc}") from exc

        if isinstance(data, dict) and "error" in data:
            raise PluginOutputError(self.grammar_id, data["error"])

        try:
            return SemanticNode.model_validate(data)
        except ValidationError as exc:
            raise PluginOutputError(
                self.grammar_id,
                f"SemanticNode validation failed: {exc}",
            ) from exc

    def drain_telemetry(self) -> list[dict[str, Any]]:
        return self._plugin.drain_telemetry()


class RendererAdapter:
    """Typed wrapper around a loaded renderer plugin."""

    def __init__(self, plugin: LoadedPlugin) -> None:
        self._plugin = plugin
        self._format_name: str | None = None
        self.provenance: str = ""  # set by registry: "<package> <version>"

    @property
    def format_name(self) -> str:
        if self._format_name is None:
            self._format_name = self._plugin.call_format_name()
        return self._format_name

    @property
    def priority(self) -> int:
        return self._plugin.call_priority("renderer")

    @property
    def supported_options(self) -> list[str]:
        return self._plugin.call_supported_options()

    def render(self, diff_json: str, fuel: int | None = None) -> str:
        return self._plugin.call_render(diff_json, fuel=fuel)

    def drain_telemetry(self) -> list[dict[str, Any]]:
        return self._plugin.drain_telemetry()


class EnricherAdapter:
    """Typed wrapper around a loaded enricher plugin."""

    def __init__(self, plugin: LoadedPlugin) -> None:
        self._plugin = plugin
        self._language_ids: list[str] | None = None
        self.provenance: str = ""  # set by registry: "<package> <version>"

    @property
    def language_ids(self) -> list[str]:
        if self._language_ids is None:
            self._language_ids = self._plugin.call_language_ids()
        return self._language_ids

    @property
    def priority(self) -> int:
        return self._plugin.call_priority()

    def enrich(
        self, tree_json: str, raw_source: str, language: str, filename: str, fuel: int | None = None
    ) -> str:
        return self._plugin.call_enrich(tree_json, raw_source, language, filename, fuel=fuel)

    def drain_telemetry(self) -> list[dict[str, Any]]:
        return self._plugin.drain_telemetry()


class DiffAnalyzerAdapter:
    """Typed wrapper around a loaded diff-analyzer plugin."""

    def __init__(self, plugin: "LoadedPlugin") -> None:
        self._plugin = plugin
        self._language_ids: list[str] | None = None
        self.provenance: str = ""

    @property
    def language_ids(self) -> list[str]:
        if self._language_ids is None:
            self._language_ids = self._plugin.call_language_ids()
        return self._language_ids

    @property
    def priority(self) -> int:
        return self._plugin.call_priority()

    def analyze_diff(
        self,
        diff_json: str,
        language: str,
        filename: str,
        fuel: int | None = None,
    ) -> str:
        return self._plugin.call_analyze_diff(diff_json, language, filename, fuel=fuel)

    def drain_telemetry(self) -> list[dict[str, Any]]:
        return self._plugin.drain_telemetry()
