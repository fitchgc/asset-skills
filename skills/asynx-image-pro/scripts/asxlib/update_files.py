"""Local install receipt (`.asx-install.json`) written by the repository installer.

The receipt only records which files a local `install.py` copy contained. It does not drive updates:
registry installs are versioned and replaced by the Plaiers installer (see updates.py, ADR-0007).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .errors import AsxError

RECEIPT = ".asx-install.json"


def read_version(root: Path) -> str | None:
    try:
        content = (root / "scripts" / "asxlib" / "constants.py").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.search(r"^VERSION\s*=\s*[\"\']([^\"\']+)[\"\']", content, re.MULTILINE)
    return match.group(1) if match else None


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".asx-", delete=False
        ) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def inventory(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts:
            continue
        if relative.name.startswith(".") and len(relative.parts) == 1:
            continue  # receipt, credential context and other local metadata
        if path.name == ".DS_Store":
            continue
        if path.is_symlink():
            raise AsxError("安装目录包含链接，无法记录安装清单", code="unmanaged_installation")
        if path.is_file():
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def record_install(root: Path, *, files: dict[str, str] | None = None) -> None:
    version = read_version(root)
    if version is None:
        raise AsxError("安装包缺少版本信息", code="invalid_install_package")
    write_json(
        root / RECEIPT,
        {"schema": 1, "version": version, "files": inventory(root) if files is None else files},
    )
