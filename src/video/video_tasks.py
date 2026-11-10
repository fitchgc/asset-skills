from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .client import AsynxClient
from .errors import AsxError

DEFAULT_VIDEO_MODEL = "kling-video-v1"


def _encode_media_file(path_str: str) -> str:
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        raise AsxError(f"媒体文件不存在：{path}", code="media_not_found")
    data = path.read_bytes()
    suffix = path.suffix.removeprefix(".").lower()
    mime = "image/png" if suffix == "png" else "image/jpeg" if suffix in ("jpg", "jpeg") else "video/mp4"
    return f"data:{mime};base64," + base64.b64encode(data).decode("utf-8")


def resolve_video_model(client: AsynxClient, selector: str | None, task_type: str) -> str:
    models, _ = client.models(modality="video")
    eligible = [m for m in models if task_type in m.get("task_types", [])]
    if selector:
        exact = [m for m in eligible if m.get("name", "").lower() == selector.lower()]
        if exact:
            return exact[0]["name"]
        substr = [m for m in eligible if selector.lower() in m.get("name", "").lower()]
        if substr:
            return substr[0]["name"]
        return selector

    if eligible:
        return eligible[0]["name"]
    return DEFAULT_VIDEO_MODEL


def build_video_task(
    client: AsynxClient,
    *,
    task_type: str,
    model_selector: str | None = None,
    prompt: str | None = None,
    image: str | None = None,
    end_image: str | None = None,
    video: str | None = None,
    reference: str | None = None,
    duration: float | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    fps: int | None = None,
    generate_audio: bool | None = None,
) -> tuple[dict[str, Any], str]:
    model = resolve_video_model(client, model_selector, task_type)
    input_data: dict[str, Any] = {}

    if not prompt and not image and not video:
        raise AsxError("视频任务至少需要 --prompt、--image 或 --video", code="missing_input")

    if prompt:
        input_data["prompt"] = prompt
    if duration is not None:
        input_data["duration"] = duration
    if aspect_ratio:
        input_data["aspect_ratio"] = aspect_ratio
    if resolution:
        input_data["resolution"] = resolution
    if fps:
        input_data["fps"] = fps
    if generate_audio is not None:
        input_data["generate_audio"] = generate_audio

    inputs: list[dict[str, Any]] = []
    if image:
        inputs.append({"role": "first_frame", "url": _encode_media_file(image)})
    if end_image:
        inputs.append({"role": "last_frame", "url": _encode_media_file(end_image)})
    if video:
        inputs.append({"role": "source_video", "url": _encode_media_file(video)})
    if reference:
        inputs.append({"role": "reference", "url": _encode_media_file(reference)})

    if inputs:
        input_data["inputs"] = inputs

    body = {
        "model": model,
        "input": input_data,
    }
    return body, model
