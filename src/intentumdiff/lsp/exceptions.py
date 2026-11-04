"""
intentumdiff.lsp.exceptions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

LSP transport exceptions used to report failures through the public indexer.

Both subclass ``Exception`` directly so callers can distinguish connection
failures and timeouts. A requested type-enrichment failure must not be
reported as a successfully completed bare semantic index.
"""

from __future__ import annotations


class LspConnectionError(Exception):
    """Raised when the TCP connection to the LSP server cannot be established.

    Type enrichment cannot certify the file while connection is unavailable.
    """


class LspTimeoutError(Exception):
    """Raised when an LSP request does not receive a response within the timeout.

    The failing hover request is not silently treated as unknown type.
    """
