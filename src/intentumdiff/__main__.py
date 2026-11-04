"""Enable `python -m intentumdiff`.

The console script alone is not enough: `python -m` is what people reach for when the
script is not on PATH (a venv they have not activated, CI, a container), and READMEs use
it constantly. 0.0.1 shipped without this module, so the invocation failed with an
unhelpful "cannot be directly executed".
"""

from intentumdiff.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
