from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import AsynxClient
from .errors import AsxError

DEFAULT_3D_MODELS = {
    "model.generate": "tripo-h3.1",
    "model.rig_check": "tripo-rig-check",
    "model.rig": "tripo-rig",
    "model.retarget": "tripo-animate",
    "model.convert": "tripo-convert",
}

RIG_TYPES = ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic", "others")
SKELETON_PROFILES = ("tripo", "mixamo", "actorcore", "unity", "unreal", "vrm")
CONVERT_FORMATS = ("fbx", "gltf", "obj", "usdz", "stl")


MAX_ANIMATIONS_PER_TASK = 5  # Tripo 文档：每个 animate 任务最多 5 个动画
MODEL_UPLOAD_EXTENSIONS = (".glb", ".fbx", ".obj", ".stl")
MODEL_UPLOAD_MAX_BYTES = 150_000_000
# 动画只接受骨骼绑定任务，不接受 file_id。
FILE_SOURCE_TASK_TYPES = {"model.rig_check", "model.rig", "model.convert"}


def _managed_task_id_for_file(model_file: str, task_type: str | None = None) -> str | None:
    """Return the remote Task ID if the file is an unmodified Asynx deliverable.

    For animate (model.retarget) the source must be a rig task, so walk up the
    lineage from e.g. an already-animated file to its rigging ancestor.
    """
    from .state import compute_file_sha256, connect_db, get_asset_by_path, get_task

    path = Path(model_file).expanduser().resolve()
    conn = connect_db()
    try:
        asset = get_asset_by_path(conn, str(path))
        if asset is None:
            return None
        if asset.get("sha256") and asset["sha256"] != compute_file_sha256(path):
            return None
        seen: set[str] = set()
        while True:
            row = get_task(conn, str(asset["local_task_id"]))
            remote_id = row.get("remote_task_id") if row else None
            if not remote_id or row.get("modality") != "3d" or asset.get("mime", "") == "application/zip":
                return None
            if task_type != "model.retarget" or row.get("operation") == "rig":
                return str(remote_id)
            # Not a rig task: climb to the parent asset.
            if asset["artifact_id"] in seen:
                return None
            seen.add(asset["artifact_id"])
            edge = conn.execute(
                "SELECT parent_path FROM asset_lineage WHERE child_artifact_id = ? ORDER BY id LIMIT 1",
                (asset["artifact_id"],),
            ).fetchone()
            parent = get_asset_by_path(conn, edge["parent_path"]) if edge else None
            if parent is None:
                if row.get("operation") in ("retarget", "convert"):
                    return None
                raise AsxError(
                    "该模型不是骨骼绑定结果，请先执行 Auto Rig 再添加动画",
                    code="not_rigged_source",
                )
            asset = parent
    finally:
        conn.close()


def resolve_source_task_id(*, from_task: str | None) -> str:
    """Resolve a succeeded remote Task ID from a remote or local task identifier."""
    from .state import connect_db, get_task

    if not from_task:
        raise AsxError("需要 --from-task（前序成功的 3D 任务 ID）", code="missing_source_task")
    conn = connect_db()
    try:
        row = get_task(conn, from_task) or get_task(conn, remote_task_id=from_task)
        if row is None:
            if from_task.startswith("local_"):
                raise AsxError(f"本地任务不存在：{from_task}", code="task_not_found")
            return from_task
        remote_id = row.get("remote_task_id")
        if not remote_id:
            raise AsxError("来源任务尚未提交成功，无法作为来源", code="task_not_submitted")
        return str(remote_id)
    finally:
        conn.close()


def resolve_model_source(
    client: AsynxClient,
    task_type: str,
    *,
    from_task: str | None,
    model_file: str | None,
) -> dict[str, str]:
    """Build input.source: a task_id, or a file_id uploaded with purpose=model."""
    if from_task and model_file:
        raise AsxError("--from-task 与 --model-file 只能二选一", code="invalid_input")
    if from_task:
        return {"task_id": resolve_source_task_id(from_task=from_task)}
    if not model_file:
        raise AsxError("需要 --from-task 或 --model-file", code="missing_source")
    path = Path(model_file).expanduser().resolve()
    if not path.is_file():
        raise AsxError(f"文件不存在：{path}", code="file_not_found")
    managed = _managed_task_id_for_file(str(path), task_type)
    if managed:
        return {"task_id": managed}
    if task_type not in FILE_SOURCE_TASK_TYPES:
        raise AsxError(
            "动画不接受上传文件；请先对该模型执行 rig，再用 rig 任务 ID 作为 --from-task",
            code="file_source_not_supported",
        )
    if path.suffix.lower() == ".gltf":
        raise AsxError("不接受 .gltf，请先导出为单文件 GLB", code="unsupported_model_file")
    if path.suffix.lower() not in MODEL_UPLOAD_EXTENSIONS:
        raise AsxError("仅支持 GLB、FBX、OBJ、STL 模型文件", code="unsupported_model_file")
    if path.stat().st_size > MODEL_UPLOAD_MAX_BYTES:
        raise AsxError("模型文件超过 150 MB 上限", code="model_file_too_large")
    uploaded, _ = client.upload_file(path, purpose="model")
    return {"file_id": str(uploaded["id"])}


def _prepare_image(path_str: str) -> str:
    from .reference_media import prepare_image

    value = path_str.strip()
    if value.casefold().startswith(("http://", "https://", "file_")):
        return value
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise AsxError(f"文件不存在：{path}", code="file_not_found")
    return prepare_image(str(path)).source


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
    # 3D 模型没有 default_for，必须显式提交 model。
    if eligible:
        return eligible[0]["name"]
    return DEFAULT_3D_MODELS[task_type]

# Preset animations from https://developers.tripo3d.ai/en/docs/animations-retarget
ANIMATION_PRESETS: dict[str, tuple[str, ...]] = {
    'Biped (v3)': (
        'preset:climb',
        'preset:dive',
        'preset:fall',
        'preset:hurt',
        'preset:idle',
        'preset:jump',
        'preset:run',
        'preset:shoot',
        'preset:slash',
        'preset:turn',
        'preset:walk',
    ),
    'Biped (v1, legacy)': (
        'preset:biped:afraid',
        'preset:biped:agree',
        'preset:biped:angry_01',
        'preset:biped:angry_02',
        'preset:biped:angry_03',
        'preset:biped:basketball_shot',
        'preset:biped:bow',
        'preset:biped:box_01',
        'preset:biped:box_02',
        'preset:biped:box_03',
        'preset:biped:cast_a_spell',
        'preset:biped:cheer',
        'preset:biped:chop',
        'preset:biped:clap',
        'preset:biped:climb',
        'preset:biped:complain_01',
        'preset:biped:complain_02',
        'preset:biped:cross_body_crunch',
        'preset:biped:crossover_dribble',
        'preset:biped:cry',
        'preset:biped:dance_01',
        'preset:biped:dance_02',
        'preset:biped:dance_03',
        'preset:biped:dance_04',
        'preset:biped:dance_05',
        'preset:biped:dance_06',
        'preset:biped:defeat_02',
        'preset:biped:defeat_03',
        'preset:biped:depressed',
        'preset:biped:dig',
        'preset:biped:dive',
        'preset:biped:dribble',
        'preset:biped:fall',
        'preset:biped:fire',
        'preset:biped:flee_01',
        'preset:biped:flee_02',
        'preset:biped:flip',
        'preset:biped:fold_arms',
        'preset:biped:football_catch',
        'preset:biped:football_pass',
        'preset:biped:football_save',
        'preset:biped:freaky',
        'preset:biped:frightened',
        'preset:biped:front_kick_01',
        'preset:biped:front_kick_02',
        'preset:biped:frustrated_01',
        'preset:biped:frustrated_02',
        'preset:biped:golf',
        'preset:biped:greet_01',
        'preset:biped:greet_02',
        'preset:biped:greet_03',
        'preset:biped:greet_04',
        'preset:biped:heart_pose',
        'preset:biped:hit_to_body_01',
        'preset:biped:hit_to_body_02',
        'preset:biped:hit_to_head',
        'preset:biped:hit_to_side',
        'preset:biped:hit_to_stomach',
        'preset:biped:hug',
        'preset:biped:hurt',
        'preset:biped:idle',
        'preset:biped:jump',
        'preset:biped:jump_down',
        'preset:biped:jump_rope_01',
        'preset:biped:jump_rope_02',
        'preset:biped:laugh_01',
        'preset:biped:laugh_02',
        'preset:biped:lift_heavy',
        'preset:biped:look_around',
        'preset:biped:make_a_call_01',
        'preset:biped:make_a_call_02',
        'preset:biped:pitch_baseball',
        'preset:biped:play_mobile_game',
        'preset:biped:play_video_game',
        'preset:biped:press-up',
        'preset:biped:run',
        'preset:biped:run_upstairs',
        'preset:biped:scared_01',
        'preset:biped:scared_02',
        'preset:biped:scratch',
        'preset:biped:shoot',
        'preset:biped:shovel',
        'preset:biped:sing_01',
        'preset:biped:sing_02',
        'preset:biped:sing_03',
        'preset:biped:sing_04',
        'preset:biped:sit',
        'preset:biped:slash',
        'preset:biped:sob',
        'preset:biped:standing_relax',
        'preset:biped:surf',
        'preset:biped:swagger',
        'preset:biped:swim',
        'preset:biped:turn',
        'preset:biped:victory_celebration',
        'preset:biped:volleyball',
        'preset:biped:wait',
        'preset:biped:walk',
        'preset:biped:warm_up',
        'preset:biped:wave_goodbye_01',
        'preset:biped:wave_goodbye_02',
    ),
    'Aquatic': (
        'preset:aquatic:march',
    ),
    'Hexapod': (
        'preset:hexapod:walk',
    ),
    'Octopod': (
        'preset:octopod:walk',
    ),
    'Quadruped': (
        'preset:quadruped:walk',
    ),
    'Serpentine': (
        'preset:serpentine:march',
    ),
}


def normalize_animation(value: str) -> str:
    value = value.strip()
    return value if ":" in value else f"preset:{value}"


def build_3d_task(
    client: AsynxClient,
    *,
    task_type: str,
    model_selector: str | None = None,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    image: str | None = None,
    from_task: str | None = None,
    model_file: str | None = None,
    rig_type: str | None = None,
    skeleton_profile: str | None = None,
    animation: str | None = None,
    animations: list[str] | None = None,
    pbr: bool | None = None,
    texture: bool | None = None,
    face_limit: int | None = None,
    output_format: str | None = None,
) -> tuple[dict[str, Any], str]:
    if task_type == "model.animate":
        task_type = "model.retarget"
    if task_type not in DEFAULT_3D_MODELS:
        raise AsxError(f"不支持的 3D 操作：{task_type}", code="unsupported_task_type")
    model = resolve_3d_model(client, model_selector, task_type)
    input_data: dict[str, Any] = {}

    if task_type == "model.generate":
        if image:
            if prompt or negative_prompt:
                raise AsxError("图生 3D 不能同时填写 prompt / negative_prompt", code="invalid_input")
            input_data["image"] = _prepare_image(image)
        else:
            if not prompt:
                raise AsxError("3D 生成需要 --prompt 或 --image", code="missing_input")
            input_data["prompt"] = prompt
            if negative_prompt:
                input_data["negative_prompt"] = negative_prompt
        tex: dict[str, Any] = {}
        if texture is not None:
            tex["enabled"] = texture
        if pbr is not None:
            tex["pbr"] = pbr
        if tex:
            input_data["texture"] = tex
        if face_limit is not None:
            input_data["geometry"] = {"max_faces": face_limit}
        if output_format and output_format != "glb":
            raise AsxError("生成只支持 glb；需要其他格式请对结果执行 convert", code="unsupported_output_format")
    else:
        input_data["source"] = resolve_model_source(
            client, task_type, from_task=from_task, model_file=model_file
        )
        if task_type == "model.rig":
            rig: dict[str, Any] = {}
            if rig_type and rig_type != "auto":
                rig["type"] = rig_type
            if skeleton_profile:
                rig["skeleton_profile"] = skeleton_profile
            if rig:
                input_data["rig"] = rig
            if output_format:
                input_data["output"] = {"formats": [output_format]}
        elif task_type == "model.retarget":
            names = [a.strip() for a in (animations or []) if a and a.strip()]
            if animation and animation.strip():
                names.append(animation.strip())
            if not names:
                raise AsxError("animate 需要至少一个动画（如 walk 或 preset:walk）", code="missing_animation")
            if len(names) > MAX_ANIMATIONS_PER_TASK:
                raise AsxError(f"每个 animate 任务最多 {MAX_ANIMATIONS_PER_TASK} 个动画", code="too_many_animations")
            input_data["animations"] = list(dict.fromkeys(normalize_animation(a) for a in names))
            if output_format:
                input_data["output"] = {"formats": [output_format]}
        elif task_type == "model.convert":
            if not output_format:
                raise AsxError("convert 需要 --output-format", code="missing_output_format")
            fmt = "gltf" if output_format == "glb" else output_format
            input_data["output"] = {"formats": [fmt]}

    body = {
        "model": model,
        "input": input_data,
    }
    return body, model
