"""Read downloaded image metadata for delivery without another Agent tool call."""

from __future__ import annotations

import importlib
import math
import re
import warnings
from pathlib import Path
from typing import Any

from .errors import AsxError
from .optional_deps import prepare_command
from .reference_media import _parse_basic_image_header


def requested_aspect_ratio(body: Any) -> str | None:
    task_input = body.get("input") if isinstance(body, dict) else None
    ratio = task_input.get("aspect_ratio") if isinstance(task_input, dict) else None
    return ratio if isinstance(ratio, str) else None


_HEADER_READ_LIMIT = 2 * 1024 * 1024
_HEADER_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}


def _pillow_metadata(image_module: Any, path: Path) -> tuple[int, int, str]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", image_module.DecompressionBombWarning)
        with image_module.open(path) as source:
            width, height = source.size
            # Respect display orientation without modifying the downloaded file.
            if source.getexif().get(274) in {5, 6, 7, 8}:
                width, height = height, width
            return width, height, str(source.format or "").lower()


def _header_metadata(path: Path) -> tuple[int, int, str]:
    """Without Pillow: read size and format from the file header; EXIF orientation is not applied."""
    with path.open("rb") as handle:
        data = handle.read(_HEADER_READ_LIMIT)
    content_type, width, height, _has_alpha = _parse_basic_image_header(data)
    return width, height, _HEADER_FORMATS[content_type]


def attach_image_details(
    payload: dict[str, Any], files: list[str], *, expected_ratio: str | None = None
) -> None:
    """Keep downloads deliverable even when optional metadata cannot be read.

    Pillow is optional here: without it, sizes come from file headers, files whose
    header cannot be read are listed without size/format, and one
    ``optional_metadata_unavailable`` notice is added. Nothing is resubmitted.
    """
    details: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    expected = re.fullmatch(r"([1-9]\d{0,4}):([1-9]\d{0,4})", expected_ratio or "")
    try:
        image_module: Any = importlib.import_module("PIL.Image")
    except (ImportError, OSError, AttributeError):
        image_module = None
        if files:
            notices.append({
                "code": "optional_metadata_unavailable",
                "dependency": "Pillow",
                "prepare_command": prepare_command(),
                "message": "未准备可选依赖 Pillow：图片尺寸和格式取自文件头（未应用 EXIF 方向），读不出的已省略。"
                "结果文件已保存，任务不会重新提交。",
            })
    for value in files:
        path = Path(value)
        try:
            size_bytes = path.stat().st_size
            if image_module is None:
                width, height, image_format = _header_metadata(path)
            else:
                width, height, image_format = _pillow_metadata(image_module, path)
            if width <= 0 or height <= 0:
                raise ValueError("invalid image dimensions")
        except AsxError:
            # Header parser could not recognize the file; only reachable without Pillow.
            details.append({"path": value, "size_bytes": size_bytes})
            continue
        except (OSError, EOFError, SyntaxError, ValueError, Warning):
            notices.append({
                "code": "image_metadata_unavailable", "path": value,
                "message": "无法读取结果图片的尺寸和格式，请检查该文件；任务记录和下载路径已保留。",
            })
            continue
        except Exception as exc:
            if image_module is None or not isinstance(exc, image_module.DecompressionBombError):
                raise
            notices.append({
                "code": "image_metadata_unavailable", "path": value,
                "message": "结果图片超过安全像素限制，未读取尺寸和格式；任务记录和下载路径已保留。",
            })
            continue
        divisor = math.gcd(width, height)
        actual_ratio = f"{width // divisor}:{height // divisor}"
        details.append({
            "path": value, "width": width, "height": height, "format": image_format,
            "aspect_ratio": actual_ratio, "size_bytes": size_bytes,
        })
        if expected:
            ratio_width, ratio_height = map(int, expected.groups())
            # Allow one-pixel rounding, but not a >1% ratio deviation on tiny images.
            difference = abs(width * ratio_height - height * ratio_width)
            if difference > max(ratio_width, ratio_height) or difference * 100 > height * ratio_width:
                notices.append({
                    "code": "aspect_ratio_mismatch", "path": value,
                    "requested_aspect_ratio": expected_ratio,
                    "actual_aspect_ratio": actual_ratio,
                    "message": f"结果图片为 {width}×{height}，与请求的 {expected_ratio} 比例不符。",
                })
    payload["images"] = details
    if notices:
        payload.setdefault("warnings", []).extend(notices)
