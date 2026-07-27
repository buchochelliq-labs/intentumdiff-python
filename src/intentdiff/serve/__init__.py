"""intentdiff.serve
~~~~~~~~~~~~~~~~~~~~~~~

HTTP playground and public API for IntentDiff.

Start with::

    intentdiff serve [--host HOST] [--port PORT]

or create the ASGI app programmatically through the compatibility
implementation package::

    from intentdiff.serve import create_app
    app = create_app()
"""

from intentdiff.serve._app import create_app

__all__ = ["create_app"]
