"""
intentdiff.plugins.exceptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plugin-related exception hierarchy.
"""

from __future__ import annotations


def _fmt_fuel(n: int) -> str:
    """Format a fuel count as a compact human-readable string (376.6M, 1.2B …)."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class PluginError(Exception):
    """Base class for all plugin errors."""


class PluginNotFoundError(PluginError):
    """No plugin is registered for a given language."""

    def __init__(self, language: str, filename: str = "") -> None:
        self.language = language
        self.filename = filename
        where = f" (file: {filename!r})" if filename else ""
        super().__init__(
            f"No parser plugin found for language {language!r}{where}. "
            "Install a plugin package or use a built-in language."
        )


class PluginOutputError(PluginError):
    """A plugin returned output that failed Pydantic validation."""

    def __init__(self, plugin_id: str, detail: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(
            f"Plugin {plugin_id!r} returned invalid output: {detail}"
        )


class GrammarNotInstalledError(PluginError):
    """A Python tree-sitter grammar package is not installed."""

    def __init__(self, package: str, language: str) -> None:
        self.package = package
        self.language = language
        super().__init__(
            f"Tree Sitter library '{package}' is not available for language '{language}'. "
            f"To install it run: pip install {package.replace('_', '-')}"
        )


class PluginFuelExhausted(PluginError):
    """A plugin ran out of Wasm fuel (infinite-loop guard)."""

    def __init__(self, plugin_id: str, fuel: int, context: str = "") -> None:
        self.plugin_id = plugin_id
        self.fuel = fuel
        ctx = f" — {context}" if context else ""
        super().__init__(
            f"FUEL_EXCEEDED: {_fmt_fuel(fuel)}{ctx} "
            "(pass --fuel inf or increase DiffConfig.plugin_fuel to remove the cap)"
        )


class PluginSandboxViolation(PluginError):
    """
    A plugin attempted a capability it was not granted (filesystem, network, …).

    Raised when wasmtime traps on an unsatisfied import.
    """

    def __init__(self, plugin_id: str, detail: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(
            f"Plugin {plugin_id!r} attempted a sandboxed capability: {detail}"
        )


class PluginLoadError(PluginError):
    """Failed to instantiate a .wasm plugin."""

    def __init__(self, wasm_path: str, detail: str) -> None:
        self.wasm_path = wasm_path
        super().__init__(f"Failed to load plugin from {wasm_path!r}: {detail}")
