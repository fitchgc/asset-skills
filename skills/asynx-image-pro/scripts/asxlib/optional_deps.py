"""Optional dependencies that the generate → query → download flow must not need.

Pillow is only required for local image decoding: reference-image and mask
validation, local post-processing and the workbench. Callers import it through
``import_pillow`` so a missing or incompatible install becomes a structured
``dependency_missing`` error raised before any request is submitted.
"""

from __future__ import annotations

import importlib
import shlex
import sys
from pathlib import Path
from typing import Any

from .errors import AsxError

PILLOW = "Pillow"


def prepare_command() -> str:
    """Command that prepares the shared optional runtime (needs user consent: it downloads packages)."""
    script = Path(__file__).resolve().parents[1] / "asynx.py"
    return shlex.join([sys.executable or "python3", str(script), "runtime", "prepare"])


def dependency_missing(feature: str, *, cause: BaseException | None = None) -> AsxError:
    details: dict[str, Any] = {
        "dependency": PILLOW,
        "feature": feature,
        "runtime_status": "not_prepared",
        "prepare_command": prepare_command(),
        "requires_user_consent": True,
    }
    if cause is not None:
        details["cause"] = f"{type(cause).__name__}: {cause}"
    return AsxError(
        f"{feature}需要可选依赖 Pillow，当前未准备。请先征得用户同意，再运行 prepare_command 准备共享运行环境；"
        "本次请求未提交。",
        code="dependency_missing",
        details=details,
    )


def import_pillow(*modules: str, feature: str) -> tuple[Any, ...]:
    """Import ``PIL.<module>`` for each name, or raise ``dependency_missing``."""
    try:
        return tuple(importlib.import_module(f"PIL.{name}" if name else "PIL") for name in modules)
    except (ImportError, OSError, AttributeError) as exc:
        raise dependency_missing(feature, cause=exc) from exc


def optional_runtime_status() -> dict[str, Any]:
    """Doctor view: a missing optional runtime is ``not_prepared``, never a failure."""
    try:
        pil = importlib.import_module("PIL")
        importlib.import_module("PIL.Image")
    except (ImportError, OSError, AttributeError):
        return {
            "dependency": PILLOW,
            "status": "not_prepared",
            "version": None,
            "prepare_command": prepare_command(),
            "message": "可选运行环境未准备：生成、查询、下载不受影响；参考图、Mask 与本地图片处理需先准备。",
        }
    return {
        "dependency": PILLOW,
        "status": "ready",
        "version": getattr(pil, "__version__", "unknown"),
        "path": getattr(pil, "__file__", None),
    }
