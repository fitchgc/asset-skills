from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .client import AsynxClient
from .errors import AsxError

DEFAULT_3D_MODEL = "tripo-3d-v2"


def _encode_file_as_data_url(path_str: str) -> str:
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        raise AsxError(f"文件不存在：{path}", code="file_not_found")
    data = path.read_bytes()
    suffix = path.suffix.removeprefix(".").lower()
    mime = "model/gltf-binary" if suffix == "glb" else f"image/{suffix}" if suffix in ("png", "jpg", "jpeg", "webp") else "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(data).decode("utf-8")


def resolve_3d_model(client: AsynxClient, selector: str | None, task_type: str) -> str:
    models, _ = client.models(modality="3d")
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
    return DEFAULT_3D_MODEL


def build_3d_task(
    client: AsynxClient,
    *,
    task_type: str,
    model_selector: str | None = None,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    image: str | None = None,
    images: list[str] | None = None,
    model_file: str | None = None,
    rig_type: str | None = None,
    animation: str | None = None,
    animations: list[str] | None = None,
    animate_in_place: bool | None = None,
    bake_animation: bool | None = None,
    pbr: bool | None = None,
    texture: bool | None = None,
    face_limit: int | None = None,
    output_format: str | None = None,
) -> tuple[dict[str, Any], str]:
    model = resolve_3d_model(client, model_selector, task_type)
    input_data: dict[str, Any] = {}

    if task_type == "3d.generate":
        all_images = []
        if image:
            all_images.append(image)
        if images:
            all_images.extend(images)

        if all_images:
            input_data["mode"] = "image_to_model"
            input_data["images"] = [_encode_file_as_data_url(p) for p in all_images]
        else:
            if not prompt:
                raise AsxError("3D 生成需要 --prompt 或 --image", code="missing_input")
            input_data["mode"] = "text_to_model"

        if prompt:
            input_data["prompt"] = prompt
        if negative_prompt:
            input_data["negative_prompt"] = negative_prompt
        if pbr is not None:
            input_data["pbr"] = pbr
        if texture is not None:
            input_data["texture"] = texture
        if face_limit is not None:
            input_data["face_limit"] = face_limit
        if output_format:
            input_data["output_format"] = output_format

    elif task_type == "3d.rig_check":
        if not model_file:
            raise AsxError("rig-check 需要 --model-file 或 --from-task", code="missing_model_file")
        input_data["model_file"] = _encode_file_as_data_url(model_file)

    elif task_type == "3d.rig":
        if not model_file:
            raise AsxError("rig 需要 --model-file 或 --from-task", code="missing_model_file")
        input_data["model_file"] = _encode_file_as_data_url(model_file)
        if rig_type:
            input_data["rig_type"] = rig_type
        if output_format:
            input_data["output_format"] = output_format

    elif task_type in {"3d.retarget", "3d.animate"}:
        task_type = "3d.retarget"
        if not model_file:
            raise AsxError("animate 需要 --model-file 或 --from-task", code="missing_model_file")
        input_data["model_file"] = _encode_file_as_data_url(model_file)
        if animation:
            input_data["animation"] = animation
        if animations:
            input_data["animations"] = animations
        if animate_in_place is not None:
            input_data["animate_in_place"] = animate_in_place
        if bake_animation is not None:
            input_data["bake_animation"] = bake_animation
        if output_format:
            input_data["output_format"] = output_format

    elif task_type == "3d.texture":
        if not model_file:
            raise AsxError("texture 需要 --model-file 或 --from-task", code="missing_model_file")
        input_data["model_file"] = _encode_file_as_data_url(model_file)
        if prompt:
            input_data["prompt"] = prompt
        if output_format:
            input_data["output_format"] = output_format

    elif task_type == "3d.convert":
        if not model_file:
            raise AsxError("convert 需要 --model-file 或 --from-task", code="missing_model_file")
        input_data["model_file"] = _encode_file_as_data_url(model_file)
        if output_format:
            input_data["output_format"] = output_format

    body = {
        "model": model,
        "input": input_data,
    }
    return body, model
