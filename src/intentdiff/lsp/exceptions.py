"""
intentdiff.lsp.exceptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Non-fatal LSP warning exceptions.

Both subclass ``Exception`` directly rather than a library-wide base so that
callers can catch them without importing any other IntentDiff symbol.
They are treated as warnings in the pipeline — a file whose LSP enrichment
fails is diffed without type information rather than aborting.
"""

from __future__ import annotations


class LspConnectionError(Exception):
    """Raised when the TCP connection to the LSP server cannot be established.

    This is non-fatal: the diff pipeline continues without type enrichment.
    """


class LspTimeoutError(Exception):
    """Raised when an LSP request does not receive a response within the timeout.

    This is non-fatal: the affected node is left with ``type_info=None``.
    """
