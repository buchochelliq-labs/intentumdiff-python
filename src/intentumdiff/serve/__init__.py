"""intentumdiff.serve
~~~~~~~~~~~~~~~~~~~~~~~

HTTP playground and public API for IntentumDiff.

Start with::

    intentumdiff serve [--host HOST] [--port PORT]

or create the ASGI app programmatically through the compatibility
implementation package::

    from intentumdiff.serve import create_app
    app = create_app()
"""

from intentumdiff.serve._app import create_app

__all__ = ["create_app"]
