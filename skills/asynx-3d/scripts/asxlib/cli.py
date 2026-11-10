from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import Any, NoReturn

from .artifacts import recent_payload
from .batches import execute_batch
from .client import AsynxClient
from .config import config_status, configure, doctor, legacy_installation_notice, load_credentials
from .constants import DEFAULT_OUTPUT_DIR, KNOWN_STATUSES, VERSION
from .errors import AsxError
from .local_model3d import execute_local_model3d
from .model3d_tasks import build_3d_task
from .output import emit, log
from .state import connect_db as connect_ledger_db, task_paths as ledger_task_paths
from .tasks import (
    cancel_local_task,
    history_payload,
    local_asset_payload,
    local_task_status,
    local_tasks_payload,
    models_payload,
    poll_local_tasks,
    recover_tasks,
    refresh_task,
    run_task_submission,
    task_payload,
    wait_and_download,
)


def _client() -> AsynxClient:
    api_key, base_url = load_credentials()
    return AsynxClient(base_url, api_key)


def _add_local_model3d_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    m3d = commands.add_parser("model3d", help="本地确定性 3D 模型检查与后处理，不调用 Asynx")
    m3d_commands = m3d.add_subparsers(dest="model3d_command", required=True)

    info = m3d_commands.add_parser("info", help="查看 3D 模型元数据 (顶点、面数、包围盒、材质、动画剪辑)")
    info.add_argument("input")

    validate = m3d_commands.add_parser("validate", help="校验模型是否符合引擎规范")
    validate.add_argument("input")
    validate.add_argument("--max-faces", type=int, help="面数上限")
    validate.add_argument("--require-pbr", action="store_true", help="必须包含 PBR 材质")
    validate.add_argument("--max-materials", type=int, help="材质槽数量上限")
    validate.add_argument("--require-animations", action="store_true", help="必须包含动画剪辑")

    extract = m3d_commands.add_parser("extract-textures", help="从 GLB 模型中解包提取内嵌 PBR 贴图")
    extract.add_argument("input")
    extract.add_argument("--output-dir", required=True)


def parser() -> argparse.ArgumentParser:
    res = argparse.ArgumentParser(description="通过 Asynx 生成、绑定骨骼、动画与处理 3D 模型")
    res.add_argument("--version", action="version", version=VERSION)
    commands = res.add_subparsers(dest="command", required=True)

    _add_local_model3d_commands(commands)

    # Cloud generation
    gen = commands.add_parser("generate", help="生成 3D 模型 (文生 3D、图生 3D)")
    gen.add_argument("--prompt", help="文字描述")
    gen.add_argument("--negative-prompt", help="负向提示词")
    gen.add_argument("--image", help="单张或首张参考图")
    gen.add_argument("--reference", action="append", help="参考图路径，可重复")
    gen.add_argument("--from-task", help="使用之前任务的图片或 3D 产物")
    gen.add_argument("--model", help="指定模型")
    gen.add_argument("--pbr", action="store_true", default=True, help="生成 PBR 材质与贴图")
    gen.add_argument("--texture", action="store_true", default=True, help="生成彩色纹理")
    gen.add_argument("--face-limit", type=int, help="多边形面数上限")
    gen.add_argument("--output-format", choices=("glb", "fbx", "obj", "usdz"), default="glb")
    gen.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    gen.add_argument("--idempotency-key")
    gen.add_argument("--detach", action="store_true")

    # Rig check & Rig
    rc = commands.add_parser("rig-check", help="检查 3D 模型是否符合绑定骨骼要求")
    rc.add_argument("--model-file", help="本地 3D 模型文件")
    rc.add_argument("--from-task", help="前序 3D 生成任务 ID")
    rc.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    rc.add_argument("--idempotency-key")
    rc.add_argument("--detach", action="store_true")

    rig = commands.add_parser("rig", help="为 3D 模型自动绑定骨骼与蒙皮权重")
    rig.add_argument("--model-file", help="本地 3D 模型文件")
    rig.add_argument("--from-task", help="前序 3D 生成任务 ID")
    rig.add_argument("--rig-type", choices=("auto", "biped", "quadruped", "hexapod", "octopod", "serpentine", "aquatic"), default="auto")
    rig.add_argument("--output-format", choices=("glb", "fbx"), default="glb")
    rig.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    rig.add_argument("--idempotency-key")
    rig.add_argument("--detach", action="store_true")

    # Animate
    anim = commands.add_parser("animate", help="为已绑定骨骼的 3D 模型重定向预设动画")
    anim.add_argument("--model-file", help="本地骨骼模型文件")
    anim.add_argument("--from-task", help="前序绑定任务 ID")
    anim.add_argument("--animation", help="动画名称 (如 idle, walk, run, jump, slash)")
    anim.add_argument("--animations", nargs="+", help="批量应用多个动画")
    anim.add_argument("--animate-in-place", action="store_true", help="原地循环动画，去除位移")
    anim.add_argument("--bake-animation", action="store_true", default=True, help="烘焙骨骼变换关键帧")
    anim.add_argument("--output-format", choices=("glb", "fbx"), default="glb")
    anim.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    anim.add_argument("--idempotency-key")
    anim.add_argument("--detach", action="store_true")

    # Texture & Convert
    tex = commands.add_parser("texture", help="为 3D 模型重新生成贴图材质")
    tex.add_argument("--model-file", help="输入模型文件")
    tex.add_argument("--from-task", help="前序任务 ID")
    tex.add_argument("--prompt", required=True, help="材质风格描述")
    tex.add_argument("--output-format", choices=("glb", "fbx", "obj"), default="glb")
    tex.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    tex.add_argument("--idempotency-key")
    tex.add_argument("--detach", action="store_true")

    conv = commands.add_parser("convert", help="云端转换 3D 模型格式")
    conv.add_argument("--model-file", help="输入模型文件")
    conv.add_argument("--from-task", help="前序任务 ID")
    conv.add_argument("--output-format", choices=("glb", "fbx", "obj", "usdz"), required=True)
    conv.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    conv.add_argument("--idempotency-key")
    conv.add_argument("--detach", action="store_true")

    # UI workbenches
    ui = commands.add_parser("ui", help="启动本地 3D 交互工作台")
    ui_commands = ui.add_subparsers(dest="ui_command", required=True)
    ui_view = ui_commands.add_parser("viewer", help="交互式 3D 模型、骨骼动画与 PBR 贴图检查工作台")
    ui_view.add_argument("--model-file", help="模型文件路径")
    ui_view.add_argument("--from-task", help="前序任务 ID")
    ui_view.add_argument("--no-open", action="store_true")

    ui_render = ui_commands.add_parser("render-sequence", help="多视角 WebGL 渲染 2D 序列帧图集工作台")
    ui_render.add_argument("--model-file", help="模型文件路径")
    ui_render.add_argument("--from-task", help="前序任务 ID")
    ui_render.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    ui_render.add_argument("--no-open", action="store_true")

    # Common task lifecycle
    status = commands.add_parser("status", help="查询任务进度")
    status.add_argument("task_id")

    wait = commands.add_parser("wait", help="等待并下载任务产物")
    wait.add_argument("task_id")
    wait.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    history = commands.add_parser("history", help="查询远端历史任务")
    history.add_argument("--status", choices=tuple(sorted(KNOWN_STATUSES)))
    history.add_argument("--limit", type=int, default=20)

    recent = commands.add_parser("recent", help="查询本机最近下载的 3D 产物")
    recent.add_argument("--query")
    recent.add_argument("--limit", type=int, default=20)
    recent.add_argument("--latest", action="store_true")
    recent.add_argument("--all-modalities", action="store_true")

    task = commands.add_parser("task", help="管理本地 3D 任务")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    task_list = task_commands.add_parser("list", help="列出本地任务")
    task_list.add_argument("--status")
    task_list.add_argument("--limit", type=int, default=50)
    task_list.add_argument("--all-modalities", action="store_true")
    task_status = task_commands.add_parser("status", help="查看任务快照")
    task_status.add_argument("task_id")
    task_status.add_argument("--refresh", action="store_true")
    task_poll = task_commands.add_parser("poll", help="推进刷新本地任务")
    task_poll.add_argument("task_id", nargs="?")
    task_poll.add_argument("--limit", type=int, default=50)
    task_recover = task_commands.add_parser("recover", help="恢复中断任务")
    task_recover.add_argument("task_id", nargs="?")
    task_recover.add_argument("--limit", type=int, default=50)
    task_cancel = task_commands.add_parser("cancel", help="取消任务")
    task_cancel.add_argument("task_id")

    asset = commands.add_parser("asset", help="查看本地产物")
    asset_commands = asset.add_subparsers(dest="asset_command", required=True)
    asset_list = asset_commands.add_parser("list", help="列出任务输出模型与贴图")
    asset_list.add_argument("task_id")

    batch = commands.add_parser("batch", help="管理 3D 批次")
    batch_commands = batch.add_subparsers(dest="batch_command", required=True)
    batch_commands.add_parser("list", help="列出 3D 批次")
    b_status = batch_commands.add_parser("status", help="查看批次状态")
    b_status.add_argument("batch_id", nargs="?")
    b_status.add_argument("--refresh", action="store_true")
    b_status.add_argument("--items", action="store_true")
    b_wait = batch_commands.add_parser("wait", help="等待批次完成")
    b_wait.add_argument("batch_id", nargs="?")
    b_wait.add_argument("--interval", type=float, default=10)
    b_wait.add_argument("--max-wait", type=float, default=0)
    b_pause = batch_commands.add_parser("pause", help="暂停批次")
    b_pause.add_argument("batch_id", nargs="?")
    b_resume = batch_commands.add_parser("resume", help="恢复批次")
    b_resume.add_argument("batch_id", nargs="?")
    b_cancel = batch_commands.add_parser("cancel", help="取消批次")
    b_cancel.add_argument("batch_id", nargs="?")

    doctor_cmd = commands.add_parser("doctor", help="诊断 3D Skill 环境配置")
    doctor_cmd.add_argument("--verify", action="store_true")
    doctor_cmd.add_argument("--updates", action="store_true")

    configure_cmd = commands.add_parser("configure", help="配置 Asynx API Key")
    configure_cmd.add_argument("--base-url")

    config_cmd = commands.add_parser("config", help="查看配置状态")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("status")

    models = commands.add_parser("models", help="列出可用 3D 模型")
    models.add_argument("--operation", choices=("all", "generate", "rig", "animate", "texture", "convert"), default="all")

    update = commands.add_parser("update", help="检查与更新 Skill")
    update_sub = update.add_subparsers(dest="update_command", required=True)
    update_sub.add_parser("status")
    update_check_cmd = update_sub.add_parser("check")
    update_check_cmd.add_argument("--force", action="store_true")
    update_apply_cmd = update_sub.add_parser("apply")
    update_apply_cmd.add_argument("--target", default="all")
    update_apply_cmd.add_argument("--force", action="store_true")
    update_sub.add_parser("rollback")

    return res


def execute(args: argparse.Namespace, *, locked_root: Path | None = None) -> tuple[dict[str, Any], int]:
    if args.command == "model3d":
        return execute_local_model3d(args)

    if args.command == "ui":
        from .ui_server import launch_workbench
        ui_static = Path(__file__).resolve().parent / "ui"
        if not ui_static.is_dir():
            ui_static = Path(__file__).resolve().parents[2] / "src" / "ui" / "3d"

        model_file = getattr(args, "model_file", None)
        from_task = getattr(args, "from_task", None)
        if from_task and not model_file:
            conn = connect_ledger_db()
            try:
                paths = ledger_task_paths(conn, from_task)
                model_file = paths[0] if paths else None
            finally:
                conn.close()

        open_browser = not getattr(args, "no_open", False)
        if args.ui_command == "viewer":
            return launch_workbench(
                mode="model3d_viewer",
                source_path=model_file,
                static_dir=ui_static,
                client=_client(),
                open_browser=open_browser,
            )
        if args.ui_command == "render-sequence":
            return launch_workbench(
                mode="model3d_render_sequence",
                source_path=model_file,
                output_dir=args.output_dir,
                static_dir=ui_static,
                client=_client(),
                open_browser=open_browser,
            )

    if args.command == "configure":
        return configure(args.base_url), 0
    if args.command == "config" and args.config_command == "status":
        return config_status(), 0
    if args.command == "doctor":
        payload = doctor(verify=args.verify, modality="3d")
        if args.updates:
            from .updates import update_check
            payload["updates"] = update_check()
        return payload, 0 if payload["ok"] else 2

    if args.command == "update":
        from .updates import update_apply, update_check, update_rollback, update_status
        if args.update_command == "status":
            return update_status(), 0
        if args.update_command == "check":
            checked = update_check(force=args.force)
            return checked, 0 if checked["ok"] else 2
        if args.update_command == "apply":
            return update_apply(target=args.target, force=args.force, already_locked=locked_root), 0
        return update_rollback(already_locked=locked_root), 0

    if args.command == "recent":
        modality = None if getattr(args, "all_modalities", False) else "3d"
        return recent_payload(args.query, args.limit, latest=args.latest, modality=modality), 0

    if args.command == "task":
        modality = None if getattr(args, "all_modalities", False) else "3d"
        if args.task_command == "list":
            return local_tasks_payload(modality=modality, status=args.status, limit=args.limit), 0
        if args.task_command == "status":
            if args.refresh:
                result, code = refresh_task(_client(), args.task_id)
                return {"ok": result["ok"], "schema_version": 2, "task": result}, code
            return local_task_status(args.task_id), 0
        client = _client()
        if args.task_command == "poll":
            return poll_local_tasks(client, args.task_id, limit=args.limit, modality=modality), 0
        if args.task_command == "recover":
            result = recover_tasks(client, args.task_id, limit=args.limit, modality=modality)
            return result, 0 if result["ok"] else 4
        if args.task_command == "cancel":
            return cancel_local_task(client, args.task_id)

    if args.command == "asset" and args.asset_command == "list":
        return local_asset_payload(args.task_id), 0

    if args.command == "batch":
        setattr(args, "modality", "3d")
        client = _client() if getattr(args, "refresh", False) or args.batch_command in {"wait", "poll", "cancel"} else None
        return execute_batch(client, args)

    client = _client()
    if args.command == "models":
        return models_payload(client, args.operation, modality="3d"), 0

    if args.command == "status":
        task, request_id = client.task(args.task_id)
        return task_payload(task, request_id, modality="3d"), 0

    if args.command == "wait":
        return wait_and_download(client, args.task_id, args.output_dir)

    if args.command == "history":
        return history_payload(client, status=args.status, limit=args.limit), 0

    # 3D cloud commands: generate, rig-check, rig, animate, texture, convert
    cmd_map = {
        "generate": "3d.generate",
        "rig-check": "3d.rig_check",
        "rig": "3d.rig",
        "animate": "3d.retarget",
        "texture": "3d.texture",
        "convert": "3d.convert",
    }
    if args.command in cmd_map:
        task_type = cmd_map[args.command]
        model_file = getattr(args, "model_file", None)
        image_path = getattr(args, "image", None)
        from_task = getattr(args, "from_task", None)

        if from_task:
            conn = connect_ledger_db()
            try:
                paths = ledger_task_paths(conn, from_task)
                if paths:
                    first = paths[0]
                    if first.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        image_path = image_path or first
                    else:
                        model_file = model_file or first
            finally:
                conn.close()

        body, selected_model = build_3d_task(
            client,
            task_type=task_type,
            model_selector=getattr(args, "model", None),
            prompt=getattr(args, "prompt", None),
            negative_prompt=getattr(args, "negative_prompt", None),
            image=image_path,
            images=getattr(args, "reference", None),
            model_file=model_file,
            rig_type=getattr(args, "rig_type", None),
            animation=getattr(args, "animation", None),
            animations=getattr(args, "animations", None),
            animate_in_place=getattr(args, "animate_in_place", None),
            bake_animation=getattr(args, "bake_animation", None),
            pbr=getattr(args, "pbr", None),
            texture=getattr(args, "texture", None),
            face_limit=getattr(args, "face_limit", None),
            output_format=getattr(args, "output_format", None),
        )
        return run_task_submission(
            client,
            task_type=task_type,
            body=body,
            model=selected_model,
            output_dir=args.output_dir,
            idempotency_key=args.idempotency_key or f"asx-3d-{args.command}-{secrets.token_hex(8)}",
            detach=args.detach,
            modality="3d",
        )

    raise AssertionError("unreachable")


def main(*, locked_root: Path | None = None) -> NoReturn:
    args: argparse.Namespace | None = None
    try:
        args = parser().parse_args()
        payload, exit_code = execute(args, locked_root=locked_root)
    except AsxError as exc:
        payload, exit_code = exc.payload(), exc.exit_code
    except KeyboardInterrupt:
        payload, exit_code = {
            "ok": False,
            "error": {"code": "interrupted", "message": "Operation interrupted"},
        }, 130
    except Exception as exc:
        payload, exit_code = AsxError(f"操作失败：{exc}", code="unexpected_error", exit_code=2).payload(), 2

    legacy_notice = legacy_installation_notice()
    if legacy_notice:
        payload["legacy_installation_notice"] = legacy_notice

    emit(payload)
    raise SystemExit(exit_code)
