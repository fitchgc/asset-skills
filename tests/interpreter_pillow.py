"""Expose the interpreter's own Pillow to test processes started with ``python -S``.

Skills no longer bundle Pillow. Tests that build image fixtures (or exercise
Pillow-backed paths in-process) use whatever Pillow this interpreter has in its
normal site-packages; the directory is appended so the repository's ``tests``
package still wins.
"""

from __future__ import annotations

import subprocess
import sys


def add_interpreter_pillow() -> bool:
    result = subprocess.run(
        [sys.executable, "-c", "import os, PIL; print(os.path.dirname(os.path.dirname(PIL.__file__)))"],
        capture_output=True,
        text=True,
        check=False,
    )
    path = result.stdout.strip()
    if result.returncode or not path:
        return False
    if path not in sys.path:
        sys.path.append(path)
    return True
