"""Optional dependencies that the generate → query → download flow must not need.

Pillow is only required for local image decoding: reference-image and mask
validation, local post-processing and the workbench. Callers import it through
``import_pillow`` / ``pillow_or_none`` so that:

- a prepared shared runtime (``runtime_env``) is verified and put on ``sys.path``
  first;
- an invalid runtime is reported (``runtime_invalid``) and never used;
- otherwise a Pillow already importable by this interpreter is used as is;
- if none is available, ``dependency_missing`` is raised before any submission.
"""

from __future__ import annotations

import importlib
import shlex
import sys
from pathlib import Path
from typing import Any

from . import runtime_env
from .errors import AsxError

PILLOW = "Pillow"
_IMPORT_ERRORS = (ImportError, OSError, AttributeError)


def runtime_command(action: str) -> str:
    """CLI command for ``runtime <action>`` of this skill."""
    script = Path(__file__).resolve().parents[1] / "asynx.py"
    return shlex.join([sys.executable or "python3", str(script), "runtime", action])


def prepare_command() -> str:
    """Prepares the shared runtime; downloads packages, so it needs user consent."""
    return runtime_command("prepare")


def _runtime_invalid(state: dict[str, Any], feature: str) -> AsxError:
    return AsxError(
        f"{feature}需要的共享运行环境校验失败（{state.get('reason')}），已停用。"
        "请先征得用户同意，再运行 prepare_command 重新准备；本次请求未提交。",
        code="runtime_invalid",
        details={
            "dependency": PILLOW,
            "feature": feature,
            "runtime": state,
            "prepare_command": prepare_command(),
            "remove_command": runtime_command("remove"),
            "requires_user_consent": True,
        },
    )


def dependency_missing(
    feature: str, *, cause: BaseException | None = None, state: dict[str, Any] | None = None
) -> AsxError:
    status = (state or {}).get("status", "not_prepared")
    details: dict[str, Any] = {
        "dependency": PILLOW,
        "feature": feature,
        "runtime_status": status,
        "requires_user_consent": True,
    }
    if status == "unsupported":
        details["runtime"] = state
        message = f"{feature}需要可选依赖 Pillow，但当前平台或 Python 没有锁定的运行环境；本次请求未提交。"
    else:
        details["prepare_command"] = prepare_command()
        message = (
            f"{feature}需要可选依赖 Pillow，当前未准备。请先征得用户同意，再运行 prepare_command "
            "准备共享运行环境；工作台可点击右上角账号栏 → 检查本地环境 → 安装共享 Pillow。本次请求未提交。"
        )
    if cause is not None:
        details["cause"] = f"{type(cause).__name__}: {cause}"
    return AsxError(message, code="dependency_missing", details=details)


def import_pillow(*modules: str, feature: str) -> tuple[Any, ...]:
    """Import ``PIL.<module>`` for each name (``""`` is ``PIL``), or raise a structured error."""
    state = runtime_env.activate()
    if state["status"] == "invalid":
        raise _runtime_invalid(state, feature)
    try:
        return tuple(importlib.import_module(f"PIL.{name}" if name else "PIL") for name in modules)
    except _IMPORT_ERRORS as exc:
        raise dependency_missing(feature, cause=exc, state=state) from exc


def pillow_or_none() -> Any | None:
    """``PIL.Image`` for optional work (result metadata); ``None`` when unavailable."""
    try:
        (image,) = import_pillow("Image", feature="结果图片元数据")
    except AsxError:
        return None
    return image


def optional_runtime_status() -> dict[str, Any]:
    """Doctor view: an unprepared optional runtime is a normal state, never a failure."""
    state = runtime_env.inspect_runtime()
    result: dict[str, Any] = {"dependency": PILLOW, **state}
    if state["status"] != "ready":
        try:
            pil = importlib.import_module("PIL")
            importlib.import_module("PIL.Image")
            result["interpreter_pillow"] = getattr(pil, "__version__", "unknown")
        except _IMPORT_ERRORS:
            result["interpreter_pillow"] = None
    if state["status"] in {"not_prepared", "invalid"}:
        result["prepare_command"] = prepare_command()
        result["message"] = (
            "共享运行环境未准备：生成、查询、下载不受影响；参考图、Mask 与本地图片处理需先准备（需征得用户同意）。"
            if state["status"] == "not_prepared"
            else "共享运行环境校验失败，已停用；重新运行 prepare_command 会整体替换。"
        )
    return result
