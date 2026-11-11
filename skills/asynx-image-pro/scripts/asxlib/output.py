from __future__ import annotations

import json
import sys
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def log(*values: Any) -> None:
    print(*values, file=sys.stderr)
