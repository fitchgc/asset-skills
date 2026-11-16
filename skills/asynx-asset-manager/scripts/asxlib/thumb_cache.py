"""Out-of-project thumbnail cache for the Asset Manager workbench.

Thumbnails (JPEG) for video / 3D assets are rendered by the browser and uploaded
to the workbench server, which stores them in a per-user cache directory that
lives outside any project workspace. Deleting the directory is always safe.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_THUMB_BYTES = 512 * 1024
MAX_CACHE_BYTES = 200 * 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"


def cache_dir() -> Path:
    override = os.environ.get("ASYNX_CACHE_DIR")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "asynx"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "asynx"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "asynx"
    return base / "thumbs"


def thumb_key(source: Path) -> str:
    st = source.stat()
    raw = f"{source.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def thumb_path(source: Path) -> Path:
    return cache_dir() / f"{thumb_key(source)}.jpg"


def read_thumb(source: Path) -> tuple[bytes, str] | None:
    target = thumb_path(source)
    try:
        data = target.read_bytes()
        os.utime(target)  # LRU touch
    except OSError:
        return None
    return data, target.stem


def write_thumb(source: Path, data: bytes) -> None:
    if not data.startswith(_JPEG_MAGIC):
        raise ValueError("thumbnail must be JPEG")
    if len(data) > MAX_THUMB_BYTES:
        raise ValueError("thumbnail too large")
    target = thumb_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _prune()


def _entries() -> list[tuple[Path, os.stat_result]]:
    out = []
    root = cache_dir()
    if not root.is_dir():
        return out
    for p in root.glob("*.jpg"):
        try:
            out.append((p, p.stat()))
        except OSError:
            pass
    return out


def _prune(limit: int = MAX_CACHE_BYTES) -> None:
    entries = _entries()
    total = sum(st.st_size for _, st in entries)
    if total <= limit:
        return
    for p, st in sorted(entries, key=lambda e: e[1].st_mtime):
        try:
            p.unlink()
        except OSError:
            continue
        total -= st.st_size
        if total <= limit * 0.8:
            break


def stats() -> dict[str, Any]:
    entries = _entries()
    return {
        "dir": str(cache_dir()),
        "count": len(entries),
        "bytes": sum(st.st_size for _, st in entries),
        "limit_bytes": MAX_CACHE_BYTES,
    }


def clear() -> dict[str, Any]:
    removed = 0
    for p, _ in _entries():
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return {"removed": removed, **stats()}
