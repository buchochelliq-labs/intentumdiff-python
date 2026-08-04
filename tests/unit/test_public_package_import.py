from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_public_package_import_does_not_require_pytest() -> None:
    script = textwrap.dedent(
        """
        import builtins

        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "pytest" or name.startswith("pytest."):
                raise ModuleNotFoundError("blocked pytest import")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = guarded_import

        import intentumdiff
        from intentumdiff import PluginTestHarness

        print(intentumdiff.__version__)
        print(PluginTestHarness.__name__)
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("src").resolve())

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["0.0.1", "PluginTestHarness"]
