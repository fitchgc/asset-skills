"""Run a skill CLI as if Pillow were not installed.

Usage: python -S tests/pil_blocker.py <skills/.../scripts/asynx.py> <args...>

A ``sys.meta_path`` finder rejects every ``PIL``/``PIL.*`` import (including the
skill's own ``scripts/vendor``) and appends each attempted module name to the file
named by ``ASX_PIL_IMPORT_LOG``, so tests can assert which commands never touch Pillow.
"""

from __future__ import annotations

import os
import runpy
import sys
from importlib.abc import MetaPathFinder
from pathlib import Path


class PillowBlocker(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        if fullname == "PIL" or fullname.startswith("PIL."):
            log = os.environ.get("ASX_PIL_IMPORT_LOG")
            if log:
                with open(log, "a", encoding="utf-8") as handle:
                    handle.write(f"{fullname}\t{' '.join(sys.argv[1:3])}\n")
            raise ModuleNotFoundError(f"No module named {fullname!r} (blocked by test guard)", name=fullname)
        return None


sys.meta_path.insert(0, PillowBlocker())
for loaded in [name for name in sys.modules if name == "PIL" or name.startswith("PIL.")]:
    del sys.modules[loaded]

script = Path(sys.argv[1]).resolve()
sys.argv = [str(script), *sys.argv[2:]]
sys.path[0] = str(script.parent)
runpy.run_path(str(script), run_name="__main__")
