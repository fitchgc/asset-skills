from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .client import AsynxClient
from .errors import AsxError

DEFAULT_AUDIO_MODELS = {
    "audio.sfx": "audiocraft-sfx",
    "audio.music": "suno-v3",
    "audio.speech": "elevenlabs-v2",
}


def resolve_audio_model(client: AsynxClient, selector: str | None, task_type: str) -> str:
    models, _ = client.models(modality="audio")
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
    return DEFAULT_AUDIO_MODELS.get(task_type, "default-audio-model")


def build_audio_task(
    client: AsynxClient,
    *,
    task_type: str,
    model_selector: str | None = None,
    prompt: str | None = None,
    text: str | None = None,
    duration: float | None = None,
    lyrics: str | None = None,
    instrumental: bool | None = None,
    voice: str | None = None,
    reference_audio: str | None = None,
    speed: float | None = None,
    emotion: str | None = None,
    output_format: str | None = None,
) -> tuple[dict[str, Any], str]:
    model = resolve_audio_model(client, model_selector, task_type)
    input_data: dict[str, Any] = {}

    if task_type == "audio.sfx":
        if not prompt:
            raise AsxError("sfx 需要 --prompt 描述声音特效", code="missing_prompt")
        input_data["prompt"] = prompt
        if duration is not None:
            input_data["duration"] = duration
        if output_format:
            input_data["output_format"] = output_format

    elif task_type == "audio.music":
        if not prompt:
            raise AsxError("music 需要 --prompt 描述音乐风格或主题", code="missing_prompt")
        input_data["prompt"] = prompt
        if lyrics is not None:
            input_data["lyrics"] = lyrics
        if instrumental is not None:
            input_data["instrumental"] = instrumental
        if duration is not None:
            input_data["duration"] = duration
        if output_format:
            input_data["output_format"] = output_format

    elif task_type == "audio.speech":
        if not text:
            raise AsxError("speech 需要 --text 朗读文本", code="missing_text")
        input_data["text"] = text
        if voice:
            input_data["voice"] = voice
        if speed is not None:
            input_data["speed"] = speed
        if emotion:
            input_data["emotion"] = emotion
        if reference_audio:
            ref_path = Path(reference_audio).expanduser().resolve()
            if not ref_path.is_file():
                raise AsxError(f"参考音频文件不存在：{ref_path}", code="audio_not_found")
            data = ref_path.read_bytes()
            input_data["reference_audio"] = "data:audio/wav;base64," + base64.b64encode(data).decode("utf-8")
        if output_format:
            input_data["output_format"] = output_format

    else:
        raise AsxError(f"不支持的音频生成类型：{task_type}", code="unsupported_task_type")

    body = {
        "model": model,
        "input": input_data,
    }
    return body, model
