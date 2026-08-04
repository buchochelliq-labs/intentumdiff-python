"""
intentumdiff.lsp_server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

LSP server package — exposes :func:`create_server` for editor integrations.

Requires the ``lsp-server`` extra::

    pip install "intentumdiff[lsp-server]"
"""

from intentumdiff.lsp_server.server import create_server

__all__ = ["create_server"]
